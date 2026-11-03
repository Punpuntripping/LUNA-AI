"""How a turn moves through the artifact_analyzer (plan artifact_analyzer.md §3).

The risks here are structural, so these run the real orchestrator functions with
the agents stubbed: a loop between the planner and the analyzer, state lost across
a pause (what got the old analyzer removed), and the flag-off path drifting from
today's behaviour.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from agents import orchestrator as orch
from agents.deep_search_v4 import orchestrator as ds_orch
from agents.deep_search_v4.planner.models import PlannerDecision
from agents.deep_search_v4.planner.prompts import build_decider_instructions
from agents.deep_search_v4.planner.runner import may_revert
from agents.memory.artifact_analyzer import models as analyzer_models
from agents.memory.artifact_analyzer.models import AnalyzerResult, VerifiedQuote
from agents.models import CheckPriorReports, ChatResponse, DispatchAgent, SpecialistResult
from agents.router.context import find_pending_follow_up, find_pending_handoff
from agents.router.router import SYSTEM_PROMPT, _validate_and_resolve_dispatch, router_agent
from agents.utils import prior_coverage
from agents.utils.history import messages_to_history, provenance_item_ids
from agents.utils.prior_coverage import (
    PRIOR_COVERAGE_LABEL,
    analyzer_enabled,
    render_prior_coverage,
)
from backend.app.services.message_service import _decoded_strings

HANDOFF = {
    "restatement": "مدين حُجز راتبه بتاريخ 2026/1/27",
    "covered_quotes": [{"text": "لا يُحجز إلا الثلث", "source_name": "نظام التنفيذ"}],
    "missing": ["مسار الاسترداد في النظام القديم"],
}


def _collect(gen: Any) -> list[dict]:
    async def run() -> list[dict]:
        return [ev async for ev in gen]

    return asyncio.run(run())


def _check(**over: Any) -> CheckPriorReports:
    return CheckPriorReports(**{
        "task_label": "حجز الراتب", "target_wi": "WI-2",
        "target_item_id": "wi-2", "attached_item_ids": ["wi-2"], **over,
    })


# ── Router output ─────────────────────────────────────────────────────────────


def test_flag_off_becomes_exactly_todays_dispatch() -> None:
    assert _check().to_dispatch() == DispatchAgent(
        agent_family="deep_search", task_label="حجز الراتب", target_wi="WI-2",
        target_item_id="wi-2", attached_item_ids=["wi-2"],
    )


def test_router_offers_the_output_and_states_when() -> None:
    assert "`CheckPriorReports`" in SYSTEM_PROMPT
    assert "accepts_search_offer" in SYSTEM_PROMPT
    assert CheckPriorReports in router_agent.output_type


def test_aliases_resolve_as_for_a_dispatch() -> None:
    ctx = SimpleNamespace(deps=SimpleNamespace(wi_alias_map={2: "wi-2", 4: "wi-4"}))
    value = CheckPriorReports(task_label="حجز", target_wi="WI-2", attached_wis=["WI-4"])
    out = _validate_and_resolve_dispatch(ctx, value)
    assert (out.target_item_id, out.attached_item_ids) == ("wi-2", ["wi-4"])


def test_a_follow_up_answer_still_counts_as_answered() -> None:
    pending = SimpleNamespace(item_ids=["wi-2"])
    assert orch._classify_follow_up(pending, _check()) == "answered"


@pytest.mark.parametrize(
    ("enabled", "users", "expected"),
    [(False, None, False), (True, None, True), (True, " u1 , u2", True), (True, "u2", False)],
)
def test_flag_and_allowlist(monkeypatch, enabled, users, expected) -> None:
    settings = SimpleNamespace(ARTIFACT_ANALYZER_ENABLED=enabled, ARTIFACT_ANALYZER_USERS=users)
    monkeypatch.setattr(prior_coverage, "get_settings", lambda: settings)
    assert analyzer_enabled("u1") is expected


def test_prior_report_needs_a_search_item() -> None:
    assert orch._has_prior_report(SimpleNamespace(workspace_item_summaries=[{"kind": "agent_search"}]))
    assert not orch._has_prior_report(SimpleNamespace(workspace_item_summaries=[{"kind": "note"}]))


# ── The analyzer branch ───────────────────────────────────────────────────────


@pytest.fixture
def flow(monkeypatch):
    """Stubs around ``_run_prior_check``; returns what each stub saw."""
    seen: dict[str, Any] = {"dispatch": [], "calls": []}

    async def fake_analyze(call: Any, _supabase: Any) -> AnalyzerResult:
        seen["calls"].append(call)
        return seen["result"]

    async def fake_reply(result: AnalyzerResult, **_: Any) -> SpecialistResult:
        return SpecialistResult(chat_summary="الجواب")

    async def fake_dispatch(**kwargs: Any):
        seen["dispatch"].append(kwargs)
        yield {"type": "token", "text": "بحث"}

    import agents.memory.artifact_analyzer as analyzer
    import agents.responder.prior_report as reply

    monkeypatch.setattr(analyzer, "analyze", fake_analyze)
    monkeypatch.setattr(reply, "write_prior_report_reply", fake_reply)
    monkeypatch.setattr(orch, "_dispatch", fake_dispatch)
    monkeypatch.setattr(orch, "_load_recent_messages", lambda *a, **k: [])
    monkeypatch.setattr("agents.utils.welcome.mark_welcomed", lambda *a, **k: None)
    return seen


def _prior_check(**over: Any) -> list[dict]:
    return _collect(orch._run_prior_check(**{
        "question": "لدين", "supabase": None, "user_id": "u", "conversation_id": "c",
        "case_id": None, "user_message_id": "m1", "assistant_message_id": "m2",
        "task_label": "حجز", "target_item_id": "wi-2", "attached_item_ids": ["wi-2"], **over,
    }))


def _verdict(verdict: str, **over: Any) -> AnalyzerResult:
    return AnalyzerResult(**{
        "verdict": verdict, "restatement": "س", "missing": ["ن"],
        "quotes": [VerifiedQuote(text="ق", source_name="م")], **over,
    })


def test_sufficient_replies_and_never_searches(flow) -> None:
    flow["result"] = _verdict("sufficient")
    events = _prior_check()
    assert flow["dispatch"] == []
    assert {"type": "token", "text": "الجواب"} in events
    assert events[-1]["type"] == "done"
    meta = next(e for e in events if e["type"] == "reply_metadata")["metadata"]
    assert meta == {"prior_report": True}


def test_partial_saves_what_is_missing_for_the_next_turn(flow) -> None:
    flow["result"] = _verdict("partial")
    events = _prior_check()
    assert flow["dispatch"] == []
    meta = next(e for e in events if e["type"] == "reply_metadata")["metadata"]
    assert meta["analyzer_handoff"]["missing"] == ["ن"]
    assert meta["analyzer_handoff"]["covered_quotes"][0]["text"] == "ق"


def test_insufficient_goes_straight_to_search_with_the_handoff(flow) -> None:
    flow["result"] = _verdict("insufficient")
    events = _prior_check()
    assert events[0] == {"type": "agent_selected", "agent_family": "deep_search"}
    sent = flow["dispatch"][0]
    assert sent["analyzer_checked"] is True
    assert sent["analyzer_handoff"]["missing"] == ["ن"]
    assert sent["task_label"] == "حجز"


def test_a_failed_analyzer_searches_as_today_without_a_handoff(flow) -> None:
    flow["result"] = AnalyzerResult(verdict="insufficient", degraded=True)
    _prior_check()
    sent = flow["dispatch"][0]
    assert sent["analyzer_handoff"] is None
    # Still marked checked: the planner must not bounce a failed turn back.
    assert sent["analyzer_checked"] is True


def test_analyzer_reads_six_rows_plus_the_current_message(flow, monkeypatch) -> None:
    flow["result"] = _verdict("sufficient")
    asked: list[int] = []
    monkeypatch.setattr(orch, "_load_recent_messages", lambda _s, _c, n, **k: asked.append(n) or [])
    _prior_check()
    assert asked == [7]


# ── Planner revert ────────────────────────────────────────────────────────────


def _decision(**over: Any) -> PlannerDecision:
    return PlannerDecision(**{"mode": "reg_compliance_led", "rationale": "س", **over})


def _deps(**over: Any) -> SimpleNamespace:
    return SimpleNamespace(**{"analyzer_checked": False, "prior_searches": ["wi"], **over})


def test_planner_reverts_when_the_analyzer_did_not_check() -> None:
    assert may_revert(_decision(already_covered=True), _deps())


@pytest.mark.parametrize(
    ("decision", "deps", "pinned"),
    [
        # The rule that makes a loop impossible.
        ({"already_covered": True}, {"analyzer_checked": True}, None),
        ({"already_covered": False}, {}, None),
        ({"already_covered": True}, {"prior_searches": []}, None),
        ({"already_covered": True}, {}, "editorial pin"),
    ],
)
def test_planner_must_search(decision, deps, pinned) -> None:
    assert not may_revert(_decision(**decision), _deps(**deps), pinned)


def test_decider_sees_the_handoff() -> None:
    from agents.deep_search_v4.planner.deps import PlannerDeps

    deps = PlannerDeps.__new__(PlannerDeps)
    for name, value in {
        "analyzer_handoff": HANDOFF, "case_brief": None, "recent_messages": [],
        "prior_searches": [], "attached_items": [], "compaction_summary_md": None,
        "conversation_memory_md": None,
    }.items():
        object.__setattr__(deps, name, value)
    text = build_decider_instructions(deps)
    assert "<prior_coverage>" in text
    assert "مسار الاسترداد في النظام القديم" in text


def _dispatch_stubs(monkeypatch, outcome: Any) -> dict:
    seen: dict[str, Any] = {"checks": [], "paused": [], "input": []}

    async def fake_run(input: Any, *a: Any, **k: Any) -> Any:
        seen["input"].append(input)
        return outcome

    async def fake_check(**kwargs: Any):
        seen["checks"].append(kwargs)
        yield {"type": "token", "text": "من التقرير"}

    async def no_gate(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(orch, "_run_deep_search", fake_run)
    monkeypatch.setattr(orch, "_run_prior_check", fake_check)
    monkeypatch.setattr(orch, "_check_deep_search_gate", no_gate)
    monkeypatch.setattr(orch, "_count_artifact_kinds", lambda *a: 0)
    monkeypatch.setattr(orch, "_load_attached_items", lambda *a: [])
    monkeypatch.setattr(orch, "_load_recent_messages", lambda *a, **k: [])
    monkeypatch.setattr(orch, "analyzer_enabled", lambda _u: True)
    monkeypatch.setattr(
        orch, "_record_deferred",
        lambda **kwargs: seen["paused"].append(kwargs) or ("سؤال", "r"),
    )
    monkeypatch.setattr(orch, "_find_awaiting_user", lambda *a: None)
    return seen


def _dispatch(**over: Any) -> list[dict]:
    return _collect(orch._dispatch(**{
        "agent_family": "deep_search", "describe_query": "لدين", "task_label": "حجز",
        "target_item_id": None, "attached_item_ids": [], "subtype": None, "supabase": None,
        "user_id": "u", "conversation_id": "c", "case_id": None, **over,
    }))


def test_revert_closes_the_run_before_the_reply(monkeypatch) -> None:
    seen = _dispatch_stubs(monkeypatch, orch._DeepSearchOutcome(kind="reverted"))
    events = _dispatch()
    kinds = [e["type"] for e in events]
    assert kinds.index("agent_run_finished") < kinds.index("token")
    assert seen["checks"][0]["after_revert"] is True
    assert "done" not in kinds[: kinds.index("agent_run_finished")]


def test_flag_off_marks_the_turn_checked_so_nothing_can_revert(monkeypatch) -> None:
    seen = _dispatch_stubs(monkeypatch, orch._DeepSearchOutcome(kind="reverted"))
    monkeypatch.setattr(orch, "analyzer_enabled", lambda _u: False)
    _dispatch()
    assert seen["input"][0].analyzer_checked is True


def test_handoff_reaches_the_search(monkeypatch) -> None:
    seen = _dispatch_stubs(monkeypatch, orch._DeepSearchOutcome(kind="reverted"))
    _dispatch(analyzer_handoff=HANDOFF, analyzer_checked=True)
    assert seen["input"][0].analyzer_handoff == HANDOFF
    assert seen["input"][0].analyzer_checked is True


def test_a_pause_keeps_the_handoff(monkeypatch) -> None:
    """The old analyzer's state died across a pause (2026-06-08). Not this one."""
    seen = _dispatch_stubs(monkeypatch, orch._DeepSearchOutcome(
        kind="paused", planner_result=object(), deferred=object(),
    ))
    _dispatch(analyzer_handoff=HANDOFF, analyzer_checked=True)
    assert seen["paused"][0]["analyzer"] == {"handoff": HANDOFF, "checked": True}


