"""The message after a «بحث شامل» plan card (build spec C; plan §4.3 at the click,
§4.4, D31, D32; lead 11-24: a button of a dead card gets a short reply).

Unit level: ``answer_card_reply`` (button / typed label / typed other / expired /
stale / flag off / other card / no live card) and the outcome read-merge. Turn level:
whole turns through ``_handle_message_inner`` on a FakeDB — a click that runs the plan
(searches → merger → publish → chat summary), «بحث عادي» through today's dispatch,
an "insufficient" click, a typed reply that goes to the router, and an ordinary
``ask_user`` pause that never touches the wiring. Offline; ``asyncio.run``.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

import agents.orchestrator as orch
from agents.deep_search_v4.extended import wiring as W
from agents.deep_search_v4.extended.models import (
    ExtendedRequest,
    ExtendedSearchReport,
    MergedAnswer,
    StoredOption,
)
from agents.deep_search_v4.extended.tests import wiring_fakes as F
from agents.deep_search_v4.planner.logger import EVENT_RETRIEVAL_DONE
from agents.models import ChatMessageSnapshot
from shared import quota

PLAN_LABEL = "نفّذ الخطة"
#: Today's dispatch, kept before ``turn_env`` replaces it with a spy.
REAL_DISPATCH = orch._dispatch


@pytest.fixture(autouse=True)
def env(monkeypatch):
    """Flag on, an inactive deep-search gate with 50 points left (Pro), quiet loaders."""
    state: dict[str, Any] = {"flag": True}
    monkeypatch.setattr(W, "_flag_on", lambda _u: state["flag"])
    token = quota.set_deep_search_gate(SimpleNamespace(remaining=50.0, plan_id="pro", active=False))
    yield state
    quota.reset_deep_search_gate(token)


def _gate(remaining: float | None, plan_id: str = "pro") -> None:
    quota.set_deep_search_gate(SimpleNamespace(remaining=remaining, plan_id=plan_id, active=False))


def _answer(db: F.FakeDB, reply: str = PLAN_LABEL, plan_choice: Any = None) -> W.CardReply:
    pending = orch._find_awaiting_user(db, F.CONVO, F.USER)
    return W.answer_card_reply(
        db, pending, reply=reply, plan_choice=plan_choice, user_id=F.USER,
        conversation_id=F.CONVO, user_message_id="m-click", current_placeholder_id="m-placeholder",
    )


def _ready(content: str = PLAN_LABEL, **seed: Any) -> F.FakeDB:
    db = F.FakeDB()
    F.seed_card(db, **seed)
    F.add_click(db, content)
    return db


def _user_meta(db: F.FakeDB) -> dict:
    return db.row("messages", message_id="m-click")["metadata"]


# --------------------------------------------------------------------------- answer_card_reply


def test_a_button_claims_its_option():
    db = _ready()
    r = _answer(db, plan_choice={"run_id": F.RUN, "option": "cheaper"})
    assert (r.kind, r.option, r.via) == ("run", "cheaper", "button")
    assert r.pause is not None and r.pause.run_id == F.RUN and r.card_message_id
    assert db.row("paused_runs", run_id=F.RUN) is not None     # the run resolves it
    assert F.outcome(db) is None


@pytest.mark.parametrize("typed, option", [
    ("نفّذ الخطة", "plan"),
    ("  نفذ الخطة ", "plan"),
    ("نفـــذ  الخطّة", "plan"),
    ("نفّذ الأوفر", "cheaper"),
    ("بحث عادي", "ordinary"),
    ("بحثٌ عاديّ", "ordinary"),
])
def test_a_label_typed_exactly_runs_that_option(typed, option):
    r = _answer(_ready(typed), reply=typed)
    assert (r.kind, r.option, r.via) == ("run", option, "typed")


@pytest.mark.parametrize("typed", ["نفّذ الخطة لو سمحت", "نعم", "نفذ الخطه", ""])
def test_near_misses_are_typed_text(typed):
    assert _answer(_ready(typed or "."), reply=typed).kind == "route"


def test_anything_else_typed_goes_to_the_router_and_ends_the_card():
    db = _ready("خلّ البحث الثاني أحكام بس")
    r = _answer(db, reply="خلّ البحث الثاني أحكام بس")
    assert r.kind == "route" and r.pending_resolved
    assert db.row("paused_runs", run_id=F.RUN) is None
    card = F.card_row(db)["metadata"]["extended_plan"]
    assert card["outcome"] == "typed" and card["outcome_at"]
    assert card["run_id"] == F.RUN and card["options"]        # read-merged, not replaced
    assert _user_meta(db) == {}                               # a normal message


def test_an_expired_card_typed_reply_routes_as_expired():
    db = _ready("سؤال آخر", expires_in=timedelta(hours=-1))
    r = _answer(db, reply="سؤال آخر")
    assert r.kind == "route" and F.outcome(db) == "expired"
    assert db.row("paused_runs", run_id=F.RUN) is None


def test_an_expired_card_button_gets_the_short_reply():
    db = _ready(expires_in=timedelta(hours=-1))
    r = _answer(db, plan_choice={"run_id": F.RUN, "option": "plan"})
    assert r.kind == "expired" and F.outcome(db) == "expired"
    assert _user_meta(db) == {"kind": "agent_answer", "run_id": F.RUN, "plan_choice": "plan"}


def test_a_button_with_no_live_card_finds_its_card_by_run_id():
    db = _ready()
    db.tables["paused_runs"].clear()                           # resolved earlier
    r = _answer(db, plan_choice={"run_id": F.RUN, "option": "plan"})
    assert r.kind == "expired" and F.outcome(db) == "expired"


def test_a_stale_card_is_expired():
    db = F.FakeDB()
    F.seed_card(db)
    db.tables["messages"].append({"message_id": "m-later", "conversation_id": F.CONVO,
                                  "role": "assistant", "content": "رد لاحق", "metadata": {},
                                  "created_at": F._stamp()})
    F.add_click(db, "سؤال")
    r = _answer(db, reply="سؤال")
    assert r.kind == "route" and F.outcome(db) == "expired"


def test_flag_off_never_runs_a_pending_card(env):
    env["flag"] = False
    db = _ready()
    assert _answer(db).kind == "route"
    assert F.outcome(db) == "expired"
    db2 = _ready()
    assert _answer(db2, plan_choice={"run_id": F.RUN, "option": "plan"}).kind == "expired"


def test_a_button_of_another_card_runs_nothing():
    db = _ready()
    r = _answer(db, plan_choice={"run_id": "run-older", "option": "plan"})
    assert r.kind == "expired" and r.run_id == "run-older"
    assert db.row("paused_runs", run_id=F.RUN) is None and F.outcome(db) == "expired"


def test_an_option_the_card_never_offered_runs_nothing():
    db = F.FakeDB()
    F.seed_card(db)
    row = db.row("paused_runs", run_id=F.RUN)
    payload = row["deferred_payload"]["extended_plan"]
    payload["options"] = [o for o in payload["options"] if o["id"] != "cheaper"]
    F.add_click(db, "نفّذ الأوفر")
    r = _answer(db, plan_choice={"run_id": F.RUN, "option": "cheaper"})
    assert r.kind == "expired" and F.outcome(db) == "expired"


def test_the_first_outcome_wins():
    db = _ready()
    assert W.record_outcome(db, conversation_id=F.CONVO, run_id=F.RUN, outcome="plan")
    assert not W.record_outcome(db, conversation_id=F.CONVO, run_id=F.RUN, outcome="expired")
    assert F.outcome(db) == "plan"


def test_history_before_card_drops_the_card_and_the_click():
    rows = [SimpleNamespace(role=r) for r in ("user", "assistant", "user", "assistant", "user")]
    assert W.history_before_card(rows) == rows[:3]
    assert W.history_before_card(rows[:1]) == rows[:1]


# --------------------------------------------------------------------------- whole turns


@pytest.fixture
def turn_env(monkeypatch):
    """Everything around the pause check, quiet; the router and today's dispatch are spies."""
    seen: dict[str, Any] = {"routed": [], "dispatched": [], "resumed": []}

    async def ocr(*_a, **_k):
        return SimpleNamespace(filled_item_ids=[], failed=0)

    async def noop(*_a, **_k):
        return None

    async def route(**kw):
        seen["routed"].append(kw)
        yield {"type": "token", "text": "ROUTED"}
        yield {"type": "done", "usage": {}}

    async def dispatch(**kw):
        seen["dispatched"].append(kw)
        seen["outcome_before_start"] = F.outcome(kw["supabase"])
        yield {"type": "agent_run_started", "agent_family": "deep_search"}
        seen["outcome_after_start"] = F.outcome(kw["supabase"])
        yield {"type": "done", "usage": {}}

    async def resume(**kw):
        seen["resumed"].append(kw)
        yield {"type": "agent_resumed", "run_id": kw["pending"]["run_id"]}

    monkeypatch.setattr(orch, "_open_helper_turn", lambda **_k: None)
    monkeypatch.setattr(orch, "has_unread_attachments", lambda *_a: False)
    monkeypatch.setattr(orch, "run_ocr_extraction", ocr)
    monkeypatch.setattr(orch, "_ocr_notices", lambda *_a, **_k: [])
    monkeypatch.setattr(orch, "_start_memory_writer", lambda **_k: None)
    monkeypatch.setattr(orch.memory, "resummarize_dirty_items", noop)
    monkeypatch.setattr(orch.memory, "compact_conversation", noop)
    monkeypatch.setattr(orch, "_route", route)
    monkeypatch.setattr(orch, "_dispatch", dispatch)
    monkeypatch.setattr(orch, "_resume_major_agent", resume)
    monkeypatch.setattr(orch, "_load_compaction_summary", lambda *_a: None)
    monkeypatch.setattr(orch, "_load_conversation_memory", lambda *_a: None)
    monkeypatch.setattr(orch, "_load_prior_search_summaries", lambda *_a, **_k: [])
    monkeypatch.setattr(orch, "_load_attached_items", lambda *_a, **_k: [])
    monkeypatch.setattr(orch, "_count_artifact_kinds", lambda *_a: 0)
    monkeypatch.setattr(
        orch, "_load_recent_messages",
        lambda *_a, **_k: [ChatMessageSnapshot(role=r, content=r, created_at="")
                           for r in ("user", "assistant", "user")],
    )
    monkeypatch.setattr("backend.app.services.preferences_service.get_detail_level", lambda *_a: "medium")
    return seen


