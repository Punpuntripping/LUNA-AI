"""Section refs (angles-v4: ``"D1.S7"``), the lenient parsing of the model's answer
(the angle's ``ocr_suspect``, v5), the ref checks, coverage across documents, the
v5 checks (one question, equal-value contradictions) and the result's stats."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.memory.angle_mapper import refs as R
from agents.memory.angle_mapper import validate as V
from agents.memory.angle_mapper.models import AngleMap, ClientInDocument, KINDS, new_result
from agents.memory.attachment_segmenter.models import StoredSegment

from . import fixtures as F


def group_sections(*docs: F.Doc) -> list[dict]:
    """The runner's flat section dicts (``group.GroupDoc.sections``) for ``docs`` as D1…"""
    out = []
    for i, d in enumerate(docs, start=1):
        for row in F.segment_rows(d):
            out.append({**{k: v for k, v in row.items() if k != "content_md"}, "ref": F.ref(i, row["seq"]),
                        "doc": i, "item_id": d.item_id})
    return out


ONE = group_sections(F.JUDGMENT)
TWO = group_sections(F.JUDGMENT, F.OBJECTION)
REFS_ONE = V.section_refs(ONE)
REFS_TWO = V.section_refs(TWO)


def amap(**kw) -> AngleMap:
    return AngleMap.model_validate(F.good_two(**kw))


# ── refs.py ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw, canon", [
    ("D2.S7", "D2.S7"), ("D2·S7", "D2.S7"), ("d2.s7", "D2.S7"), ("D02-S007", "D2.S7"), ("D2 S7", "D2.S7"),
    ("[D2S7]", "D2.S7"), (" D2 · S 7 ", "D2.S7"), ("D2_S7", "D2.S7"), ("D2/S7", "D2.S7"), ("D2:S7", "D2.S7"),
    ("D2|S7", "D2.S7"), ("D2,S7", "D2.S7"), ("D2•S7", "D2.S7"),
    (7, "S7"), (7.0, "S7"), ("7", "S7"), ("S7", "S7"), ("s07", "S7"), ("[S7]", "S7"),
    ("P007", "P007"), ("x", "x"), ("", ""), (None, ""), (True, "True"), (7.5, "7.5"),
])
def test_canonical_ref(raw, canon):
    assert R.canonical_ref(raw) == canon


@pytest.mark.parametrize("raw, canon", [
    ("D2", "D2"), ("d2", "D2"), (2, "D2"), ("2", "D2"), ("Document 2", "D2"), ("doc 02", "D2"), ("[D3]", "D3"),
    ("x", "x"), (None, ""), (True, "True"),
])
def test_canonical_doc(raw, canon):
    assert R.canonical_doc(raw) == canon


def test_parse_doc_of_and_bare():
    assert R.parse_ref("D2·S7") == (2, 7) and R.parse_ref("S7") is None and R.parse_ref("x") is None
    assert R.doc_of("d3.s1") == 3 and R.doc_of(7) is None
    assert R.is_bare("S7") and R.is_bare(7) and not R.is_bare("D1.S7") and not R.is_bare("x")
    assert R.format_ref(2, 7) == "D2.S7" and R.format_doc(4) == "D4"


def test_qualify_only_for_one_document():
    assert R.qualify("S7", True) == "D1.S7" and R.qualify(7, True) == "D1.S7"
    assert R.qualify("S7", False) == "S7"
    assert R.qualify("D2·S3", False) == R.qualify("D2·S3", True) == "D2.S3"


def test_display_ref():
    assert R.display_ref("D2.S7") == "D2·S7" == R.display_ref("d2-s07")
    assert R.DISPLAY_SEP == "·"
    assert R.display_ref("S7") == "S7" and R.display_ref("x") == "x"


# ── the model's answer: lenient refs, strict kind ────────────────────────────


def test_refs_parse_leniently_everywhere():
    m = amap(angles=[F.angle(sections=["D1·S7", "d2.s07", 3, "S4"])], context="D1·S2",
             no_angle=[{"section": "d2 · s3"}], gaps=[{"kind": "Missing", "sections": None, "issue": "x"}])
    assert m.angles[0].sections == ["D1.S7", "D2.S7", "S3", "S4"]
    assert m.context == ["D1.S2"]
    assert m.no_angle[0].section == "D2.S3" and m.no_angle[0].why == ""
    assert m.gaps[0].sections == [] and m.gaps[0].kind == "missing"


