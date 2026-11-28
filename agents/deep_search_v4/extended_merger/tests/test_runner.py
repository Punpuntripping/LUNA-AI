"""Runner + references + validator on a scripted FunctionModel (no network)."""
from __future__ import annotations

import asyncio
import json

from pydantic_ai.messages import ModelResponse, RetryPromptPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agents.deep_search_v4.aggregator.models import AggregatorOutput, Reference
from agents.deep_search_v4.extended_merger.models import (
    AngleStatus,
    LeftOutAngle,
    MergerAngle,
    MergerInput,
    MergerReport,
)
from agents.deep_search_v4.extended_merger.references import (
    build_shared_references,
    remap_citations,
    renumber_answer,
    strip_citations,
    western_citations,
)
from agents.deep_search_v4.extended_merger.runner import clamp_confidence, repair_scoreboard, run_merger


def _ref(n: int, rid: str, title: str = "نظام") -> Reference:
    return Reference(n=n, source_type="chunk", regulation_title=title, title=title, relevance="high",
                     ref_id=rid, snippet=f"نص {rid}")


def _input(conf=("medium", "medium"), failed_third: bool = False) -> MergerInput:
    r1 = MergerReport(title="البحث الأول", mode="reg_compliance_led", focus="ف1", angle_ids=["A1", "A2"],
                      output=AggregatorOutput(synthesis_md="أ [1]. ب [2].", confidence=conf[0],
                                              references=[_ref(1, "reg:x"), _ref(2, "reg:y")], gaps=["فجوة"]),
                      item_id="wi-1")
    r2 = MergerReport(title="البحث الثاني", mode="case_led", focus="ف2", angle_ids=["A3"],
                      output=AggregatorOutput(synthesis_md="ج [1,2].", confidence=conf[1],
                                              references=[_ref(1, "reg:y"), _ref(2, "case:z")]),
                      item_id="wi-2")
    reports = [r1, r2]
    if failed_third:
        reports.append(MergerReport(title="البحث الثالث", mode="full", focus="ف3", angle_ids=["A4"]))
    angles = {f"A{i}": MergerAngle(id=f"A{i}", kind="strengthen", strength="strong", title=f"وجه {i}",
                                   argument="حجة", legal_question="سؤال؟") for i in range(1, 6)}
    return MergerInput(query="ما موقفي؟", reports=reports, angles=angles, client_side="claimant",
                       left_out=[LeftOutAngle("A5", "خارج السؤال")])


def test_shared_list_dedupes_and_remaps():
    shared, bodies, sources, dropped = build_shared_references(_input().reports)
    assert [r.ref_id for r in shared] == ["reg:x", "reg:y", "case:z"]
    assert bodies == ["أ [1]. ب [2].", "ج [2,3]."]
    assert sources == {1: [1], 2: [1, 2], 3: [2]}
    assert dropped == 0


def test_failed_report_has_no_body():
    _, bodies, _, _ = build_shared_references(_input(failed_third=True).reports)
    assert bodies[2] is None


def test_remap_drops_unknown_numbers_and_empty_tags():
    assert remap_citations("أ [1,9] ب [9].", {1: 4}) == ("أ [4] ب .", 2)


def test_western_citations_and_strip():
    assert western_citations("نص [١٢،٣]") == "نص [12،3]"
    assert strip_citations("وفق المادة 5 [3] من النظام") == "وفق المادة 5 من النظام"


def test_renumber_answer_order_of_first_use():
    shared, *_ = build_shared_references(_input().reports)
    text, refs, final = renumber_answer("ب [3]. أ [1,3].", shared)
    assert text == "ب [1]. أ [2,1]."
    assert [(r.n, r.ref_id) for r in refs] == [(1, "case:z"), (2, "reg:x")]
    assert final == {3: 1, 1: 2}


def test_clamp_confidence():
    assert clamp_confidence("high", _input(("medium", "low"))) == "medium"
    assert clamp_confidence("low", _input(("medium", "high"))) == "low"


def test_repair_scoreboard():
    board, stats = repair_scoreboard(
        [AngleStatus(angle="A2", status="supported", note="أساس [3]"),
         AngleStatus(angle="A9", status="open", note="x"),
         AngleStatus(angle="A2", status="open", note="مكرر")],
        ["A1", "A2"],
    )
    assert [(s.angle, s.status) for s in board] == [("A1", "open"), ("A2", "supported")]
    assert board[1].note == "أساس"
    assert stats == {"unknown": 1, "duplicate": 1, "filled": 1}


class _Script:
    """First answer cites [7] (outside the list) and misses A2–A4; the retry cites
    right but still misses A4, and names report 9 — both repaired by code."""

    def __init__(self) -> None:
        self.calls = 0
        self.retry_text = ""

    def fn(self, messages: list, info: AgentInfo) -> ModelResponse:
        self.calls += 1
        if self.calls == 1:
            out = {"angle_status": [{"angle": "A1", "status": "supported", "note": "ن"}],
                   "resolved": [], "synthesis_md": "## الخلاصة\nأ [7].", "used_refs": [7],
                   "gaps": [], "confidence": "high"}
        else:
            for part in messages[-1].parts:
                if isinstance(part, RetryPromptPart):
                    self.retry_text = str(part.content)
            out = {"angle_status": [{"angle": a, "status": "supported", "note": "ن"} for a in ("A1", "A2", "A3")],
                   "resolved": [{"topic": "ت", "reports": [1, 2, 9], "decision": "قرار [2]", "settled": True}],
                   "synthesis_md": "## الخلاصة\nج [2,3]. أ [1].", "used_refs": [1, 2, 3],
                   "gaps": ["فجوة", "فجوة"], "confidence": "high"}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, json.dumps(out, ensure_ascii=False))])


def test_run_merger_end_to_end():
    script = _Script()
    inp = _input(failed_third=True)
    out, usage = asyncio.run(run_merger(inp, model=FunctionModel(script.fn)))
    assert script.calls == 2
    assert "[7]" in script.retry_text  # citations are the only thing sent back
    assert out.synthesis_md == "## الخلاصة\nج [1,2]. أ [3]."
    assert [(r.n, r.ref_id) for r in out.references] == [(1, "reg:y"), (2, "case:z"), (3, "reg:x")]
    assert out.ref_reports == {1: [1, 2], 2: [2], 3: [1]}
    assert out.confidence == "medium"  # clamped to the reports' highest
    assert [(s.angle, s.status) for s in out.angle_status] == [
        ("A1", "supported"), ("A2", "supported"), ("A3", "supported"), ("A4", "open")]
    assert out.stats["scoreboard_filled"] == 1 and out.stats["scoreboard_missing"] == 1
    assert out.resolved[0].decision == "قرار" and out.resolved[0].reports == [1, 2]
    assert out.gaps == ["فجوة"]
    assert [a.id for a in out.angles] == ["A1", "A2", "A3", "A4"]
    assert out.left_out[0].angle == "A5" and out.left_out[0].title == "وجه 5"
    assert [(r.n, r.ok, r.item_id) for r in out.reports] == [(1, True, "wi-1"), (2, True, "wi-2"), (3, False, None)]
    assert out.prompt_version == "merger-v2"


def test_run_merger_raises_without_a_finished_report():
    inp = MergerInput(query="س", reports=[MergerReport(title="ب", mode="full", focus="ف", angle_ids=["A1"])])
    try:
        asyncio.run(run_merger(inp, model=FunctionModel(lambda m, i: None)))
    except ValueError:
        return
    raise AssertionError("expected ValueError")