def _turn(db: F.FakeDB, content: str = PLAN_LABEL, plan_choice: Any = None) -> list[dict]:
    async def go() -> list[dict]:
        return [ev async for ev in orch._handle_message_inner(
            question=content, user_id=F.USER, conversation_id=F.CONVO, supabase=db,
            user_message_id="m-click", assistant_message_id="m-placeholder", plan_choice=plan_choice,
        )]

    return asyncio.run(go())


def _report(i: int, ok: bool = True) -> ExtendedSearchReport:
    return ExtendedSearchReport(
        index=i, request=F.plan().requests[(i - 1) % 2], ok=ok,
        agg_output=SimpleNamespace(synthesis_md=f"تقرير {i}", references=[], gaps=[], confidence="medium")
        if ok else None,
        error="" if ok else "aggregator_failed", sources=5,
    )


@pytest.fixture
def pipeline(monkeypatch):
    """B's searches / merger / publish / summary, scripted; records every call."""
    rec: dict[str, Any] = {"reports": [_report(1), _report(2)], "deps": []}

    async def searches(requests, *, plan, view, query, deps_factory, emit_sse, on_report=None):
        rec["searches"] = dict(requests=list(requests), plan=plan, view=view, query=query)
        rec["deps"] = [deps_factory(1), deps_factory(2)]
        emit_sse({"type": "agent_progress", "stage": "searching", "text": "بحث",
                  "data": {"sources": 4, "searches": {"total": 2, "done": 0}}})
        for report in rec["reports"]:           # like the runner: each as its search ends
            if report.ok and on_report is not None:
                await on_report(report)
        emit_sse({"type": "agent_progress", "stage": "aggregating", "text": "دمج",
                  "data": {"sources": 10, "searches": {"total": 2, "done": 2}}})
        return rec["reports"]

    async def post_report(report, **kw):
        rec.setdefault("posted", []).append((report.index, kw))
        report.item_id = f"wi-r{report.index}"
        return [{"type": "workspace_item_created", "item_id": report.item_id, "kind": "agent_search"}]

    async def merge(reports, **kw):
        rec["merge"] = kw
        if rec.get("merge_raises"):
            raise rec["merge_raises"]
        return MergedAnswer(synthesis_md="جواب مدمج", report_titles=["أ", "ب"])

    async def publish(reports, merged, **kw):
        rec["publish"] = dict(merged=merged, **kw)
        return SimpleNamespace(report_item_ids=["wi-r1", "wi-r2"],
                               merged_item_id="wi-m" if merged is not None else None,
                               sse_events=[{"type": "workspace_item_created", "item_id": "wi-m"}])

    async def summary(answer, *, query, deps, mode):
        rec["summary"] = dict(answer=answer, query=query, deps=deps, mode=mode)
        deps.emit_sse({"event": EVENT_RETRIEVAL_DONE, "confidence": "medium"})
        return SimpleNamespace(
            response=SimpleNamespace(chat_summary_md="الخلاصة للمحادثة", next_steps=[]),
            follow_up_question=None, question_basis=None,
        )

    monkeypatch.setattr(W, "_searches", searches)
    monkeypatch.setattr(W, "_merge", merge)
    monkeypatch.setattr(W, "_publish", publish)
    monkeypatch.setattr(W, "_post_report", post_report)
    monkeypatch.setattr(W, "_summary", summary)
    monkeypatch.setattr(W, "_load_angle_map", lambda _db, map_id: {"map": {"map_id": map_id}})
    monkeypatch.setattr(W, "_planner_view", lambda _db, m, **_k: SimpleNamespace(map_id=m["map"]["map_id"]))
    return rec


