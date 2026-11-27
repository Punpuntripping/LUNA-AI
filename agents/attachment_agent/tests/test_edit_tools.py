"""The edit tools STAGE only: the evidence guard, each tool's checks and refusals,
promote-only, thread / gap labels across two analyses, ref conversion, text-fix
decoding, the free plan, and the edit list for undo. Nothing here writes."""
from __future__ import annotations

import asyncio
import random

import pytest

from agents.attachment_agent import edit_tools as T
from agents.attachment_agent.edit_tools import (
    NO_THREADS,
    REFUSED_EVIDENCE,
    ClientFields,
    GapFields,
    ThreadFields,
    evidence_ok,
)
from agents.memory.angle_mapper import models as amodels
from agents.memory.angle_mapper.edits import R_FAKE
from backend.app.services import masking_service
from shared.attachments import segment_overrides, text_corrections
from shared.attachments.text_corrections import REASON_CROSSES_JOIN, REASON_INSIDE_WORD
from shared.privacy.codec import PrivacyCodec

from .fakes import (
    CONVO,
    LEASE,
    PAGE_STARTS,
    USER,
    attachment,
    edit_deps,
    lease_sections,
    make_db,
    segment_map,
    span_of,
    two_map_db,
)


@pytest.fixture(autouse=True)
def _no_backoff():
    text_corrections.reset_backoff()
    segment_overrides.reset_backoff()
    yield
    text_corrections.reset_backoff()
    segment_overrides.reset_backoff()


def _lease_only(**kw):
    return make_db([attachment("i1", "عقد الإيجار", LEASE, wi_seq=1)],
                   maps=[segment_map("i1", LEASE, lease_sections())], pages={"i1": PAGE_STARTS}, **kw)


# -- the evidence guard -------------------------------------------------------------


def test_evidence_guard_folds_letters_and_spaces():
    deps = edit_deps(_lease_only(), "لو سمحت اجعل «قسم التواصل»   جوهريًا")
    assert evidence_ok(deps, "اجعل «قسم التواصل» جوهريًا")
    assert evidence_ok(deps, "  إجعل قسم   التواصل ")          # alef form, spaces, edge quotes
    assert not evidence_ok(deps, "«قسم التواصل»")                # in the message, but asks nothing
    assert not evidence_ok(deps, "احذف الخيط الأول")             # asks, but not in the message
    assert not evidence_ok(deps, "جوهريًا")                      # one word
    assert not evidence_ok(deps, "")


@pytest.mark.parametrize("message", [
    "الاسم الصحيح محمود مو محمد",
    "القسم الخاص بالأسانيد مهم",
    "احنا المدعى عليه مو المدعي",
    "ماعجبني التحليل اعيد تحليله",
    "ما عجبني التحليل",
    "تراجع عن آخر تعديل",
    "احذف الثغرة الثانية",
    "خل الخيط الأول قوته عالية",
    "وصحّح اسم المؤجر",
    "رقِّ قسم التواصل",
])
def test_the_owners_requests_pass_the_guard(message):
    deps = edit_deps(_lease_only(), f"لو سمحت {message}، شكرًا")
    assert evidence_ok(deps, message)


@pytest.mark.parametrize("message,evidence", [
    ("وش قوة الخيط الثاني؟", "الخيط الثاني"),
    ("وش قوة الخيط الثاني؟", "وش قوة الخيط الثاني"),
    ("هل حكم عليه بالإعدام؟", "حكم عليه بالإعدام"),        # «اعد» only as a whole word
    ("هل ذكر الإهمال في العقد؟", "ذكر الإهمال"),             # «اهم» only as a whole word
    ("من هو كاتب العدل؟", "كاتب العدل"),
    ("وين قال إن المدة سنة؟", "المدة سنة"),
])
def test_a_question_never_passes_the_guard(message, evidence):
    deps = edit_deps(_lease_only(), message)
    assert not evidence_ok(deps, evidence)
    assert T.stage_fix_text(deps, "F1", "محمد", "محمود", evidence) == REFUSED_EVIDENCE
    assert "needs_clarification" in REFUSED_EVIDENCE and "explicitly" in REFUSED_EVIDENCE


