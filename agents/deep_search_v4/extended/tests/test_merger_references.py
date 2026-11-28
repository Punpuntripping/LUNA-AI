"""The merger's input as the extended search builds it (``extended_merger.adapter``):
every part present and masked, each report's citations rewritten to one shared
reference list (merger-v2: titles only, no excerpts), pipeline gap codes dropped,
documents capped like the searches; and the single-report answer the wiring falls back to."""
from __future__ import annotations

import pytest

from agents.deep_search_v4.extended.merger import single_report_answer
from agents.deep_search_v4.extended_merger.adapter import _documents, input_from_extended, merged_answer_from
from agents.deep_search_v4.extended_merger.models import MergerOutput
from agents.deep_search_v4.extended_merger.prompts import build_merger_user_message
from agents.deep_search_v4.extended_merger.references import build_shared_references
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


def test_single_report_answer_is_the_report_as_is():
    r1, _ = _two_reports()
    ans = single_report_answer(r1)
    assert ans.synthesis_md == r1.agg_output.synthesis_md
    assert [r["n"] for r in ans.references] == [1, 2]
    assert ans.report_titles == ["بحث الدين"] and ans.confidence == "medium"
    assert ans.gaps == ["فجوة أ"] and ans.resolved == [] and ans.merger is None


@pytest.fixture
def codec():
    token = set_active_codec(fixed_codec())
    try:
        yield
    finally:
        reset_active_codec(token)


def _message(reports, plan=None):
    inp = input_from_extended(reports, query="ما موقفي؟", recent_messages=recent(),
                              plan=plan or make_plan(), view=make_view())
    shared, bodies, _, _ = build_shared_references(inp.reports)
    return inp, build_merger_user_message(inp, shared_refs=shared, report_bodies=bodies)


def test_merger_input_carries_every_part_masked(codec):
    from agents.deep_search_v4.extended.models import LeftOut

    plan = make_plan()
    plan.left_out = [LeftOut(angle="A9", why="خارج السؤال " + REAL_ID)]
    _inp, msg = _message(list(_two_reports()), plan)
    assert REAL_ID not in msg and FAKE_ID in msg               # documents, understanding, left out
    assert "<query>\nما موقفي؟\n</query>" in msg
    assert '<message role="user">أرفقت الصحيفة</message>' in msg
    assert '<planner_understanding client_side="claimant"' in msg and "يريد المستخدم تقييم موقفه " + FAKE_ID in msg
    # every document, whole, with its markers and its number — its text only, no summary (merger-v2)
    assert '<case_document ref="D1" title="صحيفة الدعوى" doc_type="claim">' in msg
    assert "〔D1·S1 · claimant · جوهري〕" in msg
    assert '<case_document ref="D2" title="مذكرة الخصم" doc_type="memo">' in msg and "لم يُتح نص هذا المستند" in msg
    assert "<summary>" not in msg and "ملخص المذكرة" not in msg and "ملخص الصحيفة" not in msg
    # references by title only — no excerpt (merger-v2)
    assert '<reference n="3" type="case">' in msg
    assert "<excerpt>" not in msg and "نص أطول للمادة الثانية" not in msg
    # report 2's citations rewritten to the shared list; the unknown [7] dropped
    assert "حكم [3] والمادة الثانية [2] والأولى [1] وخطأ ." in msg
    # each report: title, side, confidence, its angle in the §4.9 b line (no sections), its gaps
    assert '<report n="2" title="بحث الاختصاص" side="الأنظمة" confidence="low" status="ok">' in msg
    assert "- وجه A2 [يجب الرد · متوسط]: دفع عدم الاختصاص" in msg
    assert "الأقسام" not in msg and "D1·S2 (الخصم: respondent)" not in msg
    assert "<gap>فجوة ب</gap>" in msg and "no_references_after_reranker" not in msg
    # merger-v2 order: reports, then references, then the user's file
    assert msg.index("</reports>") < msg.index("<references ") < msg.index("<case_documents>")
    assert '<angle id="A9">' in msg and "خارج السؤال " + FAKE_ID in msg


def test_the_memory_block_reaches_the_merger_untouched(codec):
    block = ('<conversation_memory>\nقواعد\n\n'
             '<entry id="M-1" kind="رأي قانوني — أصرّ عليه المستخدم">العقد تجاري</entry>\n</conversation_memory>')
    inp = input_from_extended(list(_two_reports()), query="ما موقفي؟", recent_messages=recent(),
                              plan=make_plan(), view=make_view(), conversation_memory=block)
    assert inp.conversation_memory == block                     # already encoded: never re-encoded
    shared, bodies, _, _ = build_shared_references(inp.reports)
    msg = build_merger_user_message(inp, shared_refs=shared, report_bodies=bodies)
    assert block in msg and "أصرّ عليه المستخدم]` entry is followed" in msg


def test_no_memory_no_block(codec):
    inp, msg = _message(list(_two_reports()))
    assert inp.conversation_memory == "" and "<conversation_memory>" not in msg


def test_a_failed_report_reaches_the_scoreboard(codec):
    r1, r2 = _two_reports()
    r3 = make_report(3, make_agg([], ""), title="فشل", ok=False, angles=["A1"])
    inp, msg = _message([r1, r2, r3])
    assert [r.ok for r in inp.reports] == [True, True, False]
    assert '<report n="3" title="فشل" side="الأنظمة" status="failed">' in msg


def test_merger_documents_are_capped_like_the_searches():
    from agents.deep_search_v4.extended.models import DocView, PlannerView

    view = PlannerView(map_id="m", message_id=None, angles=[], documents=[
        DocView(id="D1", item_id="i1", title="أ", doc_type="claim", summary="", content_chars=0,
                marked_text="س" * 250_000),
        DocView(id="D2", item_id="i2", title="ب", doc_type="claim", summary="", content_chars=0,
                marked_text="ن" * 150_000),
    ])
    docs = _documents(view)
    assert "[انتهى المعروض: 200000 حرف من أصل 250000" in docs[0].text
    assert "لتجاوز حد السياق" in docs[1].text and "ن" * 150_000 not in docs[1].text


def test_merged_answer_carries_the_merger_output():
    r1, r2 = _two_reports()
    out = MergerOutput(synthesis_md="ج [1]", references=[make_ref(1, "case:9", source_type="case")],
                       confidence="low", gaps=["ف"])
    ans = merged_answer_from(out, [r1, r2])
    assert ans.synthesis_md == "ج [1]" and ans.references[0]["ref_id"] == "case:9"
    assert ans.report_titles == ["بحث الدين", "بحث الاختصاص"]
    assert MergerOutput.model_validate(ans.merger).synthesis_md == "ج [1]"