def test_a_plan_click_runs_searches_merger_publish_and_summary(turn_env, pipeline):
    db = _ready()
    events = _turn(db, plan_choice={"run_id": F.RUN, "option": "plan"})
    types = [e["type"] for e in events]

    assert types[0] == "agent_resumed" and types[-1] == "agent_run_finished"
    assert types[-2] == "done" and "token" in types and not turn_env["routed"]
    progress = [e for e in events if e["type"] == "agent_progress"]
    assert progress[0]["stage"] == "planning" and progress[0]["data"]["searches"] == {"total": 2, "done": 0}
    assert [p["stage"] for p in progress[1:]] == ["searching", "aggregating", "writing", "done"]
    assert progress[-1]["data"]["sources"] == 10 and progress[-1]["data"]["searches"] == {"total": 2, "done": 2}
    assert "".join(e["text"] for e in events if e["type"] == "token") == "الخلاصة للمحادثة"
    assert {"type": "workspace_item_created", "item_id": "wi-m"} in events

    assert len(pipeline["searches"]["requests"]) == 2 and pipeline["searches"]["query"] == F.ORIGINAL
    d1, d2 = pipeline["deps"]
    assert d1 is not d2 and d1.emit_sse is None
    assert len(pipeline["merge"]["recent_messages"]) == 1          # card + click dropped
    assert pipeline["publish"]["merged"] is not None and pipeline["publish"]["run_id"] == F.RUN
    assert pipeline["publish"]["message_id"] == "m-placeholder"
    assert pipeline["summary"]["deps"].emit_sse is not None and pipeline["summary"]["mode"] == "full"

    assert F.outcome(db) == "plan" and db.row("paused_runs", run_id=F.RUN) is None
    assert _user_meta(db) == {"kind": "agent_answer", "run_id": F.RUN, "plan_choice": "plan"}