def test_refused_evidence_stages_nothing():
    deps = edit_deps(_lease_only(), "ما مدة العقد؟")
    for out in (
        T.stage_promote(deps, ["F1·S6"], "اجعله جوهري"),
        T.stage_fix_text(deps, "F1", "محمد", "محمود", "صحح الاسم"),
        T.stage_reanalysis(deps, "أعد التحليل"),
    ):
        assert out == REFUSED_EVIDENCE
    assert deps.staged_edits == []


def test_evidence_with_a_masked_number_matches_in_the_masked_space():
    codec = PrivacyCodec(rng=random.Random(3))
    token = masking_service.set_active_codec(codec)
    try:
        deps = edit_deps(_lease_only(), "رقم السجل الصحيح 1010234568 وليس 1010234567")
        fake_new = codec.real_to_fake["1010234568"]
        assert "1010234568" not in deps.user_message
        assert evidence_ok(deps, f"رقم السجل الصحيح {fake_new}")
        # Arabic-Indic digits of the same fake fold to it
        indic = fake_new.translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))
        assert evidence_ok(deps, f"الصحيح {indic}")
        # the real number never reached the model: it is not the user's words there
        assert not evidence_ok(deps, "رقم السجل الصحيح 1010234568")
    finally:
        masking_service.reset_active_codec(token)


# -- promote_sections ------------------------------------------------------------------


def test_promote_stages_the_sections_original_range():
    deps = edit_deps(_lease_only(), "خل قسم التواصل جوهري")
    out = T.stage_promote(deps, ["F1·S6", "F1·S6"], "قسم التواصل جوهري")
    assert out.startswith("Staged: F1·S6 → core")
    (e,) = deps.staged_edits
    start, end = span_of(LEASE, "للتواصل: lessor.office@mail.com\nالتوقيع: المؤجر")
    assert e.kind == "promote" and (e.args["char_start"], e.args["char_end"]) == (start, end)
    assert e.args["file_id"] == "i1" and e.args["what"] == "التواصل والتوقيع"
    assert e.evidence == "قسم التواصل جوهري"


def test_promote_of_a_core_section_is_a_no_op():
    deps = edit_deps(_lease_only(), "اجعل مدة العقد جوهري")
    out = T.stage_promote(deps, ["F1·S2"], "مدة العقد جوهري")
    assert "Already core" in out and "F1·S2" in out and deps.staged_edits == []
    assert "Unknown section refs: F1·S99" in T.stage_promote(deps, ["F1·S99"], "مدة العقد جوهري")


def test_a_promoted_section_loads_as_core_and_is_not_staged_again():
    start, end = span_of(LEASE, "للتواصل: lessor.office@mail.com\nالتوقيع: المؤجر")
    db = _lease_only()
    db.tables["attachment_segment_overrides"] = [
        {"id": "o1", "file_id": "i1", "user_id": USER, "char_start": start, "char_end": end, "value": "core",
         "created_at": "2026-11-27T09:00:00", "reverted_at": None},
    ]
    deps = edit_deps(db, "قسم التواصل جوهري")
    assert deps.files["F1"].section(6).value == "core"
    assert "Already core" in T.stage_promote(deps, ["F1·S6"], "قسم التواصل جوهري")


# -- threads across two analyses ----------------------------------------------------


def test_two_analyses_are_labelled_and_a_bare_label_must_be_unique():
    deps = edit_deps(two_map_db(), "x")
    assert [t.label for t in deps.maps] == ["M1", "M2"]
    tmap, seq, why = T.resolve_thread(deps, "A1")
    assert tmap is None and "M1·A1 or M2·A1" in why
    tmap, seq, why = T.resolve_thread(deps, "M2·A1")
    assert (tmap.map_id, seq, why) == ("map2", 1, "")
    tmap, seq, _ = T.resolve_thread(deps, "a2")          # only map1 has A2
    assert (tmap.map_id, seq) == ("map1", 2)
    assert "No thread" in T.resolve_thread(deps, "M2·A2")[2]
    assert "Unknown thread" in T.resolve_thread(deps, "الخيط الثاني")[2]
    tmap, index, _ = T.resolve_gap(deps, "G2")
    assert (tmap.map_id, index) == ("map1", 2)


