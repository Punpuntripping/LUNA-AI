"""``publish_extended`` (reports through today's publisher + the merged item with
``children``, D14) and ``write_chat_summary`` (today's responder fed the merged answer).
Fakes for every DB write and model — nothing leaves the process."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import agents.agent_search as agent_search
from agents.agent_search.models import SearchPublishOutput
from agents.deep_search_v4.extended.models import MergedAnswer, Resolution
from agents.deep_search_v4.extended.publish import (
    PUBLISH_FAILED_AR,
    merged_agg_output,
    publish_extended,
    publish_report,
    write_chat_summary,
)
from agents.deep_search_v4.extended_merger import publisher as merged_publisher
from agents.deep_search_v4.extended_merger.models import (
    AngleCard,
    AngleStatus,
    MergedReportEntry,
    MergerOutput,
)
from agents.deep_search_v4.extended_merger.models import Resolution as MergerResolution
from agents.deep_search_v4.planner import agent as planner_agent
from agents.deep_search_v4.planner import runner as planner_runner
from agents.deep_search_v4.planner.models import PlannerResponse
from agents.responder import wiring as responder_wiring
from backend.app.services import references_service, workspace_service
from backend.app.services.masking_service import reset_active_codec, set_active_codec

from ._exec_fakes import FAKE_ID, REAL_ID, fixed_codec, make_agg, make_plan, make_ref, make_report, planner_deps


def _reports():
    r1 = make_report(1, make_agg([make_ref(1, "reg:a")], "أ [1]"), title="بحث الدين")
    r1.ura = SimpleNamespace(high_results=["u1"], medium_results=["u2"])
    r1.reg_rqrs, r1.per_executor_stats = ["rq1"], {"reg_search": {}}
    r2 = make_report(2, make_agg([make_ref(1, "case:9", source_type="case")], "ب [1]"), title="بحث الاختصاص")
    r2.ura = SimpleNamespace(high_results=["u3"], medium_results=[])
    r3 = make_report(3, make_agg([], ""), title="فشل", ok=False)
    return [r1, r2, r3]


def _merged() -> MergedAnswer:
    refs = [make_ref(1, "case:9", source_type="case"), make_ref(2, "reg:a")]
    out = MergerOutput(
        synthesis_md=f"## الخلاصة\nهويته {FAKE_ID} [1] و[2].",
        references=refs,
        ref_reports={1: [2], 2: [1]},
        confidence="medium",
        gaps=[f"فجوة {FAKE_ID}"],
        angle_status=[AngleStatus(angle="A1", status="supported", note=f"أساس {FAKE_ID}")],
        resolved=[MergerResolution(topic="الميعاد", reports=[1, 2], decision=f"قرار {FAKE_ID}", settled=True)],
        angles=[AngleCard(id="A1", kind="strengthen", strength="strong", title="ثبوت الدين", for_side="claimant")],
        reports=[
            MergedReportEntry(n=1, title="بحث الدين", mode="reg_compliance_led", angles=["A1"]),
            MergedReportEntry(n=2, title="بحث الاختصاص", mode="reg_compliance_led", angles=["A2"]),
            MergedReportEntry(n=3, title="فشل", mode="reg_compliance_led", angles=[], ok=False),
        ],
        prompt_version="merger-v1",
    )
    return MergedAnswer(
        synthesis_md=out.synthesis_md,
        references=[r.model_dump(mode="json") for r in refs],
        gaps=list(out.gaps),
        confidence="medium",
        resolved=[Resolution(topic="الميعاد", reports=[1, 2], decision=f"قرار {FAKE_ID}")],
        report_titles=["بحث الدين", "بحث الاختصاص"],
        merger=out.model_dump(mode="json"),
    )


@pytest.fixture
def codec():
    token = set_active_codec(fixed_codec())
    try:
        yield
    finally:
        reset_active_codec(token)


@pytest.fixture
def db(monkeypatch):
    calls: dict = {"reports": [], "created": [], "refs": []}
    state = {"fail_reports": set(), "fail_merged": False}

    async def _publish(inp, deps):
        if inp.task_label in state["fail_reports"]:
            raise RuntimeError("insert failed")
        calls["reports"].append(inp)
        item_id = f"wi-{len(calls['reports'])}"
        return SearchPublishOutput(item_id=item_id, sse_events=[{"type": "workspace_item_created", "item_id": item_id}])

    def _create(supabase, user_id, **kw):
        if state["fail_merged"]:
            raise RuntimeError("cap")
        calls["created"].append(kw)
        return {"item_id": "wi-merged", "title": kw["title"]}

    monkeypatch.setattr(agent_search, "publish_search_result", _publish)
    monkeypatch.setattr(workspace_service, "create_workspace_item", _create)
    monkeypatch.setattr(references_service, "persist_item_references",
                        lambda supabase, **kw: calls["refs"].append(kw) or 0)
    # the merged item's publisher binds both names at import
    monkeypatch.setattr(merged_publisher, "create_workspace_item", _create)
    monkeypatch.setattr(merged_publisher, "persist_item_references",
                        lambda supabase, **kw: calls["refs"].append(kw) or 0)
    calls["state"] = state
    return calls


def _publish_all(reports, merged, **kw):
    args = dict(supabase=object(), user_id="u1", conversation_id="c1", case_id=None, message_id="m1",
                query="ما موقفي؟", plan=make_plan(), detail_level="medium", task_label="تقييم الموقف",
                run_id="run-1")
    args.update(kw)
    return asyncio.run(publish_extended(reports, merged, **args))


# --------------------------------------------------------------------------- publish_extended


def test_reports_then_the_merged_item_with_children(db, codec):
    reports = _reports()
    out = _publish_all(reports, _merged())

    assert out.report_item_ids == ["wi-1", "wi-2"] and out.merged_item_id == "wi-merged"
    assert [r.item_id for r in reports] == ["wi-1", "wi-2", None]
    first = db["reports"][0]
    assert first.task_label == "بحث الدين" and first.describe_query == "ما موقفي؟"
    assert first.ura is reports[0].ura and first.reg_rqrs == ["rq1"] and first.message_id == "m1"

    created = db["created"][0]
    assert created["kind"] == "agent_search" and created["title"] == "تقييم الموقف"
    md = created["metadata"]
    assert md["subtype"] == "extended_synthesis" and md["confidence"] == "medium"
    assert md["children"] == ["wi-1", "wi-2"]
    assert md["run_id"] == "run-1" and md["client_side"] == "claimant"
    assert [(r["n"], r["item_id"], r["ok"]) for r in md["reports"]] == [
        (1, "wi-1", True), (2, "wi-2", True), (3, None, False)]
    assert md["resolved"] == [{"topic": "الميعاد", "reports": [1, 2], "decision": f"قرار {REAL_ID}", "settled": True}]
    assert md["angle_status"] == [{"angle": "A1", "status": "supported", "note": f"أساس {REAL_ID}"}]
    assert md["gaps"] == [f"فجوة {REAL_ID}"]
    # decoded before storing; the answer only (the reports are cards from metadata, D14)
    assert created["content_md"] == f"## الخلاصة\nهويته {REAL_ID} [1] و[2]."

    refs = db["refs"][0]
    assert refs["wi_id"] == "wi-merged" and refs["cited_numbers"] == [1, 2]
    assert [r.ref_id for r in refs["references"]] == ["case:9", "reg:a"]
    assert refs["ura_results"] == ["u1", "u2", "u3"]
    # Reports first, the merged answer LAST (the clients auto-open the newest item) and
    # marked primary (message_service puts it first in artifact_ids — the citation target).
    assert [e["item_id"] for e in out.sse_events] == ["wi-1", "wi-2", "wi-merged"]
    assert out.sse_events[-1]["type"] == "workspace_item_created" and out.sse_events[-1]["primary"] is True
    assert not any(e.get("primary") for e in out.sse_events[:-1])
    assert not any(e.get("type") == "status" for e in out.sse_events)


def test_one_usable_report_publishes_only_that_report(db):
    r1, r2, r3 = _reports()
    r2.ok = False
    out = _publish_all([r1, r2, r3], _merged())     # a merged answer with < 2 reports is not stored
    assert out.report_item_ids == ["wi-1"] and out.merged_item_id is None
    assert db["created"] == []


def test_no_merged_answer_publishes_the_reports(db):
    out = _publish_all(_reports(), None)
    assert out.report_item_ids == ["wi-1", "wi-2"] and out.merged_item_id is None


def test_a_failed_report_publish_never_stops_the_others(db):
    db["state"]["fail_reports"].add("بحث الدين")
    out = _publish_all(_reports(), _merged())
    assert out.report_item_ids == ["wi-1"] and out.merged_item_id == "wi-merged"
    assert db["created"][0]["metadata"]["children"] == ["wi-1"]
    assert [e.get("item_id") for e in out.sse_events] == ["wi-1", "wi-merged", None]
    assert out.sse_events[-1] == {"type": "status", "text": PUBLISH_FAILED_AR}


def test_a_failed_merged_publish_keeps_the_reports(db):
    db["state"]["fail_merged"] = True
    out = _publish_all(_reports(), _merged())
    assert out.report_item_ids == ["wi-1", "wi-2"] and out.merged_item_id is None
    assert out.sse_events[-1]["text"] == PUBLISH_FAILED_AR


def _post(report, **kw):
    args = dict(supabase=object(), user_id="u1", conversation_id="c1", case_id=None, message_id="m1",
                query="ما موقفي؟", detail_level="medium")
    args.update(kw)
    return asyncio.run(publish_report(report, **args))


def test_a_report_is_posted_on_its_own_when_its_search_ends(db):
    r1, _r2, r3 = _reports()
    assert _post(r1) == [{"type": "workspace_item_created", "item_id": "wi-1"}]
    assert r1.item_id == "wi-1" and db["reports"][0].task_label == "بحث الدين"
    assert db["reports"][0].message_id == "m1" and db["reports"][0].describe_query == "ما موقفي؟"
    assert _post(r1) == [] and len(db["reports"]) == 1          # already posted → nothing
    assert _post(r3) == [] and r3.item_id is None                # not usable → nothing


def test_the_end_posts_only_what_is_left_then_the_merged_answer(db):
    """Reports posted as their searches ended are not posted twice and their events
    are not sent again; they still count as the merged answer's children."""
    reports = _reports()
    _post(reports[1])                                            # report 2 finished first
    out = _publish_all(reports, _merged())
    assert [inp.task_label for inp in db["reports"]] == ["بحث الاختصاص", "بحث الدين"]
    assert out.report_item_ids == ["wi-2", "wi-1"]               # plan order: report 1, report 2
    assert [r.item_id for r in reports] == ["wi-2", "wi-1", None]
    assert [e["item_id"] for e in out.sse_events] == ["wi-2", "wi-merged"]   # wi-1's event already out
    assert db["created"][0]["metadata"]["children"] == ["wi-2", "wi-1"]