def test_by_document_and_unrelated_documents_parse():
    m = amap(client={"side": "Defendant", "party": " المدعى عليه ", "by_document": [
        {"document": "d2", "side": "plaintiff", "party": " المعترض "}, {"document": 3}]},
        unrelated_documents=[{"document": "Document 3", "why": " أخرى "}])
    assert m.client.side == "respondent" and m.client.party == "المدعى عليه"
    assert [(e.document, e.side, e.party) for e in m.client.by_document] == [("D2", "claimant", "المعترض"),
                                                                              ("D3", "unknown", "")]
    assert [(u.document, u.why) for u in m.unrelated_documents] == [("D3", "أخرى")]
    assert ClientInDocument(document=2).document == "D2"


def test_missing_new_fields_default_empty():
    out = F.good_two()
    out["client"].pop("by_document")
    out.pop("unrelated_documents")
    m = AngleMap.model_validate(out)
    assert m.client.by_document == [] and m.unrelated_documents == []
    m = amap(client={**F.CLIENT, "by_document": None}, unrelated_documents=None)
    assert m.client.by_document == [] and m.unrelated_documents == []


@pytest.mark.parametrize("raw, side", [
    ("Defendant", "respondent"), ("plaintiff", "claimant"), ("public-prosecution", "prosecution"),
    ("contract party", "contract_party"), ("judge", "unknown"), (None, "unknown"), ("Victim", "victim"),
    ("accused", "respondent"),
])
def test_sides_parse_leniently(raw, side):
    m = amap(client={"side": raw, "party": " x "}, angles=[F.angle(for_side=raw)])
    assert m.client.side == side and m.angles[0].for_side == side
    assert m.client.party == "x"


def test_unknown_strength_and_confidence_fall_back():
    m = amap(client={"side": "claimant", "party": "x", "confidence": "certain"},
             angles=[F.angle(strength="decisive"), F.angle(strength="WEAK")])
    assert m.client.confidence == "unclear"
    assert [a.strength for a in m.angles] == ["medium", "weak"]


def test_kind_aliases_and_an_unknown_kind_is_refused():
    assert amap(angles=[F.angle(kind="Attacking")]).angles[0].kind == "attack"
    with pytest.raises(ValidationError):
        amap(angles=[F.angle(kind="summary")])


def test_documents_and_cross_doc_angles():
    m = amap()
    assert [a.documents() for a in m.angles] == [[2, 1], [1], [1, 2]]
    assert m.cross_doc_angles() == 2
    m = amap(angles=[F.angle(sections=["D1.S3", "S4", "x"]), F.angle(sections=[])])
    assert m.cross_doc_angles() == 0  # bare / invalid refs name no document


def test_kind_mix_and_tracking_output():
    m = amap(angles=[F.angle("attack", ["D1.S5", "D2.S2"]), F.angle("attack"), F.angle("answer")],
             unrelated_documents=[{"document": "D2", "why": "x"}])
    assert m.kind_mix() == {"strengthen": 0, "attack": 2, "answer": 1}
    assert tuple(m.kind_mix()) == KINDS
    out = m.tracking_output()
    assert out == {"angles": 3, "kind_mix": {"strengthen": 0, "attack": 2, "answer": 1}, "cross_doc_angles": 1,
                   "context": 2, "gaps": 1, "no_angle": 0, "unrelated_documents": 1, "client_side": "respondent",
                   "client_confidence": "clear"}
    assert "التحويلات" not in str(out)  # counts only, never text


def test_the_schema_has_no_class_docstrings():
    """The tested angles-v2 schema: pydantic would put a docstring in what the model sees."""
    schema = AngleMap.model_json_schema()
    assert "description" not in schema
    for d in schema.get("$defs", {}).values():
        assert "description" not in d
    assert set(schema["$defs"]) == {"Angle", "Client", "ClientInDocument", "Gap", "NoAngle", "UnrelatedDocument"}
    # the v4 / v5 fields come last (the tested v2 order kept before them)
    assert list(schema["properties"]) == ["client", "angles", "context", "gaps", "no_angle", "unrelated_documents"]
    assert list(schema["$defs"]["Client"]["properties"]) == ["side", "party", "basis", "confidence", "by_document"]
    assert list(schema["$defs"]["Angle"]["properties"]) == [
        "for_side", "for_party", "kind", "title", "argument", "sections", "legal_question", "strength", "weakness",
        "ocr_suspect"]
    assert schema["$defs"]["Angle"]["properties"]["ocr_suspect"] == {"default": False, "title": "Ocr Suspect",
                                                                     "type": "boolean"}
    assert "ocr_suspect" not in schema["$defs"]["Angle"].get("required", [])