def test_rendered_threads_carry_the_labels():
    from agents.attachment_agent.context import load_context
    from agents.attachment_agent.models import AttachmentAgentCall

    loaded = load_context(two_map_db(), AttachmentAgentCall(user_id=USER, conversation_id=CONVO, user_message="x"))
    first, second = loaded.threads
    assert first.startswith("## M1 — Threads «الخيوط»") and "M1·A1 · «منع التأجير من الباطن»" in first
    assert "M1·G2 · contradiction: تاريخ البداية غير واضح" in first
    assert "M2·A1 · «طلب الإخلاء»" in second


def test_edit_thread_converts_refs_to_the_maps_documents():
    # the lease is document 2 of map1 (i9 is a member not given here)
    deps = edit_deps(two_map_db(lease_map_members=("i9", "i1")), "أضف قسم المدة للخيط الأول وخله قوي")
    out = T.stage_edit_thread(
        deps, "M1·A1", ThreadFields(strength="strong", add_sections=["F1·S2"]), "خله قوي",
    )
    assert out.startswith("Staged: change strength, add_sections of M1·A1")
    (e,) = deps.staged_edits
    assert e.kind == "edit_thread" and e.args["map_id"] == "map1" and e.args["seq"] == 1
    assert e.args["fields"] == {"strength": "strong", "add_sections": ["D2.S2"]}
    assert e.args["title"] == "منع التأجير من الباطن"


def test_edit_thread_refusals():
    deps = edit_deps(two_map_db(), "عدل الخيط الأول")
    ev = "عدل الخيط الأول"
    assert "not in this analysis" in T.stage_edit_thread(deps, "M1·A1", ThreadFields(add_sections=["F2·S1"]), ev)
    assert "Name at least one field" in T.stage_edit_thread(deps, "M1·A1", ThreadFields(), ev)
    assert "already has these values" in T.stage_edit_thread(deps, "M1·A1", ThreadFields(strength="medium"), ev)
    assert "not in it now" in T.stage_edit_thread(deps, "M1·A1", ThreadFields(remove_sections=["F1·S2"]), ev)
    assert "at least one section" in T.stage_edit_thread(deps, "M1·A1", ThreadFields(remove_sections=["F1·S5"]), ev)
    assert "cannot be empty" in T.stage_edit_thread(deps, "M1·A1", ThreadFields(title="  "), ev)
    assert deps.staged_edits == []


def test_thread_field_values_are_the_mappers_values():
    assert set(ThreadFields.model_fields["strength"].annotation.__args__[0].__args__) == set(amodels.STRENGTHS)
    assert set(ThreadFields.model_fields["kind"].annotation.__args__[0].__args__) == set(amodels.KINDS)
    assert set(T.Side.__args__) == set(amodels.CLIENT_SIDES)
    assert set(GapFields.model_fields["kind"].annotation.__args__[0].__args__) == set(amodels.GAP_KINDS)


def test_add_thread_infers_the_analysis_from_its_sections():
    deps = edit_deps(two_map_db(), "أضف خيط عن مدة العقد")
    ev = "أضف خيط عن مدة العقد"
    assert "needs a kind" in T.stage_add_thread(deps, ThreadFields(title="مدة العقد"), ev)
    assert "at least one section" in T.stage_add_thread(deps, ThreadFields(title="مدة العقد", kind="attack"), ev)
    assert "different analyses" in T.stage_add_thread(
        deps, ThreadFields(title="مدة", kind="attack", sections=["F1·S2", "F2·S1"]), ev)
    out = T.stage_add_thread(deps, ThreadFields(title="مدة العقد", kind="attack", sections=["F1·S2"]), ev)
    assert out.startswith("Staged: new thread «مدة العقد»")
    (e,) = deps.staged_edits
    assert e.args["map_id"] == "map1" and e.args["fields"]["sections"] == ["D1.S2"]