# ── The block deep search receives ────────────────────────────────────────────


def test_block_tells_the_search_what_is_settled_and_what_is_not() -> None:
    text = render_prior_coverage(HANDOFF)
    assert "take it as given" in text
    assert "«لا يُحجز إلا الثلث» (نظام التنفيذ)" in text
    assert "- مسار الاسترداد في النظام القديم" in text
    assert "2026/1/27" in text


def test_block_is_forwarded_without_the_planner_asking() -> None:
    deps = SimpleNamespace(
        analyzer_handoff=HANDOFF, conversation_memory_md=None, case_brief=None,
        prior_searches=[], _flushed_articles=[],
    )
    decision = SimpleNamespace(planner_brief="", context_labels=[])
    labels = [
        b.label
        for b in ds_orch._select_forwarded_blocks(decision, deps, include_planner_brief=True)
    ]
    assert labels == [PRIOR_COVERAGE_LABEL]


def test_no_handoff_means_no_block() -> None:
    deps = SimpleNamespace(
        analyzer_handoff=None, conversation_memory_md=None, case_brief=None,
        prior_searches=[], _flushed_articles=[],
    )
    decision = SimpleNamespace(planner_brief="", context_labels=[])
    assert ds_orch._select_forwarded_blocks(decision, deps, include_planner_brief=True) == []