def test_each_report_reaches_the_workspace_before_the_merge(turn_env, pipeline):
    """Owner 2026-11-27: a ready report is posted right away, not held for the merger —
    its item event goes out while the run is still at the searches."""
    events = _turn(_ready(), plan_choice={"run_id": F.RUN, "option": "plan"})
    at = {e.get("item_id"): i for i, e in enumerate(events) if e["type"] == "workspace_item_created"}
    first_aggregating = next(i for i, e in enumerate(events)
                             if e["type"] == "agent_progress" and e["stage"] == "aggregating")
    assert at["wi-r1"] < at["wi-r2"] < first_aggregating < at["wi-m"]
    assert [i for i, _ in pipeline["posted"]] == [1, 2]
    _, kw = pipeline["posted"][0]
    assert kw["message_id"] == "m-placeholder" and kw["query"] == F.ORIGINAL
    assert kw["conversation_id"] == F.CONVO and kw["user_id"] == F.USER


def test_cheaper_runs_the_cheaper_requests(turn_env, pipeline):
    db = _ready("نفّذ الأوفر")
    pipeline["reports"] = [_report(1)]
    _turn(db, "نفّذ الأوفر", plan_choice={"run_id": F.RUN, "option": "cheaper"})
    reqs = pipeline["searches"]["requests"]
    assert [r.mode for r in reqs] == ["full"]
    assert pipeline["searches"]["plan"].requests == reqs                # the plan that runs
    assert "merge" not in pipeline and pipeline["publish"]["merged"] is None   # one report
    assert pipeline["summary"]["answer"].synthesis_md == "تقرير 1"
    assert F.outcome(db) == "cheaper"


def test_a_failed_merger_still_publishes_the_reports(turn_env, pipeline):
    pipeline["merge_raises"] = RuntimeError("merger down")
    events = _turn(_ready(), plan_choice={"run_id": F.RUN, "option": "plan"})
    assert pipeline["publish"]["merged"] is None
    assert pipeline["summary"]["answer"].synthesis_md == "تقرير 1"
    assert events[-1]["type"] == "agent_run_finished"


def test_a_merge_past_its_deadline_is_dropped_and_the_reports_published(turn_env, pipeline, monkeypatch):
    """The merge may not eat the turn's pipeline budget: past its deadline the turn stops
    waiting (without awaiting the task) and publishes the reports alone."""
    import asyncio
    from time import perf_counter

    async def slow_merge(reports, **kw):
        pipeline["merge"] = kw
        await asyncio.sleep(30)

    monkeypatch.setattr(W, "_merge", slow_merge)
    monkeypatch.setattr(W, "merge_budget_s", lambda started: 0.3)
    monkeypatch.setattr(W, "MERGE_MIN_S", 0.0)
    t = perf_counter()
    events = _turn(_ready(), plan_choice={"run_id": F.RUN, "option": "plan"})
    assert perf_counter() - t < 10
    assert "merge" in pipeline and pipeline["publish"]["merged"] is None
    assert pipeline["summary"]["answer"].synthesis_md == "تقرير 1"
    assert events[-1]["type"] == "agent_run_finished"