def test_remove_thread_warns_when_fewer_than_two_are_left():
    deps = edit_deps(two_map_db(), "احذف الخيط الأول والثاني")
    ev = "احذف الخيط الأول والثاني"
    out = T.stage_remove_thread(deps, "M1·A1", ev)        # map1 has two: one is left
    assert out.startswith("Staged: remove M1·A1") and "Fewer than two" in out
    assert "already staged" in T.stage_remove_thread(deps, "M1·A1", ev)
    assert "Fewer than two" in T.stage_remove_thread(deps, "M1·A2", ev)
    assert [e.args["seq"] for e in deps.staged_edits] == [1, 2]


# -- gaps and the client ----------------------------------------------------------------


def test_gap_tools_stage_with_their_numbers():
    deps = edit_deps(two_map_db(), "عدل الثغرة الأولى واحذف الثانية وأضف ثغرة")
    ev = "عدل الثغرة الأولى واحذف الثانية وأضف ثغرة"
    assert T.stage_edit_gap(deps, "G1", GapFields(kind="unanswered"), ev).startswith("Staged")
    assert T.stage_remove_gap(deps, "G2", ev).startswith("Staged")
    assert "say which one" in T.stage_add_gap(deps, GapFields(issue="لا يوجد توقيع"), ev)
    assert T.stage_add_gap(deps, GapFields(issue="لا يوجد توقيع"), ev, analysis="M2").startswith("Staged")
    assert T.stage_add_gap(deps, GapFields(issue="الإخلاء", sections=["F2·S2"]), ev).startswith("Staged")
    kinds = [(e.kind, e.args.get("map_id"), e.args.get("index")) for e in deps.staged_edits]
    assert kinds == [("edit_gap", "map1", 1), ("remove_gap", "map1", 2), ("add_gap", "map2", None),
                     ("add_gap", "map2", None)]
    assert deps.staged_edits[-1].args["fields"]["sections"] == ["D1.S2"]
    assert "No gap" in T.stage_edit_gap(deps, "M1·G9", GapFields(kind="missing"), ev)


def test_edit_client_is_a_specified_change():
    deps = edit_deps(two_map_db(), "احنا المدعى عليه مو المدعي")
    ev = "احنا المدعى عليه مو المدعي"
    assert "say which one" in T.stage_edit_client(deps, ClientFields(side="respondent"), ev)
    out = T.stage_edit_client(deps, ClientFields(side="respondent"), ev, analysis="F1")
    assert out.startswith("Staged: client side")
    assert deps.staged_edits[0].args == {"map_id": "map1", "fields": {"side": "respondent"}}
    assert "already says this" in T.stage_edit_client(deps, ClientFields(side="claimant"), ev, analysis="M1")


# -- the free plan: no analysis -----------------------------------------------------------


def test_free_plan_threads_refused_but_text_and_promotions_work():
    deps = edit_deps(_lease_only(), "صحح محمد إلى محمود واجعل قسم التواصل جوهري وعدل الخيط وأعد التحليل")
    ev = "صحح محمد إلى محمود"
    assert deps.maps == []
    assert T.stage_edit_thread(deps, "A1", ThreadFields(strength="strong"), ev) == NO_THREADS
    assert T.stage_remove_thread(deps, "A1", ev) == NO_THREADS
    assert T.stage_add_thread(deps, ThreadFields(title="x", kind="attack", sections=["F1·S2"]), ev) == NO_THREADS
    assert T.stage_add_gap(deps, GapFields(issue="x"), ev) == NO_THREADS
    assert T.stage_edit_gap(deps, "G1", GapFields(issue="x"), ev) == NO_THREADS
    assert T.stage_edit_client(deps, ClientFields(side="respondent"), ev) == NO_THREADS
    assert T.stage_reanalysis(deps, "صحح محمد إلى محمود") == NO_THREADS
    assert deps.staged_edits == []
    assert T.stage_fix_text(deps, "F1", "محمد", "محمود", ev).startswith("Staged")
    assert T.stage_promote(deps, ["F1·S6"], "اجعل قسم التواصل جوهري").startswith("Staged")
    assert [e.kind for e in deps.staged_edits] == ["fix_text", "promote"]