# ── Between turns ─────────────────────────────────────────────────────────────


def _row(**over: Any) -> dict:
    return {"role": "assistant", "content": "رد", "metadata": {}, **over}


def test_the_offer_lives_on_the_last_reply_only() -> None:
    offer = _row(metadata={"analyzer_handoff": HANDOFF})
    assert find_pending_handoff([offer])["missing"] == HANDOFF["missing"]
    assert find_pending_handoff([offer, _row()]) is None
    assert find_pending_handoff([offer, {"role": "user", "content": "نعم"}]) is not None


def test_this_turns_empty_placeholder_is_skipped() -> None:
    offer = _row(metadata={"analyzer_handoff": HANDOFF})
    assert find_pending_handoff([offer, _row(content="")]) is not None


def test_handoff_is_masked_again_on_the_way_back(monkeypatch) -> None:
    import backend.app.services.masking_service as masking

    monkeypatch.setattr(masking, "encode_active", lambda t: f"ENC({t})" if t else t)
    out = find_pending_handoff([_row(metadata={"analyzer_handoff": HANDOFF})])
    assert out["restatement"].startswith("ENC(")
    assert out["covered_quotes"][0]["text"].startswith("ENC(")


def test_a_reply_from_prior_reports_counts_as_their_output() -> None:
    row = _row(metadata={"prior_report": True}, referenced_item_ids=["wi-2"], artifact_ids=[])
    assert provenance_item_ids(row) == ["wi-2"]