def test_an_early_post_that_failed_is_tried_again_at_the_end(db):
    reports = _reports()
    db["state"]["fail_reports"].add("بحث الدين")
    with pytest.raises(RuntimeError):
        _post(reports[0])
    db["state"]["fail_reports"].clear()
    out = _publish_all(reports, _merged())
    assert out.report_item_ids == ["wi-1", "wi-2"] and out.merged_item_id == "wi-merged"
    assert not any(e.get("type") == "status" for e in out.sse_events)


def test_merged_title_without_a_task_label(db, codec):
    _publish_all(_reports(), _merged(), task_label=None, query=f"هويته {FAKE_ID}")
    assert db["created"][0]["title"] == f"بحث شامل: هويته {REAL_ID}"


def test_merged_agg_output_drops_invalid_references():
    merged = _merged()
    merged.references.append({"n": 3, "bad": True})                  # no longer a Reference → dropped
    agg = merged_agg_output(merged)
    assert [r.n for r in agg.references] == [1, 2]
    assert agg.validation.cited_numbers == [1, 2] and agg.confidence == "medium"


# --------------------------------------------------------------------------- write_chat_summary


class _Responder:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.prompts: list[str] = []

    async def run(self, prompt, deps=None, usage_limits=None):
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("responder down")
        self.seen_agg = deps._agg_output
        return SimpleNamespace(output=PlannerResponse(chat_summary_md="ملخص الجواب الموحّد"))