# -- fix_text ------------------------------------------------------------------------------


def test_fix_text_stages_the_real_text_and_its_count():
    deps = edit_deps(_lease_only(), "الاسم الصحيح محمود مو محمد")
    out = T.stage_fix_text(deps, "F1", "محمد", "محمود", "الاسم الصحيح محمود")
    assert out == "Staged: «محمد» → «محمود» in F1, 1 occurrence (applied after you finish)."
    (e,) = deps.staged_edits
    assert e.args == {"item_id": "i1", "file_id": "i1", "title": "عقد الإيجار", "wrong": "محمد",
                      "right": "محمود", "count": 1}
    # a second fix of the same file sees the first
    assert "لم يُعثر" in T.stage_fix_text(deps, "F1", "محمد", "أحمد", "الاسم الصحيح محمود")


def test_fix_text_refusals_name_the_reason():
    deps = edit_deps(_lease_only(), "صحح حمد")
    assert REASON_INSIDE_WORD in T.stage_fix_text(deps, "F1", "حمد", "حامد", "صحح حمد")
    assert "Unknown file" in T.stage_fix_text(deps, "F7", "حمد", "حامد", "صحح حمد")
    # without tashkeel it is not found letter for letter: the exact form is suggested
    out = T.stage_fix_text(deps, "F1", "يدفع", "يسدد", "صحح حمد")
    assert "The file writes it «يَدْفَع»" in out
    assert deps.staged_edits == []


def test_fix_text_allows_a_letterhead_outside_the_sections():
    # a page letterhead between sections (a joined section leaves it out) holds the name too
    text = "مكتب سالم للمحاماة\nالبند الأول: سالم يلتزم\nمكتب سالم للمحاماة\nالبند الثاني: سالم يدفع"
    first, second = span_of(text, "البند الأول: سالم يلتزم"), span_of(text, "البند الثاني: سالم يدفع")
    db = make_db([attachment("i1", "عقد", text)], maps=[segment_map("i1", text, [
        {"seq": 1, "char_start": first[0], "char_end": first[1], "label": "x", "value": "core"},
        {"seq": 2, "char_start": second[0], "char_end": second[1], "label": "y", "value": "core"}])])
    deps = edit_deps(db, "صحح سالم إلى سليم")
    assert T.stage_fix_text(deps, "F1", "سالم", "سليم", "صحح سالم إلى سليم").startswith(
        "Staged: «سالم» → «سليم» in F1, 4 occurrences")


def test_fix_text_refuses_a_match_straddling_a_section_edge():
    text = "البند الأول: شركة النور للتجارة"
    cut = text.index(" للتجارة")
    db = make_db([attachment("i1", "عقد", text)], maps=[segment_map("i1", text, [
        {"seq": 1, "char_start": 0, "char_end": cut, "label": "x", "value": "core"},
        {"seq": 2, "char_start": cut, "char_end": len(text), "label": "y", "value": "core"}])])
    deps = edit_deps(db, "صحح اسم الشركة")
    out = T.stage_fix_text(deps, "F1", "النور للتجارة", "النور للمقاولات", "صحح اسم الشركة")
    assert REASON_CROSSES_JOIN in out and deps.staged_edits == []


def test_fix_text_section_edges_follow_earlier_fixes():
    # a stored fix lengthens S1; S2's edge moves with it, so a word in S2 is not "across" it
    text = "قال سالم.\nثم دفع خالد."
    cut = text.index("ثم")
    db = make_db([attachment("i1", "عقد", text)], maps=[segment_map("i1", text, [
        {"seq": 1, "char_start": 0, "char_end": cut, "label": "x", "value": "core"},
        {"seq": 2, "char_start": cut, "char_end": len(text), "label": "y", "value": "core"}])])
    db.tables["attachment_text_corrections"] = [
        {"id": "t1", "file_id": "i1", "user_id": USER, "wrong_text": "سالم", "right_text": "سالم بن عبدالله",
         "occurrences": 1, "created_at": "2026-11-27T10:00:00", "reverted_at": None}]
    deps = edit_deps(db, "صحح خالد إلى خليل")
    f = deps.files["F1"]
    assert f.real_section_bounds() == [(0, cut + 11), (cut + 11, len(text) + 11)]
    assert T.stage_fix_text(deps, "F1", "دفع خالد", "دفع خليل", "صحح خالد إلى خليل").startswith("Staged")