# ── the angle's ocr_suspect (angles-v5) ──────────────────────────────────────


@pytest.mark.parametrize("raw, flag", [
    (True, True), (False, False), (None, False), ("true", True), ("false", False), (1, True), (0, False),
])
def test_ocr_suspect_is_a_bool(raw, flag):
    m = amap(angles=[F.angle(ocr_suspect=raw)])
    assert m.angles[0].ocr_suspect is flag


def test_ocr_suspect_defaults_to_false_when_absent():
    m = amap()
    assert all(a.ocr_suspect is False for a in m.angles)
    assert all(a["ocr_suspect"] is False for a in m.model_dump()["angles"])


@pytest.mark.parametrize("raw", ["maybe", "ربما", [], {}])
def test_an_ocr_suspect_that_is_not_a_bool_is_refused(raw):
    with pytest.raises(ValidationError):
        amap(angles=[F.angle(ocr_suspect=raw)])


def test_new_result_has_every_stats_key():
    r = new_result("x")
    assert r.outcome == "error" and r.output is None and r.unplaced == [] and not r.ok
    assert r.message_id is None and r.item_ids == [] and r.excluded == {} and r.map_id is None
    for key in ("secs", "input_tokens", "output_tokens", "reasoning_tokens", "cached_tokens", "cost_usd", "model",
                "prompt_version", "attempts", "repair_used", "angle_count", "kind_mix", "client_confidence", "docs",
                "total_chars", "cross_doc_angles", "ocr_suspect_angles"):
        assert key in r.stats
    assert r.stats["ocr_suspect_angles"] == 0
    for outcome, ok in (("preview", True), ("stored", True), ("joined_group", False), ("skipped_fresh", False),
                        ("skipped_group_pending", False), ("failed_llm", False)):
        r.outcome = outcome
        assert r.ok is ok


# ── section refs of the group ────────────────────────────────────────────────


def test_section_core_and_doc_refs():
    assert REFS_TWO == frozenset({*(F.ref(1, s) for s in F.JUDGMENT.seqs), *(F.ref(2, s) for s in F.OBJECTION.seqs)})
    assert V.core_refs(TWO) == ["D1.S2", "D1.S3", "D1.S5", "D2.S2", "D2.S3"]  # document by document
    assert V.doc_refs(REFS_TWO) == {"D1", "D2"} and V.doc_refs(REFS_ONE) == {"D1"}
    # refs in any spelling, and objects carrying ``ref`` / ``value`` attributes
    assert V.core_refs([{"ref": "d1·s2", "value": "core"}, {"ref": "D1.S3", "value": "supporting"}]) == ["D1.S2"]
    seg = StoredSegment(seq=2, from_p=1, to_p=1, char_start=0, char_end=1, label="x", value="core")
    assert V.core_refs([type("S", (), {"ref": "D2.S2", "value": seg.value})()]) == ["D2.S2"]


# ── qualify_refs ─────────────────────────────────────────────────────────────


def test_bare_refs_are_qualified_in_a_group_of_one():
    m = AngleMap.model_validate(F.good_one(
        angles=[F.angle(sections=["S3", 4, "D1·S2"])], context=["S1", "S5"],
        gaps=[{"kind": "missing", "sections": [2], "issue": "x"}], no_angle=[{"section": "S6", "why": "توقيع"}]))
    q = V.qualify_refs(m, REFS_ONE)
    assert q.angles[0].sections == ["D1.S3", "D1.S4", "D1.S2"]
    assert q.context == ["D1.S1", "D1.S5"]
    assert q.gaps[0].sections == ["D1.S2"] and q.no_angle[0].section == "D1.S6"
    assert V.invalid_refs(q, REFS_ONE) == []
    assert m.angles[0].sections == ["S3", "S4", "D1.S2"]  # the input is left as it was


def test_bare_refs_stay_bare_with_several_documents():
    m = amap(angles=[F.angle(sections=["S3", "D2.S2"])], context=["S1"])
    q = V.qualify_refs(m, REFS_TWO)
    assert q is m
    assert V.invalid_refs(q, REFS_TWO) == ["angles[1].sections: S3", "context: S1"]


