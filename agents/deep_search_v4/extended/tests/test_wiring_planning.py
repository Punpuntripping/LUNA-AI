"""The planning turn of «بحث موسّع» (build spec C; plan §4.1, §4.4, D6, D30).

``plan_extended_turn`` (flag → eligibility → view → planner → one search or a card),
the card's records (pause row + card row), and the ``_run_deep_search`` / ``_dispatch``
call sites — including "flag off = today's path with no read" and the deps factory
building exactly what ``_run_deep_search`` builds. Offline: FakeDB, seams replaced, no
model. Sync tests with ``asyncio.run`` (CI has no pytest-asyncio).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

import agents.orchestrator as orch
from agents.deep_search_v4.extended import wiring as W
from agents.deep_search_v4.extended.models import (
    CARD_METADATA_KEY,
    PAUSE_REASON,
    BudgetMode,
    ExtendedPlanCard,
    ExtendedPlanPause,
)
from agents.deep_search_v4.extended.tests import wiring_fakes as F
from agents.models import MajorAgentInput, WorkspaceItemSnapshot

MODE_20 = BudgetMode(points=20, max_searches=3, max_per_side=8, max_full=3)


@pytest.fixture
def flag(monkeypatch):
    state = {"on": True}
    monkeypatch.setattr(W, "_flag_on", lambda _u: state["on"])
    return state


@pytest.fixture
def seams(monkeypatch):
    """A / B seams with recorders. ``seams["plan"]`` is the planner's ExtendedPlan."""
    calls: dict[str, Any] = {"eligibility": 0, "view": 0, "planner": 0, "plan": F.plan()}

    def eligibility(_db, **kw):
        calls["eligibility"] += 1
        calls["elig_kw"] = kw
        return calls.get("elig") or SimpleNamespace(
            eligible=True, reason="eligible", mode=MODE_20, points_left=18.0, plan_id="pro",
            angle_map={"map": {"map_id": "map-1"}},
        )

    def view(_db, angle_map, *, user_id):
        calls["view"] += 1
        return SimpleNamespace(map_id=angle_map["map"]["map_id"])

    async def planner(view, **kw):
        calls["planner"] += 1
        calls["planner_kw"] = kw
        if calls.get("planner_raises"):
            raise calls["planner_raises"]
        return SimpleNamespace(plan=calls["plan"], points=0.6, usage=None, model="m")

    monkeypatch.setattr(W, "_eligibility", eligibility)
    monkeypatch.setattr(W, "_planner_view", view)
    monkeypatch.setattr(W, "_plan", planner)
    monkeypatch.setattr(W, "_turn_gate", lambda: SimpleNamespace(remaining=18.0, plan_id="pro", active=False))
    return calls


def _one_request_plan():
    p = F.plan()
    p.requests = p.requests[:1]
    p.cheaper = None
    return p


def _plan_turn(db: F.FakeDB, **over: Any) -> Any:
    return asyncio.run(W.plan_extended_turn(
        F.turn_input(**over), db, turn=W.ExtendedTurn(run_id="run-new", user_message_id="m-u", task_label="عنوان"),
    ))


# --------------------------------------------------------------------------- gates before the planner


def test_flag_off_returns_none_before_any_read(flag, seams):
    flag["on"] = False
    db = F.FakeDB()
    assert _plan_turn(db) is None
    assert db.log == [] and seams["eligibility"] == 0


def test_no_attachments_never_checks_eligibility(flag, seams):
    db = F.FakeDB()
    assert _plan_turn(db, attached_items=[]) is None
    assert seams["eligibility"] == 0 and db.log == []


def test_not_eligible_runs_todays_planner(flag, seams):
    seams["elig"] = SimpleNamespace(eligible=False, reason="low_points", mode=None, points_left=4.0,
                                    plan_id="pro", angle_map=None)
    assert _plan_turn(F.FakeDB()) is None
    assert seams["planner"] == 0
    assert seams["elig_kw"]["attached_item_ids"] == ["wi-doc-1"]


def test_a_failed_planner_falls_back_silently(flag, seams):
    seams["planner_raises"] = RuntimeError("provider down")
    db = F.FakeDB()
    assert _plan_turn(db) is None
    assert db.tables["paused_runs"] == [] and db.tables["messages"] == []


# --------------------------------------------------------------------------- one search (D6)