def test_no_merge_without_time_left(turn_env, pipeline, monkeypatch):
    monkeypatch.setattr(W, "merge_budget_s", lambda started: 30.0)     # under MERGE_MIN_S
    _turn(_ready(), plan_choice={"run_id": F.RUN, "option": "plan"})
    assert "merge" not in pipeline and pipeline["publish"]["merged"] is None
    assert pipeline["summary"]["answer"].synthesis_md == "تقرير 1"


def test_the_merge_budget_leaves_the_reserve(monkeypatch):
    from time import perf_counter

    budget = W.merge_budget_s(perf_counter() - 400)
    assert 900 - 400 - W.MERGE_RESERVE_S - 2 < budget <= 900 - 400 - W.MERGE_RESERVE_S


def test_no_usable_report_says_so(turn_env, pipeline):
    pipeline["reports"] = [_report(1, ok=False), _report(2, ok=False)]
    events = _turn(_ready(), plan_choice={"run_id": F.RUN, "option": "plan"})
    assert [e["text"] for e in events if e["type"] == "token"] == [W.RUN_FAILED_REPLY_AR]
    assert "publish" not in pipeline


def test_a_typed_label_runs_like_the_button(turn_env, pipeline):
    db = _ready("نفذ الخطة")
    _turn(db, "نفذ الخطة")
    assert F.outcome(db) == "plan" and "searches" in pipeline
    assert _user_meta(db)["plan_choice"] == "plan"


def test_ordinary_runs_todays_dispatch_on_the_stored_message(turn_env, pipeline):
    db = _ready("بحث عادي")
    events = _turn(db, "بحث عادي", plan_choice={"run_id": F.RUN, "option": "ordinary"})
    (kw,) = turn_env["dispatched"]
    assert kw["agent_family"] == "deep_search" and kw["extended_allowed"] is False
    assert kw["describe_query"] == F.ORIGINAL and kw["attached_item_ids"] == ["wi-doc-1"]
    assert kw["task_label"] == "أمر التغيير" and kw["analyzer_checked"] is True
    assert kw["user_message_id"] == "m-click" and kw["assistant_message_id"] == "m-placeholder"
    assert events[0]["type"] == "agent_resumed" and "searches" not in pipeline
    assert F.outcome(db) == "ordinary" and db.row("paused_runs", run_id=F.RUN) is None
    # "ordinary" is recorded only once _dispatch has started the search.
    assert turn_env["outcome_before_start"] is None and turn_env["outcome_after_start"] == "ordinary"


def test_ordinary_refused_by_the_workspace_cap_is_expired(turn_env, pipeline, monkeypatch):
    """Today's _dispatch refuses before starting (cap): its own reply streams, the card
    ends "expired" — never "ordinary"."""
    monkeypatch.setattr(orch, "_dispatch", REAL_DISPATCH)
    monkeypatch.setattr(orch, "_count_artifact_kinds", lambda *_a: 15)
    db = _ready("بحث عادي")
    events = _turn(db, "بحث عادي", plan_choice={"run_id": F.RUN, "option": "ordinary"})

    assert [e["type"] for e in events] == ["agent_resumed", "token", "done"]
    assert events[1]["text"] == W.WORKSPACE_CAP_REPLY_AR
    assert events[2] == {"type": "done", "usage": orch._zero_usage("cap_rejected")}
    assert F.outcome(db) == "expired" and db.row("paused_runs", run_id=F.RUN) is None


def test_ordinary_refused_by_the_deep_search_gate_is_insufficient(turn_env, pipeline, monkeypatch):
    block = quota.DeepSearchGated(reason="low_points", plan_id="pro", resets_at=None, remaining=0.5)

    async def gated(*_a, **_k):
        return block

    monkeypatch.setattr(orch, "_dispatch", REAL_DISPATCH)
    monkeypatch.setattr(orch, "_check_deep_search_gate", gated)
    db = _ready("بحث عادي")
    events = _turn(db, "بحث عادي", plan_choice={"run_id": F.RUN, "option": "ordinary"})

    assert [e["type"] for e in events] == ["agent_resumed", "deep_search_gated", "token", "done"]
    assert events[2]["text"] == block.reply_ar
    assert F.outcome(db) == "insufficient" and db.row("paused_runs", run_id=F.RUN) is None


def test_ordinary_whose_dispatch_ends_without_starting_is_expired(turn_env, pipeline, monkeypatch):
    async def silent(**_kw):
        if False:
            yield {}

    monkeypatch.setattr(orch, "_dispatch", silent)
    db = _ready("بحث عادي")
    events = _turn(db, "بحث عادي", plan_choice={"run_id": F.RUN, "option": "ordinary"})
    assert [e["type"] for e in events] == ["agent_resumed"]
    assert F.outcome(db) == "expired" and db.row("paused_runs", run_id=F.RUN) is None