# ── invalid_refs / drop_invalid_refs ─────────────────────────────────────────


def test_a_clean_map_has_no_problems():
    assert V.invalid_refs(amap(), REFS_TWO) == []


def test_bad_refs_in_every_field_are_named():
    m = amap(
        angles=[F.angle(sections=["D1.S3", "D1.S41", "D3.S1"]), F.angle(sections=["D2.S2"]),
                F.angle(sections=["S2"])],
        context=["D1.S1", "D2.S99"],
        gaps=[{"kind": "missing", "sections": ["D1.S2"], "issue": "x"},
              {"kind": "contradiction", "sections": ["D2.S7"], "issue": "y"}],
        no_angle=[{"section": "D1.S6", "why": "z"}, {"section": "D1.S12", "why": "w"}, {"section": "x", "why": "v"}],
        client={**F.CLIENT, "by_document": [{"document": "D2", "side": "claimant"}, {"document": "D5"}]},
        unrelated_documents=[{"document": "D9", "why": "u"}],
    )
    assert V.invalid_refs(m, REFS_TWO) == [
        "angles[1].sections: D1.S41, D3.S1",
        "angles[3].sections: S2",
        "context: D2.S99",
        "gaps[2].sections: D2.S7",
        "no_angle sections: D1.S12, x",
        "client.by_document documents: D5",
        "unrelated_documents: D9",
    ]


def test_no_valid_refs_means_every_ref_is_bad():
    assert V.invalid_refs(amap(), frozenset()) != []


def test_drop_removes_and_counts_every_bad_ref():
    m = amap(
        angles=[F.angle(sections=["D1.S3", "D1.S41", "D1.S3", "D2.S4", "D1.S41"]), F.angle(sections=["S2"])],
        context=["D1.S1", "D2.S99", "D1.S1", "D2.S1"],
        gaps=[{"kind": "missing", "sections": ["D3.S1", "D2.S2"], "issue": "x"}],
        no_angle=[{"section": "D1.S6", "why": "a"}, {"section": "D1.S6", "why": "b"}, {"section": "D1.S77", "why": "c"}],
        client={**F.CLIENT, "by_document": [{"document": "D2", "side": "claimant", "party": "المعترض"},
                                            {"document": "D2", "side": "respondent"}, {"document": "D4"}]},
        unrelated_documents=[{"document": "D2", "why": "a"}, {"document": "D2", "why": "b"}, {"document": "D7"}],
    )
    clean, dropped = V.drop_invalid_refs(m, REFS_TWO)
    # D1.S41 ×2, S2, D2.S99, D3.S1, no_angle D1.S77, by_document D4, unrelated D7 (duplicates are not counted)
    assert dropped == 2 + 1 + 1 + 1 + 1 + 1 + 1
    assert [a.sections for a in clean.angles] == [["D1.S3", "D2.S4"], []]  # duplicates gone, empty angle kept
    assert clean.context == ["D1.S1", "D2.S1"]
    assert [g.sections for g in clean.gaps] == [["D2.S2"]]
    assert [(n.section, n.why) for n in clean.no_angle] == [("D1.S6", "a")]  # one entry per section, first kept
    assert [(e.document, e.side) for e in clean.client.by_document] == [("D2", "claimant")]
    assert [(u.document, u.why) for u in clean.unrelated_documents] == [("D2", "a")]
    assert V.invalid_refs(clean, REFS_TWO) == []
    assert m.angles[0].sections == ["D1.S3", "D1.S41", "D1.S3", "D2.S4", "D1.S41"]  # input untouched


def test_drop_on_a_clean_map_changes_nothing():
    m = amap()
    clean, dropped = V.drop_invalid_refs(m, REFS_TWO)
    assert dropped == 0 and clean.model_dump() == m.model_dump()


# ── coverage across documents ────────────────────────────────────────────────


def test_placed_refs_counts_angles_context_and_no_angle_not_gaps():
    m = amap(angles=[F.angle(sections=["D2.S3"])], context=["D1.S1"],
             gaps=[{"kind": "missing", "sections": ["D1.S2"], "issue": "x"}],
             no_angle=[{"section": "D1.S6", "why": "w"}])
    assert V.placed_refs(m) == {"D1.S1", "D2.S3", "D1.S6"}
    assert V.placed_refs(m.model_dump()) == {"D1.S1", "D2.S3", "D1.S6"}