def test_one_request_drops_the_plan_for_todays_planner(flag, seams, monkeypatch):
    """D6: a one-search plan runs exactly as today — no card, no ready-made decision;
    the drop is logged."""
    seams["plan"] = _one_request_plan()
    logged: list[tuple[str, dict]] = []
    real = W._logfire
    monkeypatch.setattr(W, "_logfire", SimpleNamespace(
        span=real.span, warning=real.warning, info=lambda name, **kw: logged.append((name, kw)),
    ))
    db = F.FakeDB()

    assert _plan_turn(db) is None
    assert seams["planner"] == 1
    assert db.tables["paused_runs"] == [] and db.tables["messages"] == []   # no card
    assert [name for name, _ in logged] == [W.SINGLE_SEARCH_EVENT]
    assert logged[0][1]["mode"] == "reg_compliance_led" and logged[0][1]["conversation_id"] == F.CONVO
    assert not hasattr(W, "single_request_decision")


# --------------------------------------------------------------------------- workspace room at card time


@pytest.mark.parametrize("count, card", [(0, True), (12, True), (13, False), (15, False)])
def test_no_card_without_room_for_every_report_and_the_merged_answer(flag, seams, monkeypatch, count, card):
    """Two searches → 2 reports + 1 merged answer: a card only when count + 3 ≤ 15 —
    the click-time pre-flight would refuse anything bigger."""
    seen: list[str] = []

    def counter(_db, conversation_id):
        seen.append(conversation_id)
        return count

    monkeypatch.setattr(orch, "_count_artifact_kinds", counter)
    db = F.FakeDB()
    planned = _plan_turn(db)

    assert seen and set(seen) == {F.CONVO}
    assert (planned is not None) is card
    assert (db.row("paused_runs", run_id="run-new") is not None) is card
    if not card:
        assert db.tables["messages"] == []


@pytest.mark.parametrize("count, planner_runs", [(12, 1), (13, 0), (15, 0)])
def test_no_planner_call_without_room_for_the_smallest_plan(flag, seams, monkeypatch, count, planner_runs):
    """The smallest extended plan adds 3 items (2 reports + the merged answer): with no room
    for that, the planner is never called — its cost would buy a card never shown."""
    monkeypatch.setattr(orch, "_count_artifact_kinds", lambda *_a: count)
    db = F.FakeDB()
    _plan_turn(db)
    assert seams["planner"] == planner_runs


# --------------------------------------------------------------------------- the card (≥ 2 searches)


def test_two_requests_record_the_card_and_pause(flag, seams):
    db = F.FakeDB()
    planned = _plan_turn(db)

    assert planned is not None and planned.question
    row = db.row("paused_runs", run_id="run-new")
    assert row["pause_reason"] == PAUSE_REASON and row["agent_family"] == "deep_search"
    assert row["task_label"] == "عنوان" and "message_history" not in row
    asked = datetime.fromisoformat(row["asked_at"])
    assert datetime.fromisoformat(row["expires_at"]) - asked == timedelta(hours=24)
    payload = row["deferred_payload"]
    pause = ExtendedPlanPause.model_validate(payload[CARD_METADATA_KEY])
    assert pause.run_id == "run-new" and pause.map_id == "map-1" and pause.budget_mode == "20"
    assert pause.describe_query == F.ORIGINAL and pause.attached_item_ids == ["wi-doc-1"]
    assert [o.id for o in pause.options] == ["plan", "cheaper", "ordinary"]
    assert payload[W.ANALYZER_KEY] == {"handoff": None, "checked": True}

    msg = db.row("messages", message_id=payload[W.CARD_MESSAGE_KEY])
    assert msg["role"] == "assistant" and msg["content"] == planned.question
    meta = msg["metadata"]
    assert meta["kind"] == "agent_question" and meta["run_id"] == "run-new"
    assert meta["pause_reason"] == PAUSE_REASON and meta["agent_family"] == "deep_search"
    card = ExtendedPlanCard.model_validate(meta[CARD_METADATA_KEY])
    assert card.run_id == "run-new" and card.points_left == 18 and card.outcome is None
    assert row["question_text"] == planned.question
    assert planned.question.startswith("بحث موسّع")


def test_a_failed_pause_insert_runs_todays_path(flag, seams):
    db = F.FakeDB()
    db.fail.add(("paused_runs", "insert"))
    assert _plan_turn(db) is None
    assert db.tables["messages"] == []


def test_a_failed_card_insert_frees_the_pause_slot(flag, seams):
    db = F.FakeDB()
    db.fail.add(("messages", "insert"))
    assert _plan_turn(db) is None
    assert db.tables["paused_runs"] == []   # an invisible card must not hold the slot


# --------------------------------------------------------------------------- _run_deep_search