def test_ordinary_aborted_before_dispatch_decides_still_ends_the_card(turn_env, pipeline, monkeypatch):
    async def boom(**_kw):
        raise RuntimeError("dispatch blew up")
        yield {}  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(orch, "_dispatch", boom)
    db = _ready("بحث عادي")
    with pytest.raises(RuntimeError):
        _turn(db, "بحث عادي", plan_choice={"run_id": F.RUN, "option": "ordinary"})
    assert F.outcome(db) == "expired" and db.row("paused_runs", run_id=F.RUN) is None


@pytest.mark.parametrize("events, expected", [
    ([{"type": "token", "text": "x"}, {"type": "done", "usage": {"model": "cap_rejected"}}],
     ("expired", "workspace_cap")),
    ([{"type": "deep_search_gated"}, {"type": "token", "text": "x"},
      {"type": "done", "usage": {"model": "deep_search_gated"}}], ("insufficient", "deep_search_gate")),
    ([{"type": "done", "usage": {"model": "deep_search_gated"}}], ("insufficient", "deep_search_gate")),
    ([{"type": "done", "usage": {}}], ("expired", "dispatch_refused")),
    ([], ("expired", "dispatch_refused")),
])
def test_dispatch_refusal_reads_todays_refusals(events, expected):
    assert W.dispatch_refusal(events) == expected


# --------------------------------------------------------------------------- at the click: the mode still fits (§4.3, D23)


def _req(mode: str = "reg_compliance_led", cap: int | None = 8) -> ExtendedRequest:
    return ExtendedRequest(title="بحث", mode=mode, max_sub_queries=cap, focus="ف")


def _option(*requests: ExtendedRequest, option_id: str = "plan", estimate: float = 99.0) -> StoredOption:
    return StoredOption(id=option_id, requests=list(requests), estimate_points=estimate)


@pytest.mark.parametrize("requests, left, plan_id, fits, mode", [
    # Pro, 10 left → the 15 mode (3 searches · 8 per side · 2 full).
    ([_req(), _req("case_led")], 10.0, "pro", True, "15"),
    ([_req("full"), _req("full"), _req()], 10.0, "pro", True, "15"),          # at every limit
    ([_req("full"), _req("full"), _req("full")], 10.0, "pro", False, "15"),   # 3 full > 2
    ([_req(), _req(), _req(), _req()], 10.0, "pro", False, "15"),              # 4 searches > 3
    ([_req(cap=10), _req()], 10.0, "pro", False, "15"),                        # 10 > 8 per side
    ([_req(cap=None), _req()], 10.0, "pro", False, "15"),                      # uncapped ≠ a budget mode
    # Pro, 15 left → the 20 mode (3 full allowed).
    ([_req("full"), _req("full"), _req("full")], 15.0, "pro", True, "20"),
    # Fewer than 10 left, or a plan outside D24 → no mode at all.
    ([_req(), _req()], 9.9, "pro", False, None),
    ([_req(), _req()], 40.0, "basic", False, None),
    ([_req(), _req()], 40.0, None, False, None),                               # no gate
    # No budget (D33: over 50 left, or unlimited) holds anything.
    ([_req(cap=None), _req("full", cap=None), _req("full"), _req("full")], 60.0, "max", True, "none"),
    ([_req(cap=None), _req()], None, "max", True, "none"),
])
def test_option_fits_is_the_mode_not_the_estimate(requests, left, plan_id, fits, mode):
    ok, mode_now = W.option_fits(_option(*requests), points_left=left, plan_id=plan_id)
    assert ok is fits
    assert getattr(mode_now, "label", None) == mode


@pytest.mark.parametrize("left, fits", [(None, True), (0.0, True), (3.0, True), (-0.5, False)])
def test_ordinary_fits_while_any_points_are_left(left, fits):
    ok, mode_now = W.option_fits(_option(option_id="ordinary", estimate=5.4), points_left=left, plan_id="basic")
    assert (ok, mode_now) == (fits, None)


def _plan_option(db: F.FakeDB, *requests: ExtendedRequest, estimate: float) -> None:
    """Replace the stored ``plan`` option of the seeded card (what the click runs)."""
    payload = db.row("paused_runs", run_id=F.RUN)["deferred_payload"]["extended_plan"]
    for o in payload["options"]:
        if o["id"] == "plan":
            o["requests"] = [r.model_dump(mode="json") for r in requests]
            o["estimate_points"] = estimate