def test_fix_text_decodes_masked_numbers_and_refuses_a_damaged_fake():
    codec = PrivacyCodec(rng=random.Random(5))
    token = masking_service.set_active_codec(codec)
    try:
        deps = edit_deps(_lease_only(), "رقم السجل الصحيح 1010234568 وليس 1010234567")
        old, new = codec.real_to_fake["1010234567"], codec.real_to_fake["1010234568"]
        assert old in deps.files["F1"].view.text   # the model read the fake
        out = T.stage_fix_text(deps, "F1", old, new, f"رقم السجل الصحيح {new}")
        assert out.startswith("Staged") and "1010234567" not in out
        (e,) = deps.staged_edits
        assert (e.args["wrong"], e.args["right"], e.args["count"]) == ("1010234567", "1010234568", 1)
        damaged = old[:-1] + str((int(old[-1]) + 1) % 10)
        deps2 = edit_deps(_lease_only(), "رقم السجل الصحيح 1010234568 وليس 1010234567")
        assert T.stage_fix_text(deps2, "F1", old, damaged, "رقم السجل الصحيح") == R_FAKE
        assert deps2.staged_edits == []
    finally:
        masking_service.reset_active_codec(token)


# -- request_reanalysis -----------------------------------------------------------------


def test_reanalysis_is_staged_once_per_message_with_every_map():
    message = "ماعجبني التحليل، الخيوط كلها ضد موكلي، اعيد تحليله"
    deps = edit_deps(two_map_db(), message)
    out = T.stage_reanalysis(deps, "اعيد تحليله")
    assert out.startswith("Staged: re-analysis")
    assert "already staged" in T.stage_reanalysis(deps, "اعيد تحليله")
    (e,) = deps.staged_edits
    assert e.args["critique"] == message          # the user's whole message, never a paraphrase
    assert e.args["targets"] == [{"map_id": "map1", "item_ids": ["i1"]}, {"map_id": "map2", "item_ids": ["i2"]}]


# -- duplicates, refs not given here -----------------------------------------------------


def test_the_same_change_is_staged_once():
    deps = edit_deps(two_map_db(), "خل الخيط الأول قوي وصحح محمد إلى محمود وأضف ثغرة عن التوقيع")
    ev = "خل الخيط الأول قوي"
    assert T.stage_edit_thread(deps, "M1·A1", ThreadFields(strength="strong"), ev).startswith("Staged")
    assert T.stage_edit_thread(deps, "M1·A1", ThreadFields(strength="strong"), ev) == T.ALREADY_STAGED
    assert T.stage_edit_thread(deps, "M1·A1", ThreadFields(strength="weak"), ev).startswith("Staged")  # differs
    ev2 = "وصحح محمد إلى محمود"
    assert T.stage_fix_text(deps, "F1", "محمد", "محمود", ev2).startswith("Staged")
    assert T.stage_fix_text(deps, "F1", "محمد", "محمود", ev2) == T.ALREADY_STAGED
    ev3 = "وأضف ثغرة عن التوقيع"
    assert T.stage_add_gap(deps, GapFields(issue="لا توقيع"), ev3, analysis="M2").startswith("Staged")
    assert T.stage_add_gap(deps, GapFields(issue="لا توقيع"), ev3, analysis="M2") == T.ALREADY_STAGED
    assert T.stage_edit_client(deps, ClientFields(side="respondent"), ev, analysis="M1").startswith("Staged")
    assert T.stage_edit_client(deps, ClientFields(side="respondent"), ev, analysis="M1") == T.ALREADY_STAGED
    assert [e.kind for e in deps.staged_edits] == ["edit_thread", "edit_thread", "fix_text", "add_gap", "edit_client"]