@pytest.fixture
def planner_turn(monkeypatch):
    """Today's planner turn, replaced: records the decision it was given and reverts
    (the shortest exit)."""
    import agents.deep_search_v4.planner as planner_pkg

    seen: dict[str, Any] = {}

    def fake_deps(**kw):
        seen["deps_kw"] = kw
        return SimpleNamespace(_turn_pins={}, _events=[], **{"after_handback": False})

    async def fake_turn(query, deps, decision=None, pinned=None, **_k):
        seen["decision"] = decision
        seen["handback_allowed"] = deps.handback_allowed
        return SimpleNamespace(kind="reverted")

    monkeypatch.setattr(planner_pkg, "build_planner_deps", fake_deps)
    monkeypatch.setattr(planner_pkg, "handle_planner_turn", fake_turn)
    monkeypatch.setattr("backend.app.services.preferences_service.get_detail_level", lambda *_a: "medium")
    monkeypatch.setattr(orch, "_load_prior_search_summaries", lambda *_a, **_k: [])
    monkeypatch.setattr("backend.app.services.masking_service.persist_new_mappings", lambda *_a, **_k: 0)
    return seen


def _major_input(**over: Any) -> MajorAgentInput:
    return MajorAgentInput(
        describe_query=F.ORIGINAL, task_label="عنوان",
        attached_items=[WorkspaceItemSnapshot(item_id="wi-doc-1", kind="attachment", title="عقد",
                                              content_md="نص", wi_seq=3)],
        recent_messages=[], user_id=F.USER, conversation_id=F.CONVO, **over,
    )


def _run_ds(db: Any, major_input: MajorAgentInput | None = None, **kw: Any) -> Any:
    return asyncio.run(orch._run_deep_search(major_input or _major_input(), db, **kw))


def test_run_deep_search_without_a_turn_never_asks(monkeypatch, planner_turn):
    async def boom(*_a, **_k):
        raise AssertionError("the extended planner must not run")

    monkeypatch.setattr(W, "plan_extended_turn", boom)
    out = _run_ds(F.FakeDB())
    assert out.kind == "reverted" and planner_turn["decision"] is None


def test_run_deep_search_flag_off_is_todays_turn(monkeypatch, planner_turn, flag):
    flag["on"] = False
    out = _run_ds(F.FakeDB(), extended=W.ExtendedTurn(run_id="r"))
    assert out.kind == "reverted"
    assert planner_turn["decision"] is None


@pytest.mark.parametrize("allow_handback", [True, False])
def test_run_deep_search_one_search_runs_todays_phase_one(planner_turn, flag, seams, allow_handback):
    """D6: the extended planner's one search is dropped — today's planner turn gets no
    decision (its phase 1 runs, with every check) and keeps the router hand-back."""
    seams["plan"] = _one_request_plan()
    out = _run_ds(F.FakeDB(), _major_input(allow_handback=allow_handback),
                  extended=W.ExtendedTurn(run_id="r"))
    assert seams["planner"] == 1 and out.kind == "reverted"
    assert planner_turn["decision"] is None
    assert planner_turn["handback_allowed"] is allow_handback


def test_run_deep_search_card_returns_before_todays_planner(monkeypatch, planner_turn):
    async def planned(*_a, **_k):
        return W.PlannedTurn(question="CARD")

    monkeypatch.setattr(W, "plan_extended_turn", planned)
    out = _run_ds(F.FakeDB(), extended=W.ExtendedTurn(run_id="r"))
    assert out.kind == "extended_paused" and out.question == "CARD"
    assert "decision" not in planner_turn


def test_a_pinned_plan_never_goes_extended(monkeypatch, planner_turn):
    async def boom(*_a, **_k):
        raise AssertionError("the editorial pin must not reach the extended planner")

    monkeypatch.setattr(W, "plan_extended_turn", boom)
    from agents.deep_search_v4.planner.models import PinnedPlan

    _run_ds(F.FakeDB(), extended=W.ExtendedTurn(run_id="r"), pinned_plan=PinnedPlan())


# --------------------------------------------------------------------------- _dispatch


@pytest.fixture
def dispatch_env(monkeypatch):
    async def no_gate(*_a, **_k):
        return None

    monkeypatch.setattr(orch, "_count_artifact_kinds", lambda *_a: 0)
    monkeypatch.setattr(orch, "_check_deep_search_gate", no_gate)
    monkeypatch.setattr(orch, "analyzer_enabled", lambda _u: False)
    monkeypatch.setattr(orch, "_load_attached_items", lambda *_a, **_k: [])
    monkeypatch.setattr(orch, "_load_recent_messages", lambda *_a, **_k: [])