def test_insufficient_points_at_the_click_run_nothing(turn_env, pipeline):
    _gate(3.0)                     # under 10 left: no mode at all
    db = _ready()
    events = _turn(db, plan_choice={"run_id": F.RUN, "option": "plan"})
    assert [e["type"] for e in events] == ["token", "done"]
    assert events[0]["text"] == W.INSUFFICIENT_REPLY_AR
    assert "«بحث عادي»" in events[0]["text"] and "يحتاج" not in events[0]["text"]
    assert events[1] == {"type": "done", "usage": orch._zero_usage("extended_plan_insufficient")}
    assert "searches" not in pipeline and not turn_env["dispatched"]
    assert F.outcome(db) == "insufficient" and db.row("paused_runs", run_id=F.RUN) is None
    assert _user_meta(db)["plan_choice"] == "plan"


def test_a_plan_whose_mode_still_fits_runs_whatever_its_estimate(turn_env, pipeline):
    """D25: the estimate is display-only. 10 left on Pro → the 15 mode, which holds
    2 full + 1 searches at 8 — although the estimate (≈ 20) is over left + margin."""
    _gate(10.0)
    db = _ready()
    _plan_option(db, _req("full"), _req("full"), _req(), estimate=19.75)
    _turn(db, plan_choice={"run_id": F.RUN, "option": "plan"})
    assert F.outcome(db) == "plan" and len(pipeline["searches"]["requests"]) == 3


def test_a_plan_the_smaller_mode_cannot_hold_is_insufficient(turn_env, pipeline):
    """Planned in the 20 mode (3 full); 10 left now → the 15 mode allows 2 full —
    although the estimate (≈ 7) is well inside what is left."""
    _gate(10.0)
    db = _ready()
    _plan_option(db, _req("full", 4), _req("full", 4), _req("full", 4), estimate=7.0)
    events = _turn(db, plan_choice={"run_id": F.RUN, "option": "plan"})
    assert [e["text"] for e in events if e["type"] == "token"] == [W.INSUFFICIENT_REPLY_AR]
    assert F.outcome(db) == "insufficient" and "searches" not in pipeline


def test_ordinary_still_runs_under_the_extended_minimum(turn_env, pipeline):
    _gate(3.0)                     # too few for any mode — «بحث عادي» is today's search
    db = _ready("بحث عادي")
    _turn(db, "بحث عادي", plan_choice={"run_id": F.RUN, "option": "ordinary"})
    assert len(turn_env["dispatched"]) == 1 and F.outcome(db) == "ordinary"


def test_ordinary_with_no_points_left_is_insufficient(turn_env, pipeline):
    _gate(-1.0)
    db = _ready("بحث عادي")
    events = _turn(db, "بحث عادي", plan_choice={"run_id": F.RUN, "option": "ordinary"})
    assert [e["text"] for e in events if e["type"] == "token"] == [W.INSUFFICIENT_ORDINARY_REPLY_AR]
    assert not turn_env["dispatched"] and F.outcome(db) == "insufficient"


def test_unlimited_points_never_insufficient(turn_env, pipeline):
    _gate(None, "max")
    db = _ready()
    _turn(db, plan_choice={"run_id": F.RUN, "option": "plan"})
    assert F.outcome(db) == "plan"


@pytest.mark.parametrize("count, runs", [(12, True), (13, False), (15, False)])
def test_the_plan_needs_room_for_every_report_and_the_merged_answer(turn_env, pipeline, monkeypatch, count, runs):
    monkeypatch.setattr(orch, "_count_artifact_kinds", lambda *_a: count)   # 2 reports + 1 merged
    db = _ready()
    events = _turn(db, plan_choice={"run_id": F.RUN, "option": "plan"})
    assert ("searches" in pipeline) is runs
    if not runs:
        tokens = [e["text"] for e in events if e["type"] == "token"]
        assert tokens == [W.WORKSPACE_CAP_REPLY_AR if count >= 15 else
                          W.WORKSPACE_FULL_REPLY_AR.format(new=3, count=count, cap=15)]
        assert events[-1] == {"type": "done", "usage": orch._zero_usage("cap_rejected")}
        assert F.outcome(db) == "expired" and db.row("paused_runs", run_id=F.RUN) is None


def test_the_cap_reply_is_dispatchs_own_words():
    import inspect

    src = inspect.getsource(orch._dispatch)
    first, second = W.WORKSPACE_CAP_REPLY_AR.split(". ", 1)
    assert f'"{first}. "' in src and f'"{second}"' in src
    assert orch._WORKSPACE_CAP == 15