@pytest.mark.parametrize("angles, context, no_angle, unplaced", [
    ([F.angle(sections=["D1.S2", "D1.S3", "D2.S2"])], ["D1.S5", "D2.S3"], [], []),
    ([F.angle(sections=["D1.S2", "D1.S3"])], ["D1.S5"], [], ["D2.S2", "D2.S3"]),
    ([], [], [], ["D1.S2", "D1.S3", "D1.S5", "D2.S2", "D2.S3"]),
    ([F.angle(sections=["D1.S4", "D2.S4"])], ["D1.S1", "D2.S1"], [{"section": "D2.S3", "why": "w"}],
     ["D1.S2", "D1.S3", "D1.S5", "D2.S2"]),  # non-core sections place nothing core
    ([F.angle(sections=["D2.S2", "D1.S5"])], [], [{"section": "D1.S3", "why": "w"}, {"section": "D1.S2", "why": "w"},
                                                   {"section": "D2.S3", "why": "w"}], []),
])
def test_unplaced_core_across_documents(angles, context, no_angle, unplaced):
    m = amap(angles=angles, context=context, no_angle=no_angle,
             gaps=[{"kind": "missing", "sections": ["D1.S2", "D2.S2"], "issue": "gaps place nothing"}])
    assert V.unplaced_core(m, TWO) == unplaced
    assert V.unplaced_core(m.model_dump(), TWO) == unplaced


def test_a_bare_ref_does_not_place_a_core_section():
    m = amap(angles=[F.angle(sections=["S2", "S3"])], context=["S5"])
    assert V.unplaced_core(m, ONE) == ["D1.S2", "D1.S3", "D1.S5"]
    assert V.unplaced_core(V.qualify_refs(m, REFS_ONE), ONE) == []


def test_unplaced_core_in_the_sections_order():
    rows = list(reversed(TWO))
    assert V.unplaced_core(amap(angles=[], context=[], no_angle=[]), rows) == [
        "D2.S3", "D2.S2", "D1.S5", "D1.S3", "D1.S2"]


def test_mark_unplaced():
    m = amap(no_angle=[{"section": "D1.S6", "why": "توقيع"}])
    marked = V.mark_unplaced(m, ["D1.S5", "D2.S3"])
    assert [(n.section, n.why) for n in marked.no_angle] == [
        ("D1.S6", "توقيع"), ("D1.S5", "لم يُصنَّف"), ("D2.S3", "لم يُصنَّف")]
    assert V.UNPLACED_WHY == "لم يُصنَّف"
    assert V.mark_unplaced(m, []) is m


# ── every angle names a section ──────────────────────────────────────────────


def test_sectionless_angles_are_those_with_no_section_of_the_group():
    m = amap(angles=[F.angle(sections=[]), F.angle(sections=["D1.S3"]), F.angle(sections=["D1.S41", "D9.S1"]),
                     F.angle(sections=["D1.S41", "D2.S2"])])
    assert V.sectionless_angles(m, REFS_TWO) == [1, 3]
    assert V.sectionless_angles(amap(), REFS_TWO) == []


# The failed run of 2026-11-27 (mapper_ab repro run7): no `sections` key on any
# angle, the refs written inside `argument` instead.
_ARG = "يثبت السداد بالتحويلات (D2·S2, D1·S3) ويقرّ به الخصم في D1.S5؛ وأما D3·S1 وD1·S41 فليسا في الملف."


def test_an_angle_with_no_section_takes_the_refs_its_own_text_writes():
    m = amap(angles=[F.angle(sections=[], argument=_ARG), F.angle(sections=["D1.S2"], argument=_ARG)])
    filled, n = V.fill_sections_from_text(m, REFS_TWO)
    assert n == 1
    assert filled.angles[0].sections == ["D2.S2", "D1.S3", "D1.S5"]  # first mention order, only the group's
    assert filled.angles[1].sections == ["D1.S2"]  # an angle that names a section is left alone
    assert filled.angles[0].argument == _ARG  # code never edits the text
    assert V.sectionless_angles(filled, REFS_TWO) == []


def test_refs_from_every_field_of_the_angle_in_order():
    m = amap(angles=[F.angle(sections=["D1.S41"], argument="لا مرجع هنا", title="وجه d2 s2",
                             legal_question="ما أثر D1-S03؟", weakness="يضعفه D1·S5 وD2·S2")])
    filled, n = V.fill_sections_from_text(m, REFS_TWO)
    assert n == 1 and filled.angles[0].sections == ["D2.S2", "D1.S3", "D1.S5"]


