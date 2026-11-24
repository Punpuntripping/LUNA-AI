"""The merger's code side: one shared reference list (deduped, WITH excerpts — D19),
each report's citations rewritten to it, the final renumbering 1..k, the confidence
ceiling, the single-report answer, and the merger's input (masked, every part present)."""
from __future__ import annotations

import pytest

from agents.deep_search_v4.extended import merger
from agents.deep_search_v4.extended.merger import (
    build_merger_input,
    build_shared_references,
    cited_numbers,
    clamp_confidence,
    remap_citations,
    renumber,
    single_report_answer,
)
from backend.app.services.masking_service import reset_active_codec, set_active_codec

from ._exec_fakes import (
    FAKE_ID,
    REAL_ID,
    fixed_codec,
    make_agg,
    make_plan,
    make_ref,
    make_report,
    make_view,
    recent,
)


def _two_reports():
    r1 = make_report(1, make_agg(
        [make_ref(1, "reg:a", "المادة 1", ""), make_ref(2, "reg:b", "المادة 2", "نص المادة الثانية")],
        "الأولى [1] والثانية [2] وكلاهما [1,2].", confidence="medium", gaps=["فجوة أ"],
    ), title="بحث الدين", angles=["A1"])
    r2 = make_report(2, make_agg(
        [make_ref(1, "reg:b", "المادة 2", "نص أطول للمادة الثانية"), make_ref(2, "case:9", "حكم", "منطوق", "case"),
         make_ref(3, "reg:a", "المادة 1", "نص المادة الأولى")],
        "حكم [2] والمادة الثانية [1] والأولى [3] وخطأ [7].", confidence="low",
        gaps=["فجوة ب", "no_references_after_reranker"],
    ), title="بحث الاختصاص", angles=["A2"])
    return r1, r2


# --------------------------------------------------------------------------- citations


def test_cited_numbers_in_order_of_first_use():
    assert cited_numbers("أ [3] ب [1,3] ج [2، 5]") == [3, 1, 2, 5]
    assert cited_numbers("لا شيء") == []


def test_remap_drops_unknown_numbers_and_empty_tags():
    assert remap_citations("أ [1,2] ب [9] ج [2,1]", {1: 4, 2: 4}) == "أ [4] ب  ج [4]"


# --------------------------------------------------------------------------- shared list


def test_shared_list_dedupes_by_ref_id_and_keeps_excerpts():
    r1, r2 = _two_reports()
    shared, maps = build_shared_references([r1, r2])
    assert [r["ref_id"] for r in shared] == ["reg:a", "reg:b", "case:9"]
    assert maps == [{1: 1, 2: 2}, {1: 2, 2: 3, 3: 1}]
    # D19: an excerpt missing on the first carrier is filled from a later report
    assert shared[0]["snippet"] == "نص المادة الأولى"
    assert shared[1]["snippet"] == "نص المادة الثانية"


def test_dedupe_without_ref_id_uses_type_source_and_title():
    a = make_report(1, make_agg([make_ref(1, "", "المادة 5")], "[1]"))
    b = make_report(2, make_agg([make_ref(1, "", "المادة 5"), make_ref(2, "", "المادة 6")], "[1][2]"))
    shared, maps = build_shared_references([a, b])
    assert len(shared) == 2 and maps[1] == {1: 1, 2: 2}


def test_renumber_follows_first_use_and_keeps_only_cited():
    shared = [{"n": 1, "ref_id": "x"}, {"n": 2, "ref_id": "y"}, {"n": 3, "ref_id": "z"}]
    text, refs = renumber("أولاً [3] ثم [1,3]", shared)
    assert text == "أولاً [1] ثم [2,1]"
    assert [(r["n"], r["ref_id"]) for r in refs] == [(1, "z"), (2, "x")]


def test_confidence_never_above_the_best_report():
    r1, r2 = _two_reports()                                  # medium, low
    assert clamp_confidence("high", [r1, r2]) == "medium"
    assert clamp_confidence("low", [r1, r2]) == "low"
    assert clamp_confidence("high", [r2]) == "low"
    assert clamp_confidence("weird", [r1]) == "medium"


def test_single_report_answer_is_the_report_as_is():
    r1, _ = _two_reports()
    ans = single_report_answer(r1)
    assert ans.synthesis_md == r1.agg_output.synthesis_md
    assert [r["n"] for r in ans.references] == [1, 2]
    assert ans.report_titles == ["بحث الدين"] and ans.confidence == "medium"
    assert ans.gaps == ["فجوة أ"] and ans.resolved == []


# --------------------------------------------------------------------------- the input


@pytest.fixture
def codec():
    token = set_active_codec(fixed_codec())
    try:
        yield
    finally:
        reset_active_codec(token)


def test_merger_input_carries_every_part_masked(codec):
    r1, r2 = _two_reports()
    shared, maps = build_shared_references([r1, r2])
    msg = build_merger_input(
        [r1, r2], query="ما موقفي؟", recent_messages=recent(), plan=make_plan(), view=make_view(),
        shared=shared, maps=maps,
    )
    assert REAL_ID not in msg and FAKE_ID in msg               # documents, summaries, understanding
    assert "<query>ما موقفي؟</query>" in msg
    assert '<message role="user">أرفقت الصحيفة</message>' in msg
    assert "<planner_understanding>يريد المستخدم تقييم موقفه " + FAKE_ID in msg
    # every document, whole, with its markers and its summary
    assert '<case_document id="D1" doc_type="claim"' in msg and "〔D1·S1 · claimant · جوهري〕" in msg
    assert '<case_document id="D2" doc_type="memo"' in msg and "لم يُتح نص هذا المستند" in msg
    assert "<summary>ملخص المذكرة</summary>" in msg
    # references with their excerpts (D19)
    assert '<reference n="3" type="case">' in msg and "<excerpt>منطوق</excerpt>" in msg
    # report 2's citations rewritten to the shared list; the unknown [7] dropped
    assert "حكم [3] والمادة الثانية [2] والأولى [1] وخطأ ." in msg
    # each report: title, confidence, its angle card in the §4.9 b line, its gaps (no failure codes)
    assert '<report n="2" title="بحث الاختصاص" confidence="low">' in msg
    assert "- وجه A2 [يجب الرد · متوسط]: دفع عدم الاختصاص" in msg
    assert "<gaps>فجوة ب</gaps>" in msg


def test_merger_documents_are_capped_like_the_searches(monkeypatch):
    from agents.deep_search_v4.extended.models import DocView, PlannerView

    view = PlannerView(map_id="m", message_id=None, angles=[], documents=[
        DocView(id="D1", item_id="i1", title="أ", doc_type="claim", summary="", content_chars=0,
                marked_text="س" * 250_000),
        DocView(id="D2", item_id="i2", title="ب", doc_type="claim", summary="", content_chars=0,
                marked_text="ن" * 150_000),
    ])
    xml = merger._documents_xml(view)
    assert "[انتهى المعروض: 200000 حرف من أصل 250000" in xml
    assert "لتجاوز حد السياق" in xml and "ن" * 150_000 not in xml


def test_the_prompt_carries_the_rules_of_the_build():
    p = merger.merger_system_prompt()
    assert "## Strengths and weaknesses (نقاط القوة ونقاط الضعف)" in p      # §4.9 c
    assert "signature or approval fields are blank" in p                   # §6.3
    assert "cover email" in p                                              # §6.5
    assert "never higher than the highest `confidence`" in p               # §4.6
    assert "code adds that list" in p                                      # D14 by code