def test_publish_and_summary_run_inside_the_turns_codec_and_ledger_scope(turn_env, pipeline, monkeypatch):
    """Through handle_message: decode-before-store and billing depend on the turn's
    masking codec and the open llm_calls scope (bound to the card's run id)."""
    from agents.utils import usage_sink
    from backend.app.services.masking_service import active_codec
    from shared.privacy import PrivacyCodec

    codec = PrivacyCodec(real_to_fake={}, fake_to_real={}, enabled=False)
    monkeypatch.setattr("backend.app.services.masking_service.persist_new_mappings", lambda *_a, **_k: 0)
    seen: dict[str, Any] = {}
    real_publish, real_summary, real_post = W._publish, W._summary, W._post_report

    async def post_report(report, **kw):
        seen["post"] = (active_codec() is codec, usage_sink.in_scope(), usage_sink._run_id.get())
        return await real_post(report, **kw)

    async def publish(reports, merged, **kw):
        seen["publish"] = (active_codec() is codec, usage_sink.in_scope(), usage_sink._run_id.get())
        return await real_publish(reports, merged, **kw)

    async def summary(answer, **kw):
        seen["summary"] = (active_codec() is codec, usage_sink.in_scope(), usage_sink._run_id.get())
        usage_sink.record_call(agent="deep_search.planner", model="m", tokens_in=1, tokens_out=1, cost_usd=0.0)
        return await real_summary(answer, **kw)

    monkeypatch.setattr(W, "_publish", publish)
    monkeypatch.setattr(W, "_post_report", post_report)
    monkeypatch.setattr(W, "_summary", summary)
    db = _ready()

    async def go() -> list[dict]:
        return [ev async for ev in orch.handle_message(
            question=PLAN_LABEL, user_id=F.USER, conversation_id=F.CONVO, supabase=db,
            user_message_id="m-click", assistant_message_id="m-placeholder", codec=codec,
            deep_search_gate=SimpleNamespace(remaining=50.0, plan_id="pro", active=False),
            plan_choice={"run_id": F.RUN, "option": "plan"},
        )]

    events = asyncio.run(go())
    assert events[-1]["type"] == "agent_run_finished"
    assert seen["post"] == (True, True, F.RUN)        # a report posted as its search ends
    assert seen["publish"] == (True, True, F.RUN)
    assert seen["summary"] == (True, True, F.RUN)
    (row,) = db.tables["llm_calls"]
    assert row["run_id"] == F.RUN and row["message_id"] == "m-click"


def test_a_changed_map_refuses_the_plan(turn_env, pipeline, monkeypatch):
    monkeypatch.setattr(W, "_load_angle_map", lambda *_a: {})
    db = _ready()
    events = _turn(db, plan_choice={"run_id": F.RUN, "option": "plan"})
    assert [e["text"] for e in events if e["type"] == "token"] == [W.MAP_GONE_REPLY_AR]
    assert F.outcome(db) == "expired" and "searches" not in pipeline


def test_typed_text_goes_through_the_router(turn_env, pipeline):
    db = _ready("خلّ البحث الثاني أحكام بس")
    events = _turn(db, "خلّ البحث الثاني أحكام بس")
    (kw,) = turn_env["routed"]
    assert kw["question"] == "خلّ البحث الثاني أحكام بس"
    assert events[0] == {"type": "token", "text": "ROUTED"}
    assert F.outcome(db) == "typed" and not turn_env["resumed"]


def test_a_dead_button_gets_the_expired_reply_and_never_routes(turn_env, pipeline):
    db = _ready(expires_in=timedelta(hours=-2))
    events = _turn(db, plan_choice={"run_id": F.RUN, "option": "plan"})
    assert events == [{"type": "token", "text": W.EXPIRED_REPLY_AR},
                      {"type": "done", "usage": orch._zero_usage("extended_plan_expired")}]
    assert not turn_env["routed"] and "searches" not in pipeline


def test_an_ask_user_pause_never_touches_the_wiring(turn_env, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("an ordinary pause must not reach the card wiring")

    monkeypatch.setattr(W, "answer_card_reply", boom)
    db = F.FakeDB(paused_runs=[{
        "run_id": "run-ask", "conversation_id": F.CONVO, "user_id": F.USER,
        "agent_family": "deep_search", "pause_reason": "clarify", "asked_at": "2026-11-24T10:00:00+00:00",
    }])
    db.tables["messages"].append({"message_id": "q", "conversation_id": F.CONVO, "role": "assistant",
                                  "content": "سؤال؟", "metadata": {"kind": "agent_question",
                                                                  "run_id": "run-ask"},
                                  "created_at": F._stamp()})
    F.add_click(db, "جواب")
    _turn(db, "جواب")
    assert [kw["pending"]["run_id"] for kw in turn_env["resumed"]] == ["run-ask"]


def test_no_pause_and_no_button_is_a_plain_routed_turn(turn_env, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("no card, no button: the wiring stays out")

    monkeypatch.setattr(W, "answer_card_reply", boom)
    db = F.FakeDB()
    F.add_click(db, "سؤال")
    _turn(db, "سؤال")
    assert len(turn_env["routed"]) == 1