def test_a_full_sections_list_keeps_refs_into_files_not_given_here():
    # map1 = [i9 (not given here) D1, i1 = F1 D2]; A1 rests on D1.S4 (unseen) + D2.S5
    db = two_map_db(lease_map_members=("i9", "i1"))
    angle = next(a for a in db.tables["attachment_angles"] if a["map_id"] == "map1" and a["seq"] == 1)
    angle["sections"] = ["D1.S4", "D2.S5"]
    deps = edit_deps(db, "غير أقسام الخيط الأول إلى قسم المدة")
    out = T.stage_edit_thread(deps, "M1·A1", ThreadFields(sections=["F1·S2"]), "غير أقسام الخيط الأول")
    assert out.startswith("Staged")
    assert deps.staged_edits[0].args["fields"] == {"sections": ["D1.S4", "D2.S2"]}


# -- an undo that would move gap numbers (or the whole map) under other edits ---------------


def _gap_undo_db():
    db = two_map_db()
    db.tables["attachment_angle_map_edits"] = [
        {"id": "g1", "map_id": "map1", "conversation_id": CONVO, "user_id": USER, "kind": "add_gap",
         "target": "gap 3", "after": {"kind": "missing", "issue": "x", "sections": []},
         "created_at": "2026-11-27T10:00:00", "undone_at": None},
        {"id": "s1", "map_id": "map1", "conversation_id": CONVO, "user_id": USER, "kind": "snapshot",
         "target": "map", "before": {}, "created_at": "2026-11-27T09:00:00", "undone_at": None},
    ]
    return db


def test_a_gap_undo_and_a_gap_edit_cannot_share_a_message():
    deps = edit_deps(_gap_undo_db(), "تراجع عن آخر تعديل واحذف الثغرة الثانية")
    assert asyncio.run(T.stage_undo(deps, "E1", "تراجع عن آخر تعديل")).startswith("Staged")
    out = T.stage_remove_gap(deps, "M1·G2", "واحذف الثغرة الثانية")
    assert out.startswith("Refused") and T.R_MIX_GAPS in out
    # a thread edit is fine alongside it
    assert T.stage_edit_thread(deps, "M1·A1", ThreadFields(strength="strong"), "تراجع عن آخر تعديل").startswith("Staged")
    # and the other way round: a gap edit staged first refuses the gap undo
    deps = edit_deps(_gap_undo_db(), "احذف الثغرة الثانية وتراجع عن آخر تعديل")
    assert T.stage_remove_gap(deps, "M1·G2", "احذف الثغرة الثانية").startswith("Staged")
    out = asyncio.run(T.stage_undo(deps, "E1", "وتراجع عن آخر تعديل"))
    assert out.startswith("Refused") and T.R_MIX_GAPS in out


def test_undoing_a_reanalysis_stands_alone():
    deps = edit_deps(_gap_undo_db(), "تراجع عن إعادة التحليل وخل الخيط الأول قوي")
    assert T.stage_edit_thread(deps, "M1·A1", ThreadFields(strength="strong"), "وخل الخيط الأول قوي").startswith("Staged")
    out = asyncio.run(T.stage_undo(deps, "E2", "تراجع عن إعادة التحليل"))
    assert out.startswith("Refused") and T.R_MIX_SNAPSHOT in out
    deps = edit_deps(_gap_undo_db(), "تراجع عن إعادة التحليل وخل الخيط الأول قوي")
    assert asyncio.run(T.stage_undo(deps, "E2", "تراجع عن إعادة التحليل")).startswith("Staged")
    assert T.R_MIX_SNAPSHOT in T.stage_edit_thread(deps, "M1·A1", ThreadFields(strength="strong"), "وخل الخيط الأول قوي")
    # text fixes and promotions do not touch the map: still fine
    assert T.stage_fix_text(deps, "F1", "محمد", "محمود", "وخل الخيط الأول قوي").startswith("Staged")


# -- list_recent_edits / undo ------------------------------------------------------------


