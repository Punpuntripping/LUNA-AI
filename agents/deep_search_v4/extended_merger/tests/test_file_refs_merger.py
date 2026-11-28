"""The merger and the file refs («بحث شامل», aggregator_file_refs.md step 2): with tagged
reports it reads the searches' own documents under their file tags, its prompt keeps
each report's tag with its point, code resolves the answer's tags against what the
reports could cite, and the publisher stores them for the merged item. Off = today."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agents.agent_search.deps import SearchPublishDeps
from agents.deep_search_v4.aggregator.models import AggregatorOutput, FileRef, Reference
from agents.deep_search_v4.extended.runner import whole_file_items, whole_file_refs
from agents.deep_search_v4.extended.tests._exec_fakes import (
    MARKED_D1,
    attached_snapshot,
    make_agg,
    make_plan,
    make_ref,
    make_report,
    make_view,
)
from agents.deep_search_v4.extended_merger import prompts as P
from agents.deep_search_v4.extended_merger import publisher
from agents.deep_search_v4.extended_merger.adapter import input_from_extended, merged_answer_from
from agents.deep_search_v4.extended_merger.models import (
    MergedPublishInput,
    MergerAngle,
    MergerInput,
    MergerOutput,
    MergerReport,
)
from agents.deep_search_v4.extended_merger.runner import run_merger
from agents.deep_search_v4.shared.case_documents import FileRefDoc

SPANS = ((1, 0, 40), (2, 41, 80))
DOCS = [FileRefDoc(wi_seq=3, item_id="item-1", sections=SPANS), FileRefDoc(wi_seq=5, item_id="item-9")]


# --------------------------------------------------------------------------- the prompt


def test_off_the_prompt_is_todays_byte_for_byte():
    assert P.merger_system_prompt() is P.MERGER_SYSTEM_PROMPT
    assert P.merger_system_prompt(file_refs=False) == P.MERGER_SYSTEM_PROMPT


@pytest.mark.parametrize("old, new", P._FILE_REFS_SWAPS)
def test_on_every_swap_lands_once(old, new):
    assert P.MERGER_SYSTEM_PROMPT.count(old) == 1  # an anchor that moved would silently miss
    on = P.merger_system_prompt(file_refs=True)
    assert new in on and (old not in on or old in new)


def test_on_the_prompt_keeps_tags_with_their_point():
    on = P.merger_system_prompt(file_refs=True)
    assert "8. **File tags — keep them with their point.**" in on
    assert "keep its tag right after it, copied exactly as the report wrote it" in on
    assert "〔F3·S7 · …〕" in on and "Lines like 〔D1·S7 · …〕" not in on
    assert "never by a code alias (WI-N, D1, S7)" not in on
    assert P.FILE_REFS_PROMPT_VERSION == "merger-file-refs-v1"


# --------------------------------------------------------------------------- the adapter


def _reports(*, tagged: bool):
    view = make_view()
    view.documents[0].section_spans = SPANS
    items = whole_file_items(view, [attached_snapshot("item-1", 3)], {7: "item-2"}, file_tags=tagged)
    refs = whole_file_refs(view, items) if tagged else []
    agg = make_agg([make_ref(1, "reg:x")], "تقرير [1] [F3·S1]")
    reports = [make_report(1, agg, angles=["A1"]), make_report(2, agg, angles=["A2"])]
    for r in reports:
        r.documents, r.file_ref_docs = items, refs
    return view, reports, refs


def test_with_tags_the_merger_reads_the_searches_documents():
    view, reports, refs = _reports(tagged=True)
    inp = input_from_extended(reports, query="س", recent_messages=[], plan=make_plan(), view=view)
    assert inp.file_ref_docs == refs == [FileRefDoc(wi_seq=3, item_id="item-1", sections=SPANS)]
    d1 = inp.documents[0]
    assert d1.ref == "F3" and d1.text.startswith("〔F3·S1 ·") and "〔D1·S" not in d1.text


def test_without_tags_the_merger_input_is_todays():
    view, reports, _ = _reports(tagged=False)
    inp = input_from_extended(reports, query="س", recent_messages=[], plan=make_plan(), view=view)
    assert inp.file_ref_docs == []
    assert inp.documents[0].ref == "D1" and inp.documents[0].text == MARKED_D1


# --------------------------------------------------------------------------- the runner


def _ref(n: int, rid: str) -> Reference:
    return Reference(n=n, source_type="chunk", regulation_title="نظام", title="نظام", relevance="high",
                     ref_id=rid, snippet="")


def _input(file_ref_docs) -> MergerInput:
    reps = [
        MergerReport(title="الأول", mode="full", focus="ف", angle_ids=["A1"],
                     output=AggregatorOutput(synthesis_md="أ [1] «نص» [F3·S1].", confidence="high",
                                             references=[_ref(1, "reg:x")])),
        MergerReport(title="الثاني", mode="case_led", focus="ف", angle_ids=["A2"],
                     output=AggregatorOutput(synthesis_md="ب [1] [F5].", confidence="high",
                                             references=[_ref(1, "case:y")])),
    ]
    angles = {a: MergerAngle(id=a, kind="strengthen", strength="strong", title=a, argument="ح",
                             legal_question="س؟") for a in ("A1", "A2")}
    return MergerInput(query="س", reports=reps, angles=angles, client_side="claimant",
                       file_ref_docs=list(file_ref_docs))


class _Model:
    def __init__(self) -> None:
        self.instructions = ""

    def fn(self, messages: list, info: AgentInfo) -> ModelResponse:
        self.instructions = str(getattr(messages[-1], "instructions", "") or "")
        out = {
            "angle_status": [{"angle": "A1", "status": "supported", "note": "يثبته الحكم [F3·S1]"},
                             {"angle": "A2", "status": "open", "note": "ن"}],
            "resolved": [{"topic": "ت [F5]", "reports": [1, 2], "decision": "قرار [F3·S2]", "settled": True}],
            "synthesis_md": "## الخلاصة\nأ [1] «نص» [F3·S1]. ب [2] [F3·S9][F5]. ج [F7·S1].",
            "used_refs": [1, 2],
            "gaps": ["فجوة [F5]"],
            "confidence": "high",
        }
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, json.dumps(out, ensure_ascii=False))])


def test_with_tags_the_answers_tags_resolve_and_stay_in_the_text():
    model = _Model()
    out, _ = asyncio.run(run_merger(_input(DOCS), model=FunctionModel(model.fn)))
    assert "8. **File tags" in model.instructions
    assert out.synthesis_md == "## الخلاصة\nأ [1] «نص» [F3·S1]. ب [2] [F3·S9][F5]. ج [F7·S1]."  # code never edits a tag
    assert [(r.n, r.ref_id, r.item_id, r.wi_seq, r.section_seq, r.char_start, r.char_end) for r in out.file_refs] == [
        (1, "F3.S1", "item-1", 3, 1, 0, 40),
        (2, "F5", "item-9", 5, None, None, None),
    ]  # F3·S9 is not a section of the file, F7 not a file the reports read → no row
    assert out.angle_status[0].note == "يثبته الحكم"
    assert (out.resolved[0].topic, out.resolved[0].decision) == ("ت", "قرار")
    assert out.gaps == ["فجوة"]
    assert out.stats["file_refs"] == 2 and out.stats["file_refs_prompt_version"] == "merger-file-refs-v1"


def test_without_tags_no_file_refs_and_todays_prompt():
    model = _Model()
    out, _ = asyncio.run(run_merger(_input([]), model=FunctionModel(model.fn)))
    assert "8. **File tags" not in model.instructions and "Lines like 〔D1·S7 · …〕" in model.instructions
    assert out.file_refs == [] and "file_refs" not in out.stats


def test_the_file_refs_survive_the_handoff_to_the_publisher():
    out, _ = asyncio.run(run_merger(_input(DOCS), model=FunctionModel(_Model().fn)))
    merged = merged_answer_from(out, [])
    assert MergerOutput.model_validate(merged.merger).file_refs == out.file_refs


# --------------------------------------------------------------------------- the publisher


def _publish(monkeypatch, file_refs):
    written: list[dict] = []
    monkeypatch.setattr(publisher, "create_workspace_item",
                        lambda supabase, user_id, **kw: {"item_id": "wi-merged", "title": kw["title"]})
    monkeypatch.setattr(publisher, "persist_item_references", lambda supabase, **kw: 0)
    monkeypatch.setattr(publisher, "decode_for_persist", lambda text: text)
    from backend.app.services import file_refs_service

    monkeypatch.setattr(file_refs_service, "persist_item_file_refs",
                        lambda supabase, **kw: written.append(kw) or len(kw["file_refs"]))
    out = MergerOutput(synthesis_md="أ [F3·S1]", confidence="medium", file_refs=file_refs)
    asyncio.run(publisher.publish_merged_result(
        MergedPublishInput(user_id="user-1", conversation_id="conv-1", output=out, original_query="س"),
        SearchPublishDeps(supabase=SimpleNamespace(name="sb")),
    ))
    return written


def test_the_merged_item_stores_its_file_refs(monkeypatch):
    refs = [FileRef(n=1, ref_id="F3.S1", item_id="item-1", wi_seq=3, section_seq=1, char_start=0, char_end=40)]
    assert _publish(monkeypatch, refs) == [{"wi_id": "wi-merged", "user_id": "user-1", "file_refs": refs}]


def test_no_file_ref_no_write(monkeypatch):
    assert _publish(monkeypatch, []) == []
