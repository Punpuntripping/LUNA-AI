"""The ONE deep-search planner on V4.1 (.claude/plans/one_planner.md, owner 2026-11-27).

The decider and the «بحث شامل» planner in one agent: the offer (available / not, with
the user's words), the agent's outputs (a decision, a plan, a question) and its
validators, the runner's ``kind="extended"``, and the orchestrator call sites —
``_dispatch`` (the router's choice is a hint, «بحث عادي» stays one search),
``_run_deep_search`` (offer → card, or one search), the clarify pause keeping the
offer's inputs, and the ledger priced at the slot that ran. Offline: FakeDB, seams
replaced, FunctionModel. Sync tests with ``asyncio.run`` (CI has no pytest-asyncio).
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic_ai import DeferredToolRequests
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

import agents.orchestrator as orch
from agents.deep_search_v4.extended import offer as O
from agents.deep_search_v4.extended import wiring as W
from agents.deep_search_v4.extended.models import (
    PAUSE_REASON,
    AngleCard,
    BudgetMode,
    DocView,
    ExtendedSearchPlan,
    PlannerView,
)
from agents.deep_search_v4.extended.prompts import ONE_PLANNER_HINT_NO, ONE_PLANNER_HINT_YES
from agents.deep_search_v4.extended.router_view import UNAVAILABLE_AR
from agents.deep_search_v4.extended.tests import wiring_fakes as F
from agents.deep_search_v4.planner import agent as A
from agents.deep_search_v4.planner import runner as R
from agents.deep_search_v4.planner.deps import PlannerDeps
from agents.deep_search_v4.planner.models import PlannerDecision
from agents.models import MajorAgentInput, WorkspaceItemSnapshot

MODE_20 = BudgetMode(points=20, max_searches=3, max_per_side=8, max_full=3)


def _view() -> PlannerView:
    return PlannerView(
        map_id="map-1",
        message_id=None,
        documents=[DocView(id="D1", item_id="wi-doc-1", title="عقد المقاولة", doc_type="contract",
                           summary="", content_chars=100)],
        angles=[
            AngleCard(id="A1", kind="strengthen", strength="strong", for_side="claimant", for_party="المقاول",
                      title="أمر التغيير", argument="حجة", legal_question="هل يستحق؟", weakness="ضعف"),
            AngleCard(id="A2", kind="answer", strength="medium", for_side="claimant", for_party="المقاول",
                      title="المبلغ الثاني", argument="حجة", legal_question="هل يلزم أمر ثان؟", weakness="ضعف"),
        ],
        section_refs=frozenset({"D1.S3", "D1.S7"}),
    )


def _available(**over: Any) -> O.ExtendedOffer:
    base = dict(available=True, reason="eligible", item_ids=["wi-doc-1"], view=_view(), mode=MODE_20,
                points_left=18.0, plan_id="pro", max_searches=3, instructions="«بحث شامل» AVAILABLE")
    base.update(over)
    return O.ExtendedOffer(**base)


def _search_plan(n: int = 2, **over: Any) -> ExtendedSearchPlan:
    data = F.plan().model_dump()
    data["requests"] = data["requests"][:n]
    data.update(over)
    return ExtendedSearchPlan.model_validate(data)


# --------------------------------------------------------------------------- the offer


@pytest.fixture
def offer_seams(monkeypatch):
    calls: dict[str, Any] = {"elig": None, "count": (0, 15), "conversation": [], "view": 0}

    def eligibility(_db, *, user_id, attached_item_ids):
        calls["elig_ids"] = list(attached_item_ids)
        return calls["elig"] or SimpleNamespace(
            eligible=True, reason="eligible", mode=MODE_20, points_left=18.0, plan_id="pro",
            angle_map={"map": {"map_id": "map-1"}},
        )

    def view(_db, _angle_map, *, user_id):
        calls["view"] += 1
        return _view()

    monkeypatch.setattr(O, "_flag_on", lambda _u: True)
    monkeypatch.setattr(O, "_eligibility", eligibility)
    monkeypatch.setattr(O, "_workspace_count", lambda _db, _c: calls["count"])
    monkeypatch.setattr(O, "_planner_view", view)
    monkeypatch.setattr(O, "_encode", lambda text: text)
    monkeypatch.setattr(O, "conversation_attachment_ids", lambda _db, **_k: list(calls["conversation"]))
    return calls


def _prepare(**over: Any) -> O.ExtendedOffer | None:
    kw = dict(user_id=F.USER, conversation_id=F.CONVO, item_ids=["wi-doc-1"], hint=False,
              wi_alias_map={3: "wi-doc-1"})
    kw.update(over)
    return O.prepare_offer(F.FakeDB(), **kw)


def test_offer_is_none_when_extended_search_is_off_for_the_user(offer_seams, monkeypatch):
    monkeypatch.setattr(O, "_flag_on", lambda _u: False)
    assert _prepare() is None
    assert "elig_ids" not in offer_seams


def test_eligible_turn_offers_rules_data_room_and_the_wi_alias(offer_seams):
    offer = _prepare()
    assert offer.available and offer.mode == MODE_20 and offer.points_left == 18.0
    assert offer.max_searches == 14                      # 15 − 0 items − the merged answer
    text = offer.instructions
    assert "## «بحث شامل» — several searches at once (available this turn)" in text
    assert "<extended_search>" in text and "<budget>" in text
    assert '<document id="D1" wi="WI-3" doc_type="contract">' in text
    assert '<angle id="A1"' in text and '<angle id="A2"' in text
    assert ONE_PLANNER_HINT_NO in text and ONE_PLANNER_HINT_YES not in text
    assert offer.pause_payload() == {"hint": False, "item_ids": ["wi-doc-1"]}


def test_the_routers_choice_is_a_hint_line_only(offer_seams):
    yes = _prepare(hint=True)
    no = _prepare(hint=False)
    assert ONE_PLANNER_HINT_YES in yes.instructions and ONE_PLANNER_HINT_NO not in yes.instructions
    assert yes.instructions.replace(ONE_PLANNER_HINT_YES, "") == no.instructions.replace(ONE_PLANNER_HINT_NO, "")
    assert yes.pause_payload()["hint"] is True


def test_a_typed_change_to_the_card_reaches_the_one_planner_with_its_rule():
    """fix/extended-planner-card-edit (2026-11-27): a typed card edit now lands in the
    one planner, so its «بحث شامل» section carries the same rule."""
    from agents.deep_search_v4.extended.prompts import one_planner_available_section

    budget = one_planner_available_section(MODE_20, hint=True)
    none = one_planner_available_section(BudgetMode(points=None, max_searches=None, max_per_side=None,
                                                    max_full=None), hint=True)
    for text in (budget, none):
        assert "### When the user changes the plan card" in text
        assert "«بحث شامل — الخطة المقترحة»" in text
        assert "«الأنظمة» = `reg_compliance_led`, «الأحكام» = `case_led`, «الأنظمة والأحكام» = `full`" in text
        assert "the user's version is the plan" in text
        assert "A user's version left with ONE search is a `PlannerDecision`" in text
        assert "a user who asks for both sides on a search has given the reason" in text
        assert "without the sides (code adds them)" in text
    assert "**Beyond the budget**" in budget and "first merge two searches" in budget
    assert "Beyond the budget" not in none


def test_no_turn_files_falls_back_to_the_conversations_files(offer_seams):
    offer_seams["conversation"] = ["wi-old-1"]
    offer = _prepare(item_ids=[])
    assert offer.available and offer_seams["elig_ids"] == ["wi-old-1"]
    assert offer.item_ids == ["wi-old-1"]


def test_no_file_anywhere_is_unavailable_with_the_attach_words(offer_seams):
    offer = _prepare(item_ids=[])
    assert not offer.available and offer.reason == "no_attachments"
    assert offer.unavailable_ar == UNAVAILABLE_AR["no_attachments"]
    assert "## «بحث شامل» — not available this turn" in offer.instructions
    assert UNAVAILABLE_AR["no_attachments"] in offer.instructions
    assert "elig_ids" not in offer_seams


def test_no_thread_map_is_unavailable_and_the_planner_must_say_so(offer_seams):
    """Owner O2 (2026-11-27): no map → say so and offer «بحث عميق», never run it silently."""
    offer_seams["elig"] = SimpleNamespace(eligible=False, reason="no_fresh_map", mode=None, points_left=18.0,
                                          plan_id="pro", angle_map=None)
    offer = _prepare()
    assert not offer.available and offer.view is None and offer_seams["view"] == 0
    assert UNAVAILABLE_AR["no_fresh_map"] in offer.instructions
    assert "never run an ordinary search silently: call `ask_user`" in offer.instructions
    assert "offer to run «بحث عميق» now" in offer.instructions
    assert "This holds after an `ask_user` reply too." in offer.instructions


@pytest.mark.parametrize("count, available", [(0, True), (12, True), (13, False), (15, False)])
def test_no_offer_without_room_for_two_reports_and_the_merged_answer(offer_seams, count, available):
    offer_seams["count"] = (count, 15)
    offer = _prepare()
    assert offer.available is available
    if not available:
        assert offer.reason == O.REASON_WORKSPACE_FULL and O.WORKSPACE_FULL_AR in offer.instructions


def test_a_failing_offer_is_unavailable_never_raised(offer_seams, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(O, "_planner_view", boom)
    offer = _prepare()
    assert not offer.available and offer.reason.startswith("error:")


def test_resume_inputs_keep_off_the_paused_files_and_the_hint():
    assert O.resume_offer_inputs({"off": True}, reply_item_ids=["r1"]) is None
    assert O.resume_offer_inputs({"hint": True, "item_ids": ["a", "b"]}, reply_item_ids=["b", "r1"]) == (
        ["a", "b", "r1"], True,
    )
    # A pause from before the flag kept nothing: no ids (prepare_offer then reads the
    # conversation's files), no hint.
    assert O.resume_offer_inputs(None, reply_item_ids=[]) == ([], False)


# --------------------------------------------------------------------------- the plan check


def test_a_valid_plan_has_no_problems():
    assert O.offer_plan_problems(_search_plan(2), _available()) == []


def test_one_search_is_a_decision_not_a_plan():
    bad = O.offer_plan_problems(_search_plan(1), _available())
    assert any("PlannerDecision" in b for b in bad)


def test_the_workspace_room_caps_the_searches():
    bad = O.offer_plan_problems(_search_plan(2), _available(max_searches=1))
    assert any("room for at most 1 searches" in b for b in bad)


def test_unknown_angles_and_budget_limits_are_sent_back():
    plan = _search_plan(2)
    plan.requests[0].angles = ["A9"]
    plan.requests[1].max_sub_queries = 50
    bad = " ".join(O.offer_plan_problems(plan, _available()))
    assert "unknown angle ids ['A9']" in bad and "above the budget's 8" in bad


def test_decoding_keeps_the_request_quotes_as_matched():
    plan = _search_plan(2, request_quotes=["كم مدة فترة التجربة؟"])
    plan.requests[0].title = "مبلغ ٤٣٬٧٠٠"
    out = O.decode_offer_plan(plan)
    assert isinstance(out, ExtendedSearchPlan)
    assert out.request_quotes == ["كم مدة فترة التجربة؟"]
    assert out.requests[0].title == "مبلغ 43٬700"       # shown text in Latin digits


# --------------------------------------------------------------------------- the agent (FunctionModel)


def _deps(**over: Any) -> PlannerDeps:
    async def embed(_q: str) -> list[float]:
        return [0.0]

    deps = PlannerDeps(supabase=None, embedding_fn=embed, user_id=F.USER, conversation_id=F.CONVO)
    for k, v in over.items():
        setattr(deps, k, v)
    return deps


def _decision_args() -> dict:
    return {"mode": "reg_compliance_led", "support": False, "rationale": "سؤال نظامي"}


def _plan_args(n: int = 2) -> dict:
    return json.loads(_search_plan(n).model_dump_json())


class _Script:
    """A FunctionModel script: each call pops the next response maker; records what
    the model was offered."""

    __name__ = "script"

    def __init__(self, *steps: Any) -> None:
        self.steps = list(steps)
        self.infos: list[AgentInfo] = []

    def __call__(self, messages: Any, info: AgentInfo) -> ModelResponse:
        self.infos.append(info)
        return self.steps.pop(0)(info)


def _tool_named(info: AgentInfo, cls_name: str) -> str:
    names = [t.name for t in info.output_tools]
    (name,) = [n for n in names if n.endswith(cls_name)] or [names[0]]
    return name


def _call(cls_name: str, args: dict):
    return lambda info: ModelResponse(parts=[ToolCallPart(_tool_named(info, cls_name), args)])


@pytest.fixture
def model_slot(monkeypatch):
    seen: dict[str, Any] = {}

    def fake_get(slot: str, _override: Any = None) -> Any:
        seen["slot"] = slot
        return seen["model"]

    monkeypatch.setattr(A, "get_agent_model", fake_get)
    return seen


def _run(script: _Script, model_slot: dict, deps: PlannerDeps, **factory: Any) -> Any:
    model_slot["model"] = FunctionModel(script)
    agent = A.create_planner_decider(**factory)
    return asyncio.run(agent.run("<query>ابحث</query>", deps=deps)).output


def test_todays_decider_is_untouched_when_the_flag_is_off(model_slot):
    script = _Script(_call("PlannerDecision", _decision_args()))
    out = _run(script, model_slot, _deps())
    assert isinstance(out, PlannerDecision) and model_slot["slot"] == "planner_decider"
    info = script.infos[0]
    assert [t.name for t in info.output_tools] == ["final_result"]   # one output tool, as today
    assert info.allow_text_output is False
    assert "«بحث شامل»" not in (info.instructions or "")


def test_the_one_planner_runs_on_its_v41_slot_and_may_plan_extended(model_slot):
    offer = _available()
    script = _Script(_call("ExtendedSearchPlan", _plan_args(2)))
    out = _run(script, model_slot, _deps(one_planner=True, extended_offer=offer),
               one_planner=True, extended=offer)
    assert model_slot["slot"] == "deep_planner"
    assert isinstance(out, ExtendedSearchPlan) and len(out.requests) == 2
    info = script.infos[0]
    assert any(t.name.endswith("ExtendedSearchPlan") for t in info.output_tools)
    assert any(t.name.endswith("PlannerDecision") for t in info.output_tools)
    assert "«بحث شامل» AVAILABLE" in (info.instructions or "")
    assert info.model_settings and info.model_settings.get("max_tokens") == A.ONE_PLANNER_MAX_TOKENS


def test_an_unavailable_offer_is_said_but_never_an_output(model_slot):
    offer = O.ExtendedOffer(available=False, reason="no_fresh_map", instructions="«بحث شامل» NOT AVAILABLE")
    script = _Script(_call("PlannerDecision", _decision_args()))
    out = _run(script, model_slot, _deps(one_planner=True, extended_offer=offer), one_planner=True, extended=offer)
    assert isinstance(out, PlannerDecision)
    info = script.infos[0]
    assert not any(t.name.endswith("ExtendedSearchPlan") for t in info.output_tools)
    assert "«بحث شامل» NOT AVAILABLE" in (info.instructions or "")


def test_a_one_search_plan_is_sent_back_for_a_decision(model_slot):
    offer = _available()
    script = _Script(_call("ExtendedSearchPlan", _plan_args(1)), _call("PlannerDecision", _decision_args()))
    out = _run(script, model_slot, _deps(one_planner=True, extended_offer=offer), one_planner=True, extended=offer)
    assert isinstance(out, PlannerDecision) and len(script.infos) == 2


def test_a_one_search_plan_that_survives_the_retries_runs_as_one_search(model_slot):
    offer = _available()
    plan = _plan_args(1)
    script = _Script(*[_call("ExtendedSearchPlan", plan)] * (A._PLAN_RETRIES + 1))
    out = _run(script, model_slot, _deps(one_planner=True, extended_offer=offer), one_planner=True, extended=offer)
    assert isinstance(out, PlannerDecision) and out.mode == plan["requests"][0]["mode"]


def test_the_plan_card_is_the_long_message_review(model_slot):
    """C1: a long message may end in a card without an ask_user first; a decision
    still waits for the review."""
    offer = _available()
    script = _Script(_call("ExtendedSearchPlan", _plan_args(2)))
    out = _run(script, model_slot, _deps(one_planner=True, extended_offer=offer, review_required=True),
               one_planner=True, extended=offer)
    assert isinstance(out, ExtendedSearchPlan) and len(script.infos) == 1


def test_the_one_planner_can_still_ask(model_slot):
    offer = _available()
    script = _Script(lambda info: ModelResponse(parts=[ToolCallPart("ask_user", {"question": "هل أنت المدعي؟"})]))
    out = _run(script, model_slot, _deps(one_planner=True, extended_offer=offer), one_planner=True, extended=offer)
    assert isinstance(out, DeferredToolRequests)


def test_a_plan_written_as_text_is_salvaged(model_slot):
    offer = _available()
    text = "<thinking>..</thinking>" + json.dumps(_plan_args(2), ensure_ascii=False)
    script = _Script(lambda info: ModelResponse(parts=[TextPart(text)]))
    out = _run(script, model_slot, _deps(one_planner=True, extended_offer=offer), one_planner=True, extended=offer)
    assert isinstance(out, ExtendedSearchPlan)


# --------------------------------------------------------------------------- the runner


def test_the_runner_returns_the_plan_without_searching(monkeypatch):
    plan = _search_plan(2)
    billed: list[str] = []

    class _Decider:
        async def run(self, *_a, **_k):
            return SimpleNamespace(output=plan)

    made: dict[str, Any] = {}

    def factory(**kw):
        made.update(kw)
        return _Decider()

    async def no_search(*_a, **_k):
        raise AssertionError("a plan must not search")

    monkeypatch.setattr(R, "create_planner_decider", factory)
    monkeypatch.setattr(R, "_emit_planner_ledger", lambda *_r, slot="planner_decider": billed.append(slot))
    offer = _available()
    deps = _deps(one_planner=True, extended_offer=offer)
    turn = asyncio.run(R.handle_planner_turn("ابحث", deps, run_retrieval=no_search))
    assert turn.kind == "extended" and turn.extended_plan is plan and turn.planner_result is not None
    assert made["one_planner"] is True and made["extended"] is offer
    assert billed == ["deep_planner"]


def test_the_ledger_prices_each_slot_at_its_own_model(monkeypatch):
    rows: list[str] = []
    monkeypatch.setattr(R, "_emit_planner_ledger", lambda *_r, slot="planner_decider": rows.append(slot))
    R._emit_decider_and_responder(_deps(one_planner=True), object(), object())
    assert rows == ["deep_planner", "planner_responder"]
    rows.clear()
    R._emit_decider_and_responder(_deps(), object(), object())
    assert rows == ["planner_decider"]                  # today: one combined row


# --------------------------------------------------------------------------- the orchestrator


def test_a_clarify_pause_keeps_what_its_resume_needs():
    assert orch._offer_pause_payload(_deps(extended_offer=_available())) == {"hint": False, "item_ids": ["wi-doc-1"]}
    assert orch._offer_pause_payload(_deps()) == {"off": True}


@pytest.mark.parametrize("modes, expected", [
    (["case_led", "case_led"], "case_led"),
    (["case_led", "reg_compliance_led"], "full"),
])
def test_a_plan_without_a_card_runs_as_one_search(modes, expected):
    plan = _search_plan(2, request_quotes=["طلب سابق"])
    for req, mode in zip(plan.requests, modes):
        req.mode = mode
    decision = orch._single_search_of_plan(plan)
    assert isinstance(decision, PlannerDecision) and decision.mode == expected
    assert decision.request_quotes == ["طلب سابق"]


@pytest.mark.parametrize("extended", [None, {"hint": True, "item_ids": ["wi-doc-1"]}])
def test_record_deferred_stores_the_offer_inputs_only_when_given(monkeypatch, extended):
    seen: dict[str, Any] = {}

    def fake_record(_db, record, run_id=None):
        seen["payload"] = record.deferred_payload
        return "run-x"

    monkeypatch.setattr(orch, "record_pause", fake_record)
    call = SimpleNamespace(tool_call_id="t1", tool_name="ask_user",
                           args_as_dict=lambda: {"question": "سؤال؟"})
    orch._record_deferred(
        supabase=F.FakeDB(), planner_result=SimpleNamespace(all_messages_json=lambda: b"[]"),
        planner_output=SimpleNamespace(calls=[call]), user_id=F.USER, conversation_id=F.CONVO,
        case_id=None, user_message_id="m-u", agent_family="deep_search", describe_query="ابحث",
        extended=extended,
    )
    if extended is None:
        assert O.PAUSE_KEY not in seen["payload"]
    else:
        assert seen["payload"][O.PAUSE_KEY] == extended


@pytest.fixture
def dispatch_env(monkeypatch):
    async def no_gate(*_a, **_k):
        return None

    async def no_check(**_k):
        if False:
            yield {}

    seen: dict[str, Any] = {}

    async def fake_ds(_input, _db, **kw):
        seen.update(kw)
        return orch._DeepSearchOutcome(kind="reverted")

    monkeypatch.setattr(orch, "_count_artifact_kinds", lambda *_a: 0)
    monkeypatch.setattr(orch, "_check_deep_search_gate", no_gate)
    monkeypatch.setattr(orch, "analyzer_enabled", lambda _u: False)
    monkeypatch.setattr(orch, "_load_attached_items", lambda *_a, **_k: [])
    monkeypatch.setattr(orch, "_load_recent_messages", lambda *_a, **_k: [])
    monkeypatch.setattr(orch, "_run_deep_search", fake_ds)
    monkeypatch.setattr(orch, "_run_prior_check", no_check)
    monkeypatch.setattr(orch, "_one_planner_on", lambda _u: True)
    return seen


def _dispatch(**over: Any) -> list[dict]:
    kw = dict(
        agent_family="deep_search", describe_query=F.ORIGINAL, task_label="عنوان",
        target_item_id=None, attached_item_ids=["wi-doc-1"], subtype=None, supabase=F.FakeDB(),
        user_id=F.USER, conversation_id=F.CONVO, case_id=None, user_message_id="m-u",
        assistant_message_id="m-a",
    )
    kw.update(over)

    async def go() -> list[dict]:
        return [ev async for ev in orch._dispatch(**kw)]

    return asyncio.run(go())


@pytest.mark.parametrize("router_chose", [True, False])
def test_dispatch_offers_extended_every_time_and_the_router_only_hints(dispatch_env, router_chose):
    _dispatch(extended_allowed=router_chose)
    turn = dispatch_env["extended"]
    assert isinstance(turn, W.ExtendedTurn) and turn.hint is router_chose


def test_dispatch_ordinary_button_stays_one_search(dispatch_env):
    _dispatch(extended_allowed=False, extended_off=True)
    assert dispatch_env["extended"] is None


def test_dispatch_flag_off_keeps_todays_router_gate(dispatch_env, monkeypatch):
    monkeypatch.setattr(orch, "_one_planner_on", lambda _u: False)
    _dispatch(extended_allowed=False)
    assert dispatch_env["extended"] is None
    _dispatch(extended_allowed=True)
    assert isinstance(dispatch_env["extended"], W.ExtendedTurn) and dispatch_env["extended"].hint is False


@pytest.fixture
def one_planner_turn(monkeypatch):
    """``_run_deep_search`` with the one planner on: the planner turn and the offer
    replaced, recording what they got."""
    import agents.deep_search_v4.planner as planner_pkg

    seen: dict[str, Any] = {"turns": [], "results": []}

    def fake_deps(**kw):
        return SimpleNamespace(_turn_pins={}, _events=[], after_handback=False, recent_messages=[],
                               extended_offer=None, one_planner=True)

    async def fake_turn(query, deps, decision=None, pinned=None, **_k):
        seen["turns"].append(deps.extended_offer)
        return seen["results"].pop(0)

    def fake_offer(_db, **kw):
        seen["offer_kw"] = kw
        return _available(hint=kw["hint"])

    async def boom(*_a, **_k):
        raise AssertionError("the separate extended planner must not run")

    monkeypatch.setattr(planner_pkg, "build_planner_deps", fake_deps)
    monkeypatch.setattr(planner_pkg, "handle_planner_turn", fake_turn)
    monkeypatch.setattr("backend.app.services.preferences_service.get_detail_level", lambda *_a: "medium")
    monkeypatch.setattr(orch, "_load_prior_search_summaries", lambda *_a, **_k: [])
    monkeypatch.setattr("backend.app.services.masking_service.persist_new_mappings", lambda *_a, **_k: 0)
    monkeypatch.setattr(orch, "_one_planner_on", lambda _u: True)
    monkeypatch.setattr(O, "prepare_offer", fake_offer)
    monkeypatch.setattr(W, "plan_extended_turn", boom)
    return seen


def _major_input() -> MajorAgentInput:
    return MajorAgentInput(
        describe_query=F.ORIGINAL, task_label="عنوان",
        attached_items=[WorkspaceItemSnapshot(item_id="wi-doc-1", kind="attachment", title="عقد",
                                              content_md="نص", wi_seq=3)],
        recent_messages=[], user_id=F.USER, conversation_id=F.CONVO,
    )


def _run_ds(**kw: Any) -> Any:
    return asyncio.run(orch._run_deep_search(_major_input(), F.FakeDB(), **kw))


def test_a_plan_becomes_the_card(one_planner_turn, monkeypatch):
    plan = _search_plan(2)
    one_planner_turn["results"] = [SimpleNamespace(kind="extended", extended_plan=plan, planner_result=None)]
    got: dict[str, Any] = {}

    async def card(_db, **kw):
        got.update(kw)
        return W.PlannedTurn(question="CARD")

    monkeypatch.setattr(orch, "_one_planner_card", card)
    out = _run_ds(extended=W.ExtendedTurn(run_id="r", hint=True))
    assert out.kind == "extended_paused" and out.question == "CARD"
    assert one_planner_turn["offer_kw"]["hint"] is True
    assert one_planner_turn["offer_kw"]["item_ids"] == ["wi-doc-1"]
    assert got["raw_plan"] is plan and got["turn"].run_id == "r" and got["offer"].available


def test_a_plan_without_a_card_asks_the_planner_again_for_one_search(one_planner_turn, monkeypatch):
    one_planner_turn["results"] = [
        SimpleNamespace(kind="extended", extended_plan=_search_plan(2), planner_result=None),
        SimpleNamespace(kind="reverted"),
    ]

    async def no_card(*_a, **_k):
        return None

    monkeypatch.setattr(orch, "_one_planner_card", no_card)
    out = _run_ds(extended=W.ExtendedTurn(run_id="r"))
    assert out.kind == "reverted"
    first, second = one_planner_turn["turns"]
    assert first is not None and second is None           # the second run has no «بحث شامل»


def test_a_clarify_pause_carries_the_offer_inputs(one_planner_turn):
    one_planner_turn["results"] = [SimpleNamespace(kind="paused", planner_result="R", deferred="D")]
    out = _run_ds(extended=W.ExtendedTurn(run_id="r", hint=True))
    assert out.kind == "paused" and out.extended_payload == {"hint": True, "item_ids": ["wi-doc-1"]}


def test_without_an_extended_turn_there_is_no_offer(one_planner_turn):
    one_planner_turn["results"] = [SimpleNamespace(kind="paused", planner_result="R", deferred="D")]
    out = _run_ds()
    assert "offer_kw" not in one_planner_turn and one_planner_turn["turns"] == [None]
    assert out.extended_payload == {"off": True}


def test_the_card_records_the_pause_and_composes_the_request(monkeypatch):
    """The real card path: decode, compose the earlier turns' words + the message, the
    pause row + the card message, the offer's files."""
    monkeypatch.setattr(orch, "_count_artifact_kinds", lambda *_a: 0)
    db = F.FakeDB()
    earlier = SimpleNamespace(role="user", content="أريد دراسة العقد كاملاً من كل جوانبه")
    latest = SimpleNamespace(role="user", content="نعم")
    plan = _search_plan(2, request_quotes=["أريد دراسة العقد كاملاً من كل جوانبه"])
    planned = asyncio.run(orch._one_planner_card(
        db,
        input=F.turn_input(),
        turn=W.ExtendedTurn(run_id="run-1", user_message_id="m-u", task_label="عنوان"),
        offer=_available(),
        raw_plan=plan,
        planner_result=None,
        recent_messages=[earlier, latest],
        query="نعم",
    ))
    assert planned is not None and planned.question
    pause = db.row("paused_runs", run_id="run-1")
    assert pause["pause_reason"] == PAUSE_REASON
    stored = pause["deferred_payload"]["extended_plan"]
    assert stored["map_id"] == "map-1" and stored["attached_item_ids"] == ["wi-doc-1"]
    assert stored["describe_query"].endswith("نعم") and "أريد دراسة العقد كاملاً" in stored["describe_query"]
    assert len(stored["plan"]["requests"]) == 2
    card_msg = [m for m in db.tables["messages"] if (m.get("metadata") or {}).get("pause_reason") == PAUSE_REASON]
    assert len(card_msg) == 1 and card_msg[0]["metadata"]["run_id"] == "run-1"


def test_no_card_when_the_offer_is_not_available():
    out = asyncio.run(orch._one_planner_card(
        F.FakeDB(), input=F.turn_input(), turn=W.ExtendedTurn(run_id="r"),
        offer=O.ExtendedOffer(available=False, reason="no_fresh_map"), raw_plan=_search_plan(2),
        planner_result=None, recent_messages=[], query="ابحث",
    ))
    assert out is None