def test_an_ordinary_reference_chip_is_not_provenance() -> None:
    assert provenance_item_ids(_row(referenced_item_ids=["wi-2"], artifact_ids=[])) == []


def test_its_follow_up_question_can_be_answered() -> None:
    row = _row(
        metadata={"prior_report": True, "follow_up_question": "هل أُودع الراتب بعد الأمر؟"},
        referenced_item_ids=["wi-2"], artifact_ids=[], message_id="m",
    )
    assert find_pending_follow_up([row]).item_ids == ["wi-2"]


def test_history_tags_it_so_the_router_sees_where_it_came_from() -> None:
    row = _row(metadata={"prior_report": True}, referenced_item_ids=["wi-2"], artifact_ids=[])
    history = messages_to_history([row], {"wi-2": (2, "agent_search", "حجز الراتب")})
    assert "WI-2" in history[0].parts[0].content


def test_saved_metadata_is_decoded_all_the_way_down() -> None:
    codec = SimpleNamespace()
    import backend.app.services.message_service as service

    original = service.decode_text
    service.decode_text = lambda _c, t, emit=True: t.replace("FAKE", "REAL")
    try:
        out = _decoded_strings(codec, {"a": ["FAKE", {"b": "FAKE"}], "n": 1, "flag": True})
    finally:
        service.decode_text = original
    assert out == {"a": ["REAL", {"b": "REAL"}], "n": 1, "flag": True}


def test_chat_response_is_untouched_by_the_branch() -> None:
    assert not isinstance(ChatResponse(message="مرحبا"), CheckPriorReports)
    assert analyzer_models.CONVERSATION_ROWS == 6