def test_an_angle_whose_text_names_no_section_of_the_group_is_left_for_the_retry():
    m = amap(angles=[F.angle(sections=[], argument="بلا مرجع"), F.angle(sections=[], argument="ID1·S3 وD9·S1")])
    same, n = V.fill_sections_from_text(m, REFS_TWO)
    assert n == 0 and same is m
    assert V.sectionless_angles(same, REFS_TWO) == [1, 2]
    whole = amap()
    assert V.fill_sections_from_text(whole, REFS_TWO) == (whole, 0)


# ── angles-v5: one question per angle ────────────────────────────────────────


def test_multi_question_angles():
    m = amap(angles=[
        F.angle(legal_question="ما حجية التحويل البنكي في إثبات السداد؟"),
        F.angle(legal_question="ما حجية التحويل؟ وهل يكفي وحده؟"),
        F.angle(legal_question="What is the rule? And the deadline?"),
        F.angle(legal_question="ما حجية التحويل? وما أثره؟"),
        F.angle(legal_question="ما حجية التحويل"),
    ])
    assert V.multi_question_angles(m) == [2, 3, 4]
    assert V.multi_question_angles(amap()) == []


@pytest.mark.parametrize("question", [
    "هل يجوز للمؤجر فسخ العقد وما أثر ذلك على الأجرة؟",          # the owner's example
    "هل يجوز فسخ العقد، وهل يستحق المؤجر التعويض؟",
    "هل يجوز فسخ العقد، وما أثر ذلك على الأجرة المستحقة؟",
    "ما حجية الشهادة ومدى كفايتها للإثبات؟",
    "ما حجية الشهادة، ومدى كفايتها للإثبات",                       # no «؟» at all
    "ما حجية الشهادة وما مدى كفايتها للإثبات؟",
    "ما الجهة المختصة بنظر الدعوى، ومن يتحمل الرسوم؟",
    "ما أثر الإقرار ومن له حق الرجوع عنه؟",
    "ما شروط قبول الدعوى وأي محكمة تختص بها؟",
    "متى يبدأ التقادم وكيف ينقطع؟",
    "متى يبدأ التقادم ومتى ينقطع؟",
    "ما المدة النظامية للاعتراض وكيفية احتسابها؟",
    "ما الأثر المترتب على عدم التبليغ وما إذا كان يبطل الحكم؟",
    "ما حكم البيع، ما الأثر على المشتري؟",                         # «،» without «و»
    "هل يسقط الحق بالتقادم أم أن المطالبة الودية تقطع المدة؟",   # «أم» opening a question
    "هل يُعَدُّ الإخلال جوهرياً، وَهَلْ يبرر الفسخ؟",               # diacritics
    "هل يجوز ذلك و هل يلزم التعويض؟",                               # «و» written apart
    "What is the rule? And the deadline?",                          # two "?"
])
def test_a_compound_question_is_more_than_one(question):
    assert V.compound_question(question) is True
    assert V.multi_question_angles(amap(angles=[F.angle(), F.angle(legal_question=question)])) == [2]


@pytest.mark.parametrize("question", [
    "ما أثر الفسخ على الأجرة المستحقة؟",                       # «ما» opening, «على» inside: ONE question
    "ما حجية التحويل البنكي في إثبات السداد؟",
    "ما حكم الشرط الجزائي وما في حكمه؟",                        # «وما في حكمه»: a phrase
    "هل يجوز الفسخ، ما لم يتفق الطرفان على خلافه؟",             # «ما لم»: a condition
    "ما حكم الاحتجاج بالمادة الخامسة وما بعدها؟",
    "هل يبدأ الميعاد من تاريخ التبليغ ومن تاريخ العلم؟",         # «ومن» the preposition
    "ما حكم الدعوى ومن ثم آثارها؟",
    "هل يبدأ ميعاد الاعتراض من تاريخ التبليغ أم من تاريخ العلم؟",  # «أم» between two answers
    "هل العقد بيع أم إيجار؟",
    "هل يجوز ذلك أم لا؟",
    "وهل يجوز للمستأجر الامتناع عن الأجرة؟",                      # the opening itself
    "ما مدى حجية الصورة الضوئية للمستند في الإثبات؟",
    "ما حجية التحويل",
    "",
])
def test_a_single_question_is_one(question):
    assert V.compound_question(question) is False
    assert V.multi_question_angles(amap(angles=[F.angle(legal_question=question)])) == []