def _deps_with_events():
    events: list[dict] = []
    deps = planner_deps([])
    deps.emit_sse = events.append
    return deps, events


def test_chat_summary_on_the_legacy_responder(monkeypatch):
    responder = _Responder()
    monkeypatch.setattr(responder_wiring, "responder_unified", lambda family: False)
    monkeypatch.setattr(planner_agent, "create_planner_responder", lambda model_override=None: responder)
    deps, events = _deps_with_events()
    turn = asyncio.run(write_chat_summary(_merged(), query="ما موقفي؟", deps=deps, mode="case_led"))

    assert turn.kind == "completed" and turn.response.chat_summary_md == "ملخص الجواب الموحّد"
    assert responder.seen_agg.synthesis_md == _merged().synthesis_md
    assert deps._decision.mode == "case_led" and deps._decision.support is False
    assert [e["event"] for e in events] == ["planner_retrieval_done", "planner_responded"]
    assert "ما موقفي؟" in responder.prompts[0] and turn.retrieval_query == "ما موقفي؟"


def test_chat_summary_falls_back_to_the_answer(monkeypatch):
    monkeypatch.setattr(responder_wiring, "responder_unified", lambda family: False)
    monkeypatch.setattr(planner_agent, "create_planner_responder",
                        lambda model_override=None: _Responder(fail=True))
    deps, events = _deps_with_events()
    turn = asyncio.run(write_chat_summary(_merged(), query="q", deps=deps))
    assert turn.degraded is True and turn.response.chat_summary_md.startswith("## الخلاصة")
    assert events[-1]["event"] == "planner_error"
    assert deps._decision.mode == "full"


def test_chat_summary_on_the_unified_responder(monkeypatch):
    seen: dict = {}

    async def _phase3(query, deps, agg, decision, *, decider_result, t2):
        seen.update(query=query, agg=agg, decision=decision, decider_result=decider_result)
        return planner_runner.PlannerTurnResult(kind="completed", agg_output=agg, decision=decision)

    monkeypatch.setattr(responder_wiring, "responder_unified", lambda family: True)
    monkeypatch.setattr(planner_runner, "_phase3_unified", _phase3)
    deps, _ = _deps_with_events()
    turn = asyncio.run(write_chat_summary(_merged(), query="ما موقفي؟", deps=deps))
    assert turn.kind == "completed"
    assert seen["query"] == "ما موقفي؟" and seen["decider_result"] is None
    assert seen["agg"].synthesis_md == _merged().synthesis_md and seen["decision"].mode == "full"