def _dispatch(db: Any, **over: Any) -> list[dict]:
    kw = dict(
        agent_family="deep_search", describe_query=F.ORIGINAL, task_label="عنوان",
        target_item_id=None, attached_item_ids=["wi-doc-1"], subtype=None, supabase=db,
        user_id=F.USER, conversation_id=F.CONVO, case_id=None, user_message_id="m-u",
        assistant_message_id="m-a",
    )
    kw.update(over)

    async def go() -> list[dict]:
        return [ev async for ev in orch._dispatch(**kw)]

    return asyncio.run(go())


def test_dispatch_turns_a_card_into_an_agent_question(monkeypatch, dispatch_env):
    seen: dict[str, Any] = {}

    async def fake_ds(_input, _db, **kw):
        seen.update(kw)
        return orch._DeepSearchOutcome(kind="extended_paused", question="CARD TEXT")

    monkeypatch.setattr(orch, "_run_deep_search", fake_ds)
    # The router chose «بحث موسّع» (D34): only then may the planning turn run.
    events = _dispatch(F.FakeDB(), extended_allowed=True)

    turn = seen["extended"]
    assert isinstance(turn, W.ExtendedTurn)
    assert turn.user_message_id == "m-u" and turn.task_label == "عنوان" and turn.run_id
    q = [e for e in events if e["type"] == "agent_question"]
    assert q == [{"type": "agent_question", "run_id": turn.run_id, "question": "CARD TEXT",
                  "pause_reason": PAUSE_REASON}]
    types = [e["type"] for e in events]
    assert types[-1] == "done" and "agent_run_finished" not in types   # the run stays alive


def test_dispatch_can_switch_the_extended_planner_off(monkeypatch, dispatch_env):
    seen: dict[str, Any] = {}

    async def fake_ds(_input, _db, **kw):
        seen.update(kw)
        return orch._DeepSearchOutcome(kind="reverted")

    async def no_check(**_k):
        if False:
            yield {}

    monkeypatch.setattr(orch, "_run_deep_search", fake_ds)
    monkeypatch.setattr(orch, "_run_prior_check", no_check)
    _dispatch(F.FakeDB(), extended_allowed=False)
    assert seen["extended"] is None


def test_dispatch_runs_todays_search_unless_the_router_chose_extended(monkeypatch, dispatch_env):
    """D34: ``extended_allowed`` defaults to False — the analyzer's "insufficient"
    and every other caller run today's deep search; only the router's
    ``DispatchAgent(extended=True)`` passes True."""
    seen: dict[str, Any] = {}

    async def fake_ds(_input, _db, **kw):
        seen.update(kw)
        return orch._DeepSearchOutcome(kind="reverted")

    async def no_check(**_k):
        if False:
            yield {}

    monkeypatch.setattr(orch, "_run_deep_search", fake_ds)
    monkeypatch.setattr(orch, "_run_prior_check", no_check)
    _dispatch(F.FakeDB())
    assert seen["extended"] is None


# --------------------------------------------------------------------------- the deps factory


def test_the_factory_builds_what_run_deep_search_builds(monkeypatch, planner_turn):
    """Drift guard: the extended searches' deps get the SAME build_planner_deps
    arguments as today's turn (bar the progress sink the runner sets itself)."""
    db = F.FakeDB()
    _run_ds(db)
    todays = dict(planner_turn["deps_kw"])

    import agents.deep_search_v4.planner as planner_pkg

    built: list[dict] = []

    def capture(**kw):
        built.append(kw)
        return SimpleNamespace()

    monkeypatch.setattr(planner_pkg, "build_planner_deps", capture)
    factory = W.make_deps_factory(_major_input(), db, http_client=todays["http_client"],
                                  detail_level="medium")
    d1, d2 = factory(1), factory(2)
    assert d1 is not d2 and d1.handback_allowed is False and d1.after_handback is False
    mine = built[0]

    assert set(mine) == set(todays)
    for key in todays:
        if key in ("emit_sse", "http_client"):
            continue
        if key == "attached_items":
            assert [s.item_id for s in mine[key]] == [s.item_id for s in todays[key]]
            continue
        assert mine[key] == todays[key], key
    assert mine["emit_sse"] is None
    # Each search owns its lists and snapshots (run_retrieval writes onto its deps).
    assert built[0]["attached_items"][0] is not built[1]["attached_items"][0]
    assert built[0]["recent_messages"] is not built[1]["recent_messages"]


# --------------------------------------------------------------------------- history marker


def test_the_card_reads_as_a_plan_in_the_history():
    from agents.utils.history import build_pause_question_tag

    tag = build_pause_question_tag({"metadata": {
        "kind": "agent_question", "agent_family": "deep_search", "pause_reason": PAUSE_REASON,
    }})
    assert tag == "〔[نظام] هذه خطة عرضها متخصصٌ (agent_family=deep_search) أثناء مهمته〕"