# ── angles-v5: a contradiction is two DIFFERENT values ───────────────────────


@pytest.mark.parametrize("issue, values", [
    ("التاريخ في D1·S3 هو 1445/05/12 وفي D2·S4 هو ١٤٤٥/٠٥/١٢", ["1445/05/12", "1445/05/12"]),
    ("المبلغ 5,000 ريال في العقد و٥٠٠٠ ريال في الفاتورة", ["5000", "5000"]),
    ("P012–P015 تذكر 3000 بينما D2.S2 يذكر 4000", ["3000", "4000"]),
    ("الحكم والاعتراض يختلفان في السداد", []),
    ("القيمة ١٢٫٥ مقابل 12.5", ["12.5", "12.5"]),
    ("المبلغ 5000.00 مقابل 1,250.50 و٣٤٬٧٥٨٫٧٥", ["5000", "1250.5", "34758.75"]),  # a decimal's zeros dropped
])
def test_compared_values(issue, values):
    assert V.compared_values(issue) == values


def _contradiction(issue: str, kind: str = "contradiction") -> dict:
    return {"kind": kind, "sections": ["D1.S5", "D2.S2"], "issue": issue, "ocr_suspect": False}


@pytest.mark.parametrize("issue, equal", [
    ("التاريخ في D1·S3 هو 1445/05/12 وفي D2·S4 هو ١٤٤٥/٠٥/١٢", True),   # the same date, two digit sets
    ("المبلغ 5,000 ريال في العقد و٥٠٠٠ ريال في الفاتورة", True),         # the same amount
    ("المبلغ 5000 في العقد و6000 في الفاتورة", False),                    # a real contradiction
    ("البند 5 في العقد يحدد سنة والبند 5 في الملحق يحدد سنتين", False),    # clause numbers are not figures
    ("المبلغ 5000 في البند 3 و5000 في البند 7", True),                    # clause numbers do not save it
    ("المبلغ 5000 فقط", False),                                            # one value
    ("الحكم والاعتراض يختلفان في السداد", False),                         # no value
    ("D1·S5 و D2·S5 يختلفان", False),                                      # refs are not values
    # the review's case: a third number (an invoice number) no longer lets equal amounts through
    ("إجمالي الفاتورة رقم 10234 هو 34,758.75 ريال بينما المطالب به 34,758.75 ريال", True),
    ("إجمالي الفاتورة رقم ١٠٢٣٤ هو ٣٤٬٧٥٨٫٧٥ ريال بينما المطالب به 34758.75 ريال", True),  # Arabic-Indic digits
    ("المبلغ في العقد 5000.00 ريال وفي الحكم ٥٬٠٠٠ ريال", True),          # the same number, two spellings
    ("المبلغ في الحكم 34,758.75 وفي الاعتراض 34,758.75 وفي المطالبة 43,758.75", False),  # a different amount
    ("المبلغ 5000 في العقد و5000 في الحكم و50000 في الاعتراض", False),     # 5000 vs 50000: the contradiction
    # equal amounts, two DIFFERENT dates: the dates may be the contradiction
    ("مبلغ 34,758.75 في الفاتورة المؤرخة 1445/05/12 و34,758.75 في الإيصال المؤرخ 1445/06/01", False),
    ("المبلغ 5000 في العقد و6000 في الفاتورة رقم 1023", False),           # amounts differ (same shape as the number)
])
def test_equal_value_contradictions(issue, equal):
    m = amap(gaps=[_contradiction(issue)])
    assert V.equal_valued(issue) is equal
    assert V.equal_value_contradictions(m) == ([1] if equal else [])


def test_only_contradiction_gaps_are_judged():
    same = "المبلغ 5000 في العقد و5000 في الفاتورة"
    m = amap(gaps=[_contradiction(same, "missing"), _contradiction("x"), _contradiction(same),
                   _contradiction(same, "unanswered")])
    assert V.equal_value_contradictions(m) == [3]


def test_equal_value_gaps_are_dropped_in_code():
    same = "المبلغ ٣٤٬٧٥٨٫٧٥ في الفاتورة رقم 10234 و34,758.75 في الحكم"
    m = amap(gaps=[_contradiction(same), _contradiction("المبلغ 5000 في العقد و6000 في الفاتورة"),
                   _contradiction(same, "missing"), _contradiction(same)])
    kept, dropped = V.drop_equal_value_gaps(m)
    assert dropped == 2
    assert [(g.kind, g.issue) for g in kept.gaps] == [
        ("contradiction", "المبلغ 5000 في العقد و6000 في الفاتورة"), ("missing", same)]
    assert len(m.gaps) == 4  # the input is left as it was


