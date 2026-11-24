"""The planner's view of a stored angle map (production loaders over the fake client) and
the D28 planner input."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agents.deep_search_v4.extended import budget as B
from agents.deep_search_v4.extended.models import AngleCard, DocView, PlannerView, SectionView
from agents.deep_search_v4.extended.view import (
    RECENT_MESSAGE_MAX_CHARS,
    RECENT_MESSAGES_MAX,
    build_planner_view,
    marked_text,
    recent_pairs,
    render_planner_input,
)
from agents.memory.angle_mapper.store import load_angle_map_for_item, write_angle_map
from agents.memory.angle_mapper.tests import fixtures as F
from agents.memory.angle_mapper.tests.fake_supabase import FakeSupabase
from agents.memory.attachment_segmenter.paragraphs import PARAGRAPH_RULE_VERSION

ANGLES = [
    {"seq": 1, "for_side": "respondent", "for_party": "المدعى عليه", "kind": "strengthen", "title": "الحكم أغفل التحويلات",
     "argument": "التحويلات تثبت السداد.", "sections": ["D2.S2", "D1.S3", "D1.S4"],
     "legal_question": "ما حجية التحويل البنكي في إثبات السداد؟", "strength": "strong", "weakness": "لا يوجد عقد بيع"},
    {"seq": 2, "for_side": "respondent", "for_party": "المدعى عليه", "kind": "answer", "title": "سندات التسليم",
     "argument": "سندات المدعي.", "sections": ["D1.S2", "D1.S9"], "legal_question": "هل السند يثبت القرض؟",
     "strength": "medium", "weakness": "سندات التسليم"},
    {"seq": 3, "for_side": "respondent", "for_party": "المدعى عليه", "kind": "attack", "title": "الأسباب",
     "argument": "أسباب الحكم.", "sections": ["D1.S5"], "legal_question": "هل الأسباب تحمل المنطوق؟",
     "strength": "weak", "weakness": "", "ocr_suspect": True},
]


def stored(*docs: F.Doc, user_id: str = F.USER) -> tuple[FakeSupabase, dict]:
    docs = docs or (F.JUDGMENT, F.OBJECTION)
    db = FakeSupabase(F.tables(*docs))
    members = [
        {"item_id": d.item_id, "doc_index": n, "content_chars": len(F.content(d)),
         "segment_prompt_version": F.SEG_VERSION, "paragraph_rule_version": PARAGRAPH_RULE_VERSION,
         "segmented_at": F.SEG_CREATED_AT}
        for n, d in enumerate(docs, start=1)
    ]
    write_angle_map(db, message_id=F.MSG, conversation_id=F.CONVO, user_id=user_id, items=members,
                    map_row={"prompt_version": "angles-v5", "client": dict(F.CLIENT_TWO)}, angles=ANGLES)
    return db, load_angle_map_for_item(db, docs[0].item_id)


# ── the view ─────────────────────────────────────────────────────────────────


def test_documents_in_map_order_with_their_rows():
    db, amap = stored()
    view = build_planner_view(db, amap, user_id=F.USER)
    assert view.map_id == amap["map"]["map_id"] and view.message_id == F.MSG
    assert [(d.id, d.item_id, d.title, d.doc_type, d.summary) for d in view.documents] == [
        ("D1", F.ITEM_A, F.JUDGMENT.title, "judgment", F.JUDGMENT.summary),
        ("D2", F.ITEM_B, F.OBJECTION.title, "claim", F.OBJECTION.summary),
    ]
    assert view.documents[0].content_chars == len(F.content(F.JUDGMENT))
    assert view.section_refs == frozenset({f"D1.S{i}" for i in range(1, 7)} | {f"D2.S{i}" for i in range(1, 5)})
    assert view.map_client["side"] == "respondent"


def test_the_marked_text_is_the_whole_document_with_a_marker_per_section():
    db, amap = stored()
    view = build_planner_view(db, amap, user_id=F.USER)
    text = view.documents[0].marked_text
    for line in F.JUDGMENT.lines:  # every paragraph, word for word
        assert line in text
    markers = [x for x in text.split("\n\n") if x.startswith("〔")]
    assert markers == [
        "〔D1·S1 · المحكمة العامة بالرياض · بيانات〕",
        "〔D1·S2 · المدعي · جوهري〕",
        "〔D1·S3 · المدعى عليه · جوهري〕",
        "〔D1·S4 · الخبير المحاسبي، قدّمه المدعى عليه · مساند〕",
        "〔D1·S5 · المحكمة · جوهري〕",
        "〔D1·S6 · المحكمة · قالب〕",
    ]
    blocks = text.split("\n\n")
    assert blocks[0] == markers[0] and blocks[1] == F.JUDGMENT.lines[0]
    assert blocks[blocks.index(markers[3]) + 1] == F.JUDGMENT.lines[8]
    assert view.documents[1].marked_text.startswith("〔D2·S1 · المعترض · بيانات〕\n\n" + F.OBJECTION.lines[0])


def test_angle_cards_with_sections_labelled_relative_to_the_angles_side():
    db, amap = stored()
    view = build_planner_view(db, amap, user_id=F.USER)
    a1, a2, a3 = view.angles
    assert (a1.id, a1.kind, a1.strength, a1.for_side, a1.title) == ("A1", "strengthen", "strong", "respondent",
                                                                     "الحكم أغفل التحويلات")
    assert [(s.ref, s.label, s.owner) for s in a1.sections] == [
        ("D2.S2", "الموكّل", "المعترض"),              # the client is the claimant in D2 (by_document)
        ("D1.S3", "الموكّل", "المدعى عليه"),
        ("D1.S4", "الموكّل", "الخبير المحاسبي، قدّمه المدعى عليه"),
    ]
    assert a1.sections[0].what == "أسباب الاعتراض"
    assert [(s.ref, s.label) for s in a2.sections] == [("D1.S2", "الخصم"), ("D1.S9", "?")]
    assert [(s.ref, s.label) for s in a3.sections] == [("D1.S5", "محايد")]
    assert a3.ocr_suspect is True and a1.ocr_suspect is False


def test_a_member_that_is_gone_raises():
    db, amap = stored()
    db.tables["workspace_items"] = [r for r in db.tables["workspace_items"] if r["item_id"] != F.ITEM_B]
    with pytest.raises(ValueError):
        build_planner_view(db, amap, user_id=F.USER)


def test_a_map_of_another_user_raises():
    db, amap = stored()
    with pytest.raises(ValueError):
        build_planner_view(db, amap, user_id=F.OTHER_USER)


def test_a_failed_read_raises():
    db, amap = stored()
    db.fail = {"workspace_items"}
    with pytest.raises(RuntimeError):
        build_planner_view(db, amap, user_id=F.USER)


def test_a_document_without_its_segment_map_has_no_marked_text():
    db, amap = stored()
    db.tables["attachment_segment_maps"] = [r for r in db.tables["attachment_segment_maps"] if r["item_id"] != F.ITEM_B]
    view = build_planner_view(db, amap, user_id=F.USER)
    assert view.documents[0].marked_text and view.documents[1].marked_text == ""
    assert not any(r.startswith("D2.") for r in view.section_refs)


def test_marked_text_falls_back_to_the_stored_offsets_when_paragraphs_moved():
    text = "أولاً سطر\nثانياً سطر\nثالثاً سطر"
    a, b = text.index("ثانياً"), len(text)
    sections = [{"ref": "D1.S1", "seq": 1, "from_p": 7, "to_p": 9, "char_start": a, "char_end": b,
                 "value": "core", "owner": "المدعي", "brought_by": None}]
    out = marked_text(text, sections, doc_type="judgment")
    assert out == "أولاً سطر\n\n〔D1·S1 · المدعي · جوهري〕\n\nثانياً سطر\nثالثاً سطر"
    assert marked_text(text, [], doc_type="judgment") == ""


# ── the planner input (D28) ──────────────────────────────────────────────────


def small_view(**doc_kw) -> PlannerView:
    return PlannerView(
        map_id="m1", message_id="msg",
        documents=[DocView(id="D1", item_id="i1", title=doc_kw.get("title", "صك <حكم> & ملحق"), doc_type="judgment",
                           summary="ملخص سري لا يراه المخطِّط", content_chars=10, marked_text="نص الملف السري")],
        angles=[AngleCard(id="A1", kind="strengthen", strength="strong", for_side="respondent", for_party="المدعى عليه",
                          title="عنوان", argument="حجة", legal_question="سؤال؟", weakness="ضعف",
                          sections=[SectionView(ref="D1.S3", label="الموكّل", owner="المدعى عليه", what="الدفع")])],
        section_refs=frozenset({"D1.S3"}),
    )


def test_the_input_layout_as_tested():
    recent = [SimpleNamespace(role="user", content="سؤال سابق"), {"role": "assistant", "content": "جواب <سابق>"}]
    out = render_planner_input(small_view(), query="ما موقفي؟", recent_messages=recent, mode=B.MODES[15])
    assert out == "\n".join([
        "<recent_messages>",
        '  <message role="user">سؤال سابق</message>',
        '  <message role="assistant">جواب &lt;سابق&gt;</message>',
        "</recent_messages>",
        "",
        B.budget_block(B.MODES[15]),
        "",
        "<documents>",
        '  <document id="D1" doc_type="judgment">صك &lt;حكم&gt; &amp; ملحق</document>',
        "</documents>",
        "",
        "<angles>",
        '  <angle id="A1" kind="strengthen" strength="strong" for_side="respondent" for="المدعى عليه">',
        "    <title>عنوان</title>",
        "    <argument>حجة</argument>",
        "    <legal_question>سؤال؟</legal_question>",
        "    <weakness>ضعف</weakness>",
        "    <sections>D1.S3 (الموكّل: المدعى عليه — الدفع)</sections>",
        "  </angle>",
        "</angles>",
        "",
        "<query>ما موقفي؟</query>",
    ])
    assert "ملخص" not in out and "نص الملف" not in out  # D28: no summary, no file text


def test_no_history_and_the_no_budget_block():
    out = render_planner_input(small_view(), query="q", recent_messages=None, mode=B.NO_BUDGET)
    assert out.startswith("<recent_messages>\n  (none)\n</recent_messages>\n\n<budget>\nNo budget limit")


def test_recent_messages_drop_the_turns_own_row_and_are_capped():
    long = "س" * (RECENT_MESSAGE_MAX_CHARS + 10)
    msgs = [{"role": "user", "content": f"م{i}"} for i in range(10)] + [
        {"role": "assistant", "content": long}, {"role": "assistant", "content": "  "},
        {"role": "user", "content": "[مرفق] ما موقفي؟"},
    ]
    pairs = recent_pairs(msgs, query="ما موقفي؟")
    assert len(pairs) == RECENT_MESSAGES_MAX
    assert pairs[-1] == ("assistant", "س" * RECENT_MESSAGE_MAX_CHARS + " …[مقتطع]")
    assert recent_pairs([{"role": "assistant", "content": "ما موقفي؟"}], query="ما موقفي؟") == [
        ("assistant", "ما موقفي؟")]