def _with_history():
    db = two_map_db()
    start, end = span_of(LEASE, "للتواصل: lessor.office@mail.com\nالتوقيع: المؤجر")
    db.tables["attachment_text_corrections"] = [
        {"id": "t1", "file_id": "i1", "user_id": USER, "conversation_id": CONVO, "wrong_text": "محمد",
         "right_text": "محمود", "occurrences": 1, "created_at": "2026-11-27T10:00:00", "reverted_at": None},
        {"id": "t0", "file_id": "i1", "user_id": USER, "conversation_id": "other", "wrong_text": "أ",
         "right_text": "ب", "occurrences": 1, "created_at": "2026-11-27T11:00:00", "reverted_at": None},
    ]
    db.tables["attachment_segment_overrides"] = [
        {"id": "o1", "file_id": "i1", "user_id": USER, "conversation_id": CONVO, "char_start": start,
         "char_end": end, "value": "core", "created_at": "2026-11-27T10:05:00", "reverted_at": None},
    ]
    db.tables["attachment_angle_map_edits"] = [
        {"id": "e1", "map_id": "map1", "conversation_id": CONVO, "user_id": USER, "kind": "edit_angle",
         "target": "A2", "before": {"strength": "medium"}, "after": {"strength": "weak"},
         "created_at": "2026-11-27T10:10:00", "undone_at": None},
        {"id": "e2", "map_id": "map1", "conversation_id": CONVO, "user_id": USER, "kind": "undo",
         "target": "e0", "created_at": "2026-11-27T10:11:00", "undone_at": None},
    ]
    return db


def test_list_recent_edits_newest_first_with_short_labels():
    deps = edit_deps(_with_history(), "تراجع عن آخر تعديل")
    out = asyncio.run(T.list_recent(deps))
    lines = out.splitlines()
    assert lines[1].startswith("E1 · 2026-11-27 10:10 · تعديل الخيط «الأجرة مقدمًا» (القوة)")
    assert lines[2].startswith("E2 · 2026-11-27 10:05 · ترقية القسم «التواصل والتوقيع» إلى جوهري")
    assert lines[3].startswith("E3 · 2026-11-27 10:00 · تصحيح «محمد» إلى «محمود»")
    assert len(lines) == 4                     # other conversations and undo rows are not listed
    assert "t1" not in out and "o1" not in out and "e1" not in out   # ids stay hidden
    assert {k: (v.source, v.id) for k, v in deps.recent_edits.items()} == {
        "E1": ("map", "e1"), "E2": ("promote", "o1"), "E3": ("text", "t1")}


def test_undo_stages_by_label_loading_the_list_when_needed():
    deps = edit_deps(_with_history(), "تراجع عن آخر تعديل")
    assert asyncio.run(T.stage_undo(deps, "E1", "تراجع عن آخر تعديل")).startswith("Staged: undo E1")
    assert "already staged" in asyncio.run(T.stage_undo(deps, "E1", "تراجع عن آخر تعديل"))
    assert "Unknown change" in asyncio.run(T.stage_undo(deps, "E9", "تراجع عن آخر تعديل"))
    assert asyncio.run(T.stage_undo(deps, "E1", "غير موجود")) == REFUSED_EVIDENCE
    (e,) = deps.staged_edits
    assert e.args["source"] == "map" and e.args["id"] == "e1"


def test_the_edit_list_is_masked():
    codec = PrivacyCodec(rng=random.Random(9))
    token = masking_service.set_active_codec(codec)
    try:
        db = _lease_only()
        db.tables["attachment_text_corrections"] = [
            {"id": "t1", "file_id": "i1", "user_id": USER, "conversation_id": CONVO, "wrong_text": "1010234567",
             "right_text": "1010234568", "occurrences": 1, "created_at": "2026-11-27T10:00:00", "reverted_at": None},
        ]
        deps = edit_deps(db, "تراجع")
        out = asyncio.run(T.list_recent(deps))
        assert "1010234567" not in out and codec.real_to_fake["1010234567"] in out
    finally:
        masking_service.reset_active_codec(token)