def test_no_equal_value_gap_changes_nothing():
    m = amap()
    same, dropped = V.drop_equal_value_gaps(m)
    assert dropped == 0 and same is m


# ── OCR gaps: a reading problem, one per document ────────────────────────────


def _gap(kind: str, sections: list[str], issue: str, ocr: bool = True) -> dict:
    return {"kind": kind, "sections": sections, "issue": issue, "ocr_suspect": ocr}


def test_an_ocr_contradiction_is_relabelled_missing():
    m = amap(gaps=[_gap("contradiction", ["D1.S5", "D2.S2"], "قد يكون الرقم ٣ مقروءاً خطأ"),
                   _gap("contradiction", ["D1.S3"], "الحكم والاعتراض يختلفان في السداد", ocr=False)])
    out, merged = V.merge_ocr_gaps(m)
    assert merged == 0
    assert [(g.kind, g.ocr_suspect) for g in out.gaps] == [("missing", True), ("contradiction", False)]
    assert m.gaps[0].kind == "contradiction"  # the input is left as it was


def test_one_ocr_gap_per_document_the_rest_merged_into_the_first():
    m = amap(gaps=[
        _gap("missing", ["D1.S2"], "نص مشوه في D1·S2."),
        _gap("missing", ["D2.S2"], "نص مشوه في D2·S2"),
        _gap("contradiction", ["D1.S3"], "الحكم والاعتراض يختلفان في السداد", ocr=False),
        _gap("contradiction", ["D1.S5", "D2.S3"], "تاريخ غير مقروء في D1·S5"),
        _gap("missing", ["D1.S2", "D1.S4"], "تكرار حلقي في D1·S4"),
        _gap("unanswered", ["D2.S3"], "رقم غير واضح في D2·S3"),
        _gap("missing", ["D1.S5"], "تاريخ غير مقروء في D1·S5"),  # the same words: not repeated
    ])
    out, merged = V.merge_ocr_gaps(m)
    assert merged == 4
    assert [g.model_dump() for g in out.gaps] == [
        _gap("missing", ["D1.S2", "D1.S5", "D2.S3", "D1.S4"],
             "نص مشوه في D1·S2؛ تاريخ غير مقروء في D1·S5؛ تكرار حلقي في D1·S4"),
        _gap("missing", ["D2.S2", "D2.S3"], "نص مشوه في D2·S2؛ رقم غير واضح في D2·S3"),
        _gap("contradiction", ["D1.S3"], "الحكم والاعتراض يختلفان في السداد", ocr=False),
    ]
    assert V.OCR_ISSUE_JOIN == "؛ "


def test_ocr_gaps_without_sections_share_one():
    m = amap(gaps=[_gap("missing", [], "ملاحظة استخراج"), _gap("missing", [], "نص متكرر"),
                   _gap("missing", ["D1.S2"], "نص مشوه")])
    out, merged = V.merge_ocr_gaps(m)
    assert merged == 1
    assert [(g.sections, g.issue) for g in out.gaps] == [([], "ملاحظة استخراج؛ نص متكرر"), (["D1.S2"], "نص مشوه")]


def test_no_ocr_gap_changes_nothing():
    m = amap(gaps=[_gap("contradiction", ["D1.S5"], "x", ocr=False), _gap("missing", ["D1.S2"], "y")])
    same, merged = V.merge_ocr_gaps(m)
    assert merged == 0 and same is m


def test_drop_sectionless_angles_after_the_refs_are_cleaned():
    m, dropped = V.drop_invalid_refs(
        amap(angles=[F.angle(sections=[]), F.angle(sections=["D1.S3"]), F.angle(sections=["D1.S41"])]), REFS_TWO)
    assert dropped == 1
    kept, empty = V.drop_sectionless_angles(m)
    assert empty == 2 and [a.sections for a in kept.angles] == [["D1.S3"]]
    same, none = V.drop_sectionless_angles(kept)
    assert none == 0 and same is kept
    nothing, all_gone = V.drop_sectionless_angles(amap(angles=[F.angle(sections=[])]))
    assert all_gone == 1 and nothing.angles == []
