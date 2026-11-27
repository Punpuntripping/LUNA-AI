"""``merge_reports`` end to end on a FunctionModel (no real LLM), through the
``extended_merger`` package: the validator sends back citations outside the shared
list, code renumbers 1..k, caps the confidence, repairs the scoreboard and the
``resolved`` report numbers, and one ledger row ``deep_search.extended_merger`` is
written after the masking fakes are persisted."""
from __future__ import annotations

import asyncio
import json

import pytest
from pydantic_ai.messages import ModelResponse, RetryPromptPart, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agents.deep_search_v4.extended import merger
from agents.deep_search_v4.extended.merger import merge_reports
from agents.deep_search_v4.extended_merger import agent as merger_agent
from agents.utils import agent_models, usage_sink
from backend.app.services import masking_service

from ._exec_fakes import make_agg, make_plan, make_ref, make_report, make_view, recent


def _reports():
    r1 = make_report(1, make_agg([make_ref(1, "reg:a"), make_ref(2, "reg:b")], "أ [1] ب [2]",
                                 confidence="medium"), title="بحث الدين", angles=["A1"])
    r2 = make_report(2, make_agg([make_ref(1, "case:9", source_type="case")], "ج [1]",
                                 confidence="medium"), title="بحث الاختصاص", angles=["A2"])
    r3 = make_report(3, make_agg([], "", confidence="low"), title="فشل", ok=False, angles=["A2"])
    return [r1, r2, r3]          # shared list: 1 reg:a · 2 reg:b · 3 case:9


def _out(**kw) -> str:
    body = {
        "angle_status": [{"angle": "A1", "status": "supported", "note": "ثابت بالسند [3]"}],
        "resolved": [{"topic": "الميعاد", "reports": [1, 2, 9], "decision": "يُحسب من اليوم التالي", "settled": True}],
        "synthesis_md": "## الخلاصة\nنتيجة [3] ثم [1].", "used_refs": [3, 1],
        "gaps": ["فجوة", "فجوة"], "confidence": "high",
    }
    body.update(kw)
    return json.dumps(body, ensure_ascii=False)


def _model(replies: list[str], seen: list):
    def fn(messages, info: AgentInfo) -> ModelResponse:
        seen.append(messages)
        return ModelResponse(parts=[TextPart(replies[min(len(seen), len(replies)) - 1])])

    return FunctionModel(fn)


def _use(monkeypatch, model) -> None:
    monkeypatch.setattr(merger_agent, "get_agent_model", lambda slot: model)


def _merge(monkeypatch, replies, **kw):
    seen: list = []
    _use(monkeypatch, _model(replies, seen))
    out = asyncio.run(merge_reports(
        _reports(), query="ما موقفي؟", recent_messages=recent(), plan=make_plan(), view=make_view(), **kw,
    ))
    return out, seen


def test_merge_renumbers_clamps_and_repairs(monkeypatch):
    out, seen = _merge(monkeypatch, [_out()])
    assert len(seen) == 1                                       # nothing sent back
    assert out.synthesis_md == "## الخلاصة\nنتيجة [1] ثم [2]."
    assert [(r["n"], r["ref_id"]) for r in out.references] == [(1, "case:9"), (2, "reg:a")]
    assert out.confidence == "medium"                           # model said high; best report medium
    assert out.gaps == ["فجوة"]
    assert out.resolved[0].reports == [1, 2]                    # 9 is not a report
    assert out.report_titles == ["بحث الدين", "بحث الاختصاص"]    # usable reports, plan order
    m = out.merger
    assert [(s["angle"], s["status"]) for s in m["angle_status"]] == [("A1", "supported"), ("A2", "open")]
    assert m["angle_status"][0]["note"] == "ثابت بالسند"         # no [n] outside the answer
    assert [(r["n"], r["ok"]) for r in m["reports"]] == [(1, True), (2, True), (3, False)]
    assert m["ref_reports"] == {"1": [2], "2": [1]}             # JSON keys: final n → reports


def test_unknown_citations_are_sent_back(monkeypatch):
    out, seen = _merge(monkeypatch, [_out(synthesis_md="خطأ [4]"), _out(synthesis_md="صحيح [2]")])
    assert len(seen) == 2
    retry = [p for m in seen[1] for p in getattr(m, "parts", []) if isinstance(p, RetryPromptPart)]
    assert retry and "not in <references> (1..3)" in str(retry[-1].content)
    assert out.synthesis_md == "صحيح [1]" and out.references[0]["ref_id"] == "reg:b"


def test_the_merger_reads_the_instructions_and_the_input(monkeypatch):
    captured: dict = {}

    def fn(messages, info: AgentInfo) -> ModelResponse:
        captured["messages"] = messages
        return ModelResponse(parts=[TextPart(_out())])

    _use(monkeypatch, FunctionModel(fn))
    asyncio.run(merge_reports(_reports(), query="ما موقفي؟", recent_messages=[], plan=make_plan(), view=make_view()))
    req = captured["messages"][0]
    assert "merge parallel research reports into ONE answer" in (req.instructions or "")
    assert "angle scoreboard" in (req.instructions or "")
    user = "".join(str(p.content) for p in req.parts if hasattr(p, "content"))
    assert '<reports count="3">' in user and '<report n="3"' in user and 'status="failed"' in user
    assert "<references" in user and "A1, A2" in user        # the scoreboard's ids, failed report included


def test_fewer_than_two_usable_reports_is_an_error(monkeypatch):
    _use(monkeypatch, _model([_out()], []))
    r1, _r2, r3 = _reports()
    with pytest.raises(ValueError):
        asyncio.run(merge_reports([r1, r3], query="q", recent_messages=[], plan=make_plan(), view=make_view()))


def test_a_model_failure_raises_for_the_caller(monkeypatch):
    def fn(messages, info):
        raise RuntimeError("provider down")

    _use(monkeypatch, FunctionModel(fn))
    with pytest.raises(RuntimeError):
        asyncio.run(merge_reports(_reports(), query="q", recent_messages=[], plan=make_plan(), view=make_view()))


def test_ledger_row_and_fakes_flushed_before_the_model(monkeypatch):
    order: list[str] = []
    rows: list[dict] = []
    monkeypatch.setattr(usage_sink, "_flush", lambda _sb, buf: rows.extend(buf or []) or len(buf or []))
    monkeypatch.setattr(masking_service, "persist_new_mappings", lambda sb, uid, c: order.append("persist") or 0)

    def fn(messages, info):
        order.append("model")
        return ModelResponse(parts=[TextPart(_out())])

    _use(monkeypatch, FunctionModel(fn))
    with usage_sink.collect_llm_calls(None, conversation_id="c1", user_id="u1"):
        asyncio.run(merge_reports(
            _reports(), query="q", recent_messages=[], plan=make_plan(), view=make_view(),
            supabase=object(), user_id="u1", conversation_id="c1",
        ))
    assert order == ["persist", "model"]
    assert [r["agent"] for r in rows] == ["deep_search.extended_merger"]
    assert rows[0]["agent_family"] == "deep_search" and rows[0]["tokens_in"] > 0


def test_the_slot_is_the_aggregators_policy():
    assert merger.MODEL_SLOT == merger_agent.MODEL_SLOT == "extended_merger"
    assert agent_models.AGENT_MODELS["extended_merger"] == agent_models.AGENT_MODELS["aggregator"]
