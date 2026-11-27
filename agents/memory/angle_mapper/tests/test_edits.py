"""In-place edits on a stored angle map + their log (migration 234,
attachment_agent.md §3.2, §7 option a): each operation, every validation that
refuses before writing, the log row, rollback when the log cannot be written,
undo of each kind, snapshot + restore, and freshness untouched by an edit."""
from __future__ import annotations

import copy
import random

import pytest

from agents.memory.angle_mapper import edits as E
from agents.memory.angle_mapper.store import (
    ANGLES_TABLE,
    MAPS_TABLE,
    load_angle_map_for_item,
    load_fresh_angle_map_for_item,
    write_angle_map,
)
from agents.memory.attachment_segmenter.paragraphs import PARAGRAPH_RULE_VERSION

from . import fixtures as F
from .fake_supabase import FakeSupabase

A, B = F.ITEM_A, F.ITEM_B
USER = F.USER
EV = "غيّر عنوان الخيط الثاني"
LOG = E.EDITS_TABLE


def _items(*docs: F.Doc) -> list[dict]:
    return [
        {"item_id": d.item_id, "doc_index": n, "content_chars": len(F.content(d)),
         "segment_prompt_version": F.SEG_VERSION, "paragraph_rule_version": PARAGRAPH_RULE_VERSION,
         "segmented_at": F.SEG_CREATED_AT}
        for n, d in enumerate(docs, start=1)
    ]


def _map_row(answer: dict) -> dict:
    return {
        "prompt_version": "angles-v5", "model": "m", "client": answer["client"], "context": answer["context"],
        "gaps": answer["gaps"], "no_angle": answer["no_angle"], "unrelated_documents": answer["unrelated_documents"],
        "input_tokens": 1, "output_tokens": 1, "reasoning_tokens": 0, "cost_usd": 0.01, "duration_ms": 10,
    }


def stored(*docs: F.Doc, answer: dict | None = None) -> tuple[FakeSupabase, str]:
    """The judgment (D1) + the objection (D2) mapped with ``good_two`` by default:
    A1 strengthen [D2.S2, D1.S3, D1.S4] · A2 answer [D1.S2] · A3 attack [D1.S5, D2.S3];
    context [D1.S1, D2.S1]; one contradiction gap."""
    docs = docs or (F.JUDGMENT, F.OBJECTION)
    answer = answer or (F.good_two() if len(docs) == 2 else F.good_one())
    db = FakeSupabase(F.tables(*docs))
    angles = [{**a, "seq": i} for i, a in enumerate(answer["angles"], start=1)]
    map_id = write_angle_map(db, message_id=F.MSG, conversation_id=F.CONVO, user_id=USER,
                             items=_items(*docs), map_row=_map_row(answer), angles=angles)
    assert map_id
    return db, map_id


def angle(db, seq: int) -> dict | None:
    return next((a for a in db.tables[ANGLES_TABLE] if a["seq"] == seq), None)


def head(db) -> dict:
    return db.tables[MAPS_TABLE][0]


def rows(db) -> dict:
    return copy.deepcopy({k: v for k, v in db.tables.items() if v})


def kw(**more):
    return {"evidence": EV, "user_id": USER, "conversation_id": F.CONVO, "message_id": "msg-9", **more}


# ── edit_angle ────────────────────────────────────────────────────────────────


def test_edit_angle_in_place_and_logged():
    db, map_id = stored()
    r = E.edit_angle(db, map_id, 2, {"title": "  عنوان جديد ", "strength": "STRONG", "weakness": "لا شيء"}, **kw())
    assert r.ok and r.edit_id and r.kind == "edit_angle" and r.target == "A2"
    assert r.before == {"title": "وجه answer", "strength": "medium", "weakness": "سندات التسليم"}
    assert r.after == {"title": "عنوان جديد", "strength": "strong", "weakness": "لا شيء"}
    a2 = angle(db, 2)
    assert a2["title"] == "عنوان جديد" and a2["strength"] == "strong" and a2["sections"] == ["D1.S2"]
    log = db.tables[LOG]
    assert len(log) == 1
    row = log[0]
    assert row["id"] == r.edit_id and row["map_id"] == map_id and row["kind"] == "edit_angle"
    assert row["target"] == "A2" and row["before"] == r.before and row["after"] == r.after
    assert row["evidence"] == EV and row["item_ids"] == [A, B] and row["user_id"] == USER
    assert row["conversation_id"] == F.CONVO and row["message_id"] == "msg-9"


def test_section_refs_are_canonicalised_added_and_removed():
    db, map_id = stored()
    r = E.edit_angle(db, map_id, 2, {"add_sections": ["d1·s6", "D2-S04"], "remove_sections": ["D1 S2"]}, **kw())
    assert r.ok
    assert angle(db, 2)["sections"] == ["D1.S6", "D2.S4"]
    r = E.edit_angle(db, map_id, 2, {"sections": ["D2.S4", "D2.S4", "[D1S1]"]}, **kw())
    assert r.ok and angle(db, 2)["sections"] == ["D2.S4", "D1.S1"]


@pytest.mark.parametrize("fields,reason", [
    ({"sections": ["D1.S9"]}, "D1.S9"),
    ({"add_sections": ["D3.S1"]}, "D3.S1"),
    ({"sections": ["S4"]}, "قسم غير موجود"),                  # bare: two documents → ambiguous
    ({"remove_sections": ["D1.S2"]}, E.R_SECTIONS_EMPTY),     # the last section
    ({"sections": []}, E.R_SECTIONS_EMPTY),
    ({"remove_sections": ["D1.S5"]}, E.R_NOT_IN_ANGLE),
    ({"sections": ["D1.S2"], "add_sections": ["D1.S3"]}, E.R_SECTIONS_BOTH),
    ({"kind": "defend"}, E.R_KIND),
    ({"strength": "very"}, E.R_STRENGTH),
    ({"for_side": "judge"}, E.R_SIDE),
    ({"ocr_suspect": "maybe"}, E.R_BOOL),
    ({"title": "  "}, E.R_TITLE),
    ({"title": "x" * (E.MAX_TEXT_CHARS + 1)}, E.R_TEXT_LONG),
    ({"seq": 9}, "حقل لا يُعدَّل هنا: seq."),
    ({"map_id": "x", "title": "y"}, "map_id"),
    ({}, E.R_NO_FIELDS),
    ({"title": "وجه answer"}, E.R_NO_CHANGE),
])
def test_edit_angle_refusals_write_nothing(fields, reason):
    db, map_id = stored()
    before = rows(db)
    r = E.edit_angle(db, map_id, 2, fields, **kw())
    assert not r.ok and reason in r.reason
    assert rows(db) == before


def test_edit_angle_aliases_and_bools():
    db, map_id = stored()
    r = E.edit_angle(db, map_id, 1, {"kind": "Attacking", "for_side": "defendant", "ocr_suspect": "true"}, **kw())
    assert r.ok
    a = angle(db, 1)
    assert (a["kind"], a["for_side"], a["ocr_suspect"]) == ("attack", "respondent", True)


def test_a_bare_ref_is_qualified_in_a_one_document_map():
    db, map_id = stored(F.JUDGMENT)
    r = E.edit_angle(db, map_id, 1, {"add_sections": ["S6"]}, **kw())
    assert r.ok and "D1.S6" in angle(db, 1)["sections"]


@pytest.mark.parametrize("change,reason", [
    (dict(evidence=""), E.R_NO_EVIDENCE),
    (dict(evidence="   "), E.R_NO_EVIDENCE),
    (dict(user_id="user-8"), E.R_NO_MAP),
])
def test_owner_and_evidence_are_required(change, reason):
    db, map_id = stored()
    r = E.edit_angle(db, map_id, 1, {"title": "س"}, **kw(**change))
    assert not r.ok and r.reason == reason
    assert db.tables[LOG] == []


def test_unknown_map_and_unknown_angle():
    db, map_id = stored()
    assert E.edit_angle(db, "no-such-map", 1, {"title": "س"}, **kw()).reason == E.R_NO_MAP
    assert E.edit_angle(db, map_id, 7, {"title": "س"}, **kw()).reason == E.R_NO_ANGLE


def test_read_failure_is_a_plain_refusal():
    db, map_id = stored()
    db.fail.add((MAPS_TABLE, "select"))
    r = E.edit_angle(db, map_id, 1, {"title": "س"}, **kw())
    assert not r.ok and r.reason == E.R_READ


def test_failed_log_write_rolls_the_edit_back():
    db, map_id = stored()
    db.fail.add((LOG, "insert"))
    r = E.edit_angle(db, map_id, 2, {"title": "جديد"}, **kw())
    assert not r.ok and r.reason == E.R_WRITE
    assert angle(db, 2)["title"] == "وجه answer"
    r = E.add_angle(db, map_id, {"title": "ج", "kind": "attack", "sections": ["D1.S6"]}, **kw())
    assert not r.ok and len(db.tables[ANGLES_TABLE]) == 3
    r = E.remove_gap(db, map_id, 1, **kw())
    assert not r.ok and len(head(db)["gaps"]) == 1


def test_failed_update_changes_nothing_and_logs_nothing():
    db, map_id = stored()
    db.fail.add((ANGLES_TABLE, "update"))
    r = E.edit_angle(db, map_id, 2, {"title": "جديد"}, **kw())
    assert not r.ok and r.reason == E.R_WRITE and db.tables[LOG] == []


# ── masking: stored real, never a fake ────────────────────────────────────────

REAL = "1098765432"


@pytest.fixture
def turn_codec():
    from backend.app.services.masking_service import reset_active_codec, set_active_codec
    from shared.privacy.codec import PrivacyCodec

    codec = PrivacyCodec(rng=random.Random(3), enabled=True)
    codec.encode(f"هوية {REAL}")
    token = set_active_codec(codec)
    yield codec
    reset_active_codec(token)


def test_text_is_decoded_before_it_is_stored(turn_codec):
    db, map_id = stored()
    fake = turn_codec.real_to_fake[REAL]
    r = E.edit_angle(db, map_id, 1, {"argument": f"صاحب الهوية {fake} سدد"}, evidence=f"الهوية {fake}",
                     user_id=USER)
    assert r.ok and angle(db, 1)["argument"] == f"صاحب الهوية {REAL} سدد"
    assert db.tables[LOG][0]["evidence"] == f"الهوية {REAL}"


def test_a_fake_that_would_stay_is_refused(turn_codec):
    db, map_id = stored()
    fake = turn_codec.real_to_fake[REAL]
    bad = next(fake[:-1] + d for d in "0123456789" if fake[:-1] + d not in (fake, REAL))
    r = E.edit_angle(db, map_id, 1, {"argument": f"الهوية {bad}"}, **kw())
    assert not r.ok and r.reason == E.R_FAKE and bad not in r.reason
    r = E.add_gap(db, map_id, {"issue": f"رقم {bad}"}, **kw())
    assert not r.ok and r.reason == E.R_FAKE


# ── add / remove angle ────────────────────────────────────────────────────────


def test_add_angle_defaults_to_the_client_and_takes_the_next_seq():
    db, map_id = stored()
    r = E.add_angle(db, map_id, {"title": "خيط جديد", "kind": "strengthen", "sections": ["D1.S6"],
                                 "legal_question": "هل؟"}, **kw())
    assert r.ok and r.seq == 4 and r.target == "A4" and r.before is None
    a4 = angle(db, 4)
    assert a4["for_side"] == "respondent" and a4["for_party"] == "المدعى عليه"
    assert a4["strength"] == "medium" and a4["sections"] == ["D1.S6"] and a4["ocr_suspect"] is False
    assert db.tables[LOG][0]["after"] == {k: v for k, v in a4.items() if k != "map_id"}


@pytest.mark.parametrize("fields,reason", [
    ({"kind": "attack", "sections": ["D1.S6"]}, E.R_TITLE),
    ({"title": "ج", "sections": ["D1.S6"]}, E.R_KIND),
    ({"title": "ج", "kind": "attack"}, E.R_SECTIONS_EMPTY),
    ({"title": "ج", "kind": "attack", "sections": ["D9.S1"]}, "D9.S1"),
    ({"title": "ج", "kind": "attack", "sections": ["D1.S6"], "add_sections": ["D1.S1"]}, "add_sections"),
])
def test_add_angle_refusals(fields, reason):
    db, map_id = stored()
    r = E.add_angle(db, map_id, fields, **kw())
    assert not r.ok and reason in r.reason and len(db.tables[ANGLES_TABLE]) == 3


def test_remove_angle_warns_below_two_and_seqs_are_never_reused():
    db, map_id = stored()
    r = E.remove_angle(db, map_id, 3, **kw())
    assert r.ok and r.angles_left == 2 and not r.below_two and r.warning == ""
    assert r.before["title"] == "أسباب الحكم لا تحمل منطوقه" and angle(db, 3) is None
    r = E.remove_angle(db, map_id, 1, **kw())
    assert r.ok and r.angles_left == 1 and r.below_two and r.warning == E.W_BELOW_TWO
    # A3 was the highest ever: a new thread is A4, not A3 again
    r = E.add_angle(db, map_id, {"title": "ج", "kind": "attack", "sections": ["D1.S5"]}, **kw())
    assert r.seq == 4
    assert sorted(a["seq"] for a in db.tables[ANGLES_TABLE]) == [2, 4]


def test_remove_unknown_angle():
    db, map_id = stored()
    assert E.remove_angle(db, map_id, 9, **kw()).reason == E.R_NO_ANGLE


# ── gaps ──────────────────────────────────────────────────────────────────────


def test_add_edit_remove_gap():
    db, map_id = stored()
    r = E.add_gap(db, map_id, {"issue": "لا يوجد عقد القرض", "sections": ["d1.s2"]}, **kw())
    assert r.ok and r.target == "gap 2"
    assert head(db)["gaps"][1] == {"kind": "missing", "sections": ["D1.S2"], "issue": "لا يوجد عقد القرض",
                                   "ocr_suspect": False}
    r = E.edit_gap(db, map_id, 2, {"kind": "unanswered", "add_sections": ["D2.S2"]}, **kw())
    assert r.ok and r.before["kind"] == "missing" and r.after["kind"] == "unanswered"
    assert head(db)["gaps"][1]["sections"] == ["D1.S2", "D2.S2"]
    r = E.remove_gap(db, map_id, 1, **kw())
    assert r.ok and r.before["kind"] == "contradiction"
    assert [g["kind"] for g in head(db)["gaps"]] == ["unanswered"]
    assert [row["kind"] for row in db.tables[LOG]] == ["add_gap", "edit_gap", "remove_gap"]


@pytest.mark.parametrize("op,reason", [
    (lambda db, m: E.add_gap(db, m, {"issue": ""}, **kw()), E.R_ISSUE),
    (lambda db, m: E.add_gap(db, m, {"issue": "x", "kind": "other"}, **kw()), E.R_GAP_KIND),
    (lambda db, m: E.add_gap(db, m, {"issue": "x", "sections": ["D7.S1"]}, **kw()), "D7.S1"),
    (lambda db, m: E.add_gap(db, m, {"issue": "x", "why": "y"}, **kw()), "why"),
    (lambda db, m: E.edit_gap(db, m, 5, {"issue": "x"}, **kw()), E.R_NO_GAP),
    (lambda db, m: E.edit_gap(db, m, 1, {"remove_sections": ["D1.S1"]}, **kw()), "ليس في هذه الثغرة"),
    (lambda db, m: E.edit_gap(db, m, 1, {"kind": "contradiction"}, **kw()), E.R_NO_CHANGE),
    (lambda db, m: E.remove_gap(db, m, 0, **kw()), E.R_NO_GAP),
])
def test_gap_refusals(op, reason):
    db, map_id = stored()
    before = copy.deepcopy(head(db)["gaps"])
    r = op(db, map_id)
    assert not r.ok and reason in r.reason
    assert head(db)["gaps"] == before and db.tables[LOG] == []


# ── client, context, no_angle, unrelated ──────────────────────────────────────


def test_edit_client_side_sets_clear_and_logs_only_changes():
    db, map_id = stored(answer=F.good_two(client={**F.CLIENT_TWO, "confidence": "likely"}))
    r = E.edit_client(db, map_id, {"side": "Plaintiff", "party": "المدعي"}, **kw())
    assert r.ok
    assert r.before == {"side": "respondent", "party": "المدعى عليه", "confidence": "likely"}
    assert r.after == {"side": "claimant", "party": "المدعي", "confidence": "clear"}
    client = head(db)["client"]
    assert client["side"] == "claimant" and client["by_document"] == F.CLIENT_TWO["by_document"]
    assert client["basis"] == F.CLIENT_TWO["basis"]


def test_edit_client_by_document_and_refusals():
    db, map_id = stored()
    r = E.edit_client(db, map_id, {"by_document": [{"document": "d2", "side": "respondent", "party": "المعترض"}]},
                      **kw())
    assert r.ok and head(db)["client"]["by_document"] == [{"document": "D2", "side": "respondent", "party": "المعترض"}]
    for fields, reason in [
        ({"by_document": [{"document": "D5", "side": "claimant"}]}, E.R_DOCUMENT),
        ({"side": "judge"}, E.R_SIDE),
        ({"confidence": "sure"}, E.R_CONFIDENCE),
        ({"name": "x"}, "name"),
        ({"side": "respondent"}, E.R_NO_CHANGE),
    ]:
        res = E.edit_client(db, map_id, fields, **kw())
        assert not res.ok and reason in res.reason


def test_edit_context_add_remove():
    db, map_id = stored()
    r = E.edit_context(db, map_id, add=["D1.S5"], remove=["D2.S1"], **kw())
    assert r.ok and head(db)["context"] == ["D1.S1", "D1.S5"]
    assert r.before == ["D1.S1", "D2.S1"] and r.after == ["D1.S1", "D1.S5"]
    assert E.edit_context(db, map_id, remove=["D2.S3"], **kw()).reason == E.R_NOT_IN_CONTEXT
    assert E.edit_context(db, map_id, add=["D1.S1"], **kw()).reason == E.R_NO_CHANGE
    assert E.edit_context(db, map_id, **kw()).reason == E.R_NO_FIELDS


def test_edit_no_angle_and_unrelated():
    db, map_id = stored()
    r = E.edit_no_angle(db, map_id, [{"section": "d1.s6", "why": "توقيع"}], **kw())
    assert r.ok and head(db)["no_angle"] == [{"section": "D1.S6", "why": "توقيع"}]
    r = E.edit_unrelated(db, map_id, [{"document": "2", "why": "قضية أخرى"}], **kw())
    assert r.ok and head(db)["unrelated_documents"] == [{"document": "D2", "why": "قضية أخرى"}]
    assert not E.edit_unrelated(db, map_id, [{"document": "D4"}], **kw()).ok
    assert not E.edit_no_angle(db, map_id, [{"section": "D1.S99"}], **kw()).ok


# ── undo ──────────────────────────────────────────────────────────────────────


def test_undo_edit_angle_then_refuse_twice_and_undo_of_undo():
    db, map_id = stored()
    r = E.edit_angle(db, map_id, 2, {"title": "جديد", "strength": "weak"}, **kw())
    u = E.undo_edit(db, r.edit_id, USER, evidence="رجّع الخيط كما كان", conversation_id=F.CONVO)
    assert u.ok and u.kind == "undo" and u.target == r.edit_id
    assert angle(db, 2)["title"] == "وجه answer" and angle(db, 2)["strength"] == "medium"
    rows = {row["id"]: row for row in db.tables[LOG]}
    assert rows[r.edit_id]["undone_at"] and rows[u.edit_id]["kind"] == "undo"
    assert rows[u.edit_id]["target"] == r.edit_id and rows[u.edit_id]["evidence"] == "رجّع الخيط كما كان"
    assert E.undo_edit(db, r.edit_id, USER).reason == E.R_UNDO_DONE
    assert E.undo_edit(db, u.edit_id, USER).reason == E.R_UNDO_UNDO
    assert E.undo_edit(db, "nope", USER).reason == E.R_UNDO_NONE


def test_undo_is_owner_only():
    db, map_id = stored()
    r = E.edit_angle(db, map_id, 2, {"title": "جديد"}, **kw())
    assert E.undo_edit(db, r.edit_id, "user-8").reason == E.R_UNDO_NONE
    assert angle(db, 2)["title"] == "جديد"


def test_undo_edit_angle_after_the_same_field_changed_again_is_refused():
    db, map_id = stored()
    first = E.edit_angle(db, map_id, 2, {"title": "أول"}, **kw())
    E.edit_angle(db, map_id, 2, {"title": "ثان"}, **kw())
    assert E.undo_edit(db, first.edit_id, USER).reason == E.R_UNDO_CHANGED
    assert angle(db, 2)["title"] == "ثان"


def test_undo_add_and_remove_angle():
    db, map_id = stored()
    add = E.add_angle(db, map_id, {"title": "ج", "kind": "attack", "sections": ["D1.S6"]}, **kw())
    assert E.undo_edit(db, add.edit_id, USER).ok and angle(db, 4) is None
    rem = E.remove_angle(db, map_id, 2, **kw())
    original = rem.before
    u = E.undo_edit(db, rem.edit_id, USER)
    assert u.ok and u.seq == 2
    assert {k: v for k, v in angle(db, 2).items() if k != "map_id"} == original


def test_undo_remove_angle_when_its_seq_was_taken_gets_a_new_seq():
    db, map_id = stored()
    rem = E.remove_angle(db, map_id, 3, **kw())
    db.tables[ANGLES_TABLE].append({**rem.before, "map_id": map_id, "title": "دخيل"})   # A3 taken meanwhile
    u = E.undo_edit(db, rem.edit_id, USER)
    assert u.ok and u.seq == 4 and angle(db, 4)["title"] == rem.before["title"]


def test_undo_gaps_client_context():
    db, map_id = stored()
    gaps0 = copy.deepcopy(head(db)["gaps"])
    add = E.add_gap(db, map_id, {"issue": "ثغرة"}, **kw())
    edit = E.edit_gap(db, map_id, 1, {"issue": "معدلة"}, **kw())
    rem = E.remove_gap(db, map_id, 1, **kw())
    assert E.undo_edit(db, rem.edit_id, USER).ok
    assert head(db)["gaps"][0]["issue"] == "معدلة"
    assert E.undo_edit(db, edit.edit_id, USER).ok
    assert E.undo_edit(db, add.edit_id, USER).ok
    assert head(db)["gaps"] == gaps0

    client0 = copy.deepcopy(head(db)["client"])
    c = E.edit_client(db, map_id, {"party": "شخص آخر"}, **kw())
    assert E.undo_edit(db, c.edit_id, USER).ok and head(db)["client"] == client0

    ctx = E.edit_context(db, map_id, add=["D1.S5"], remove=["D2.S1"], **kw())
    E.edit_context(db, map_id, add=["D2.S4"], **kw())
    assert E.undo_edit(db, ctx.edit_id, USER).ok
    assert head(db)["context"] == ["D1.S1", "D2.S4", "D2.S1"]       # the later edit is kept


def test_undo_a_gap_edited_again_is_refused():
    db, map_id = stored()
    edit = E.edit_gap(db, map_id, 1, {"issue": "أول"}, **kw())
    E.edit_gap(db, map_id, 1, {"issue": "ثان"}, **kw())
    assert E.undo_edit(db, edit.edit_id, USER).reason == E.R_UNDO_CHANGED


def test_undo_after_a_reanalysis_is_refused():
    db, map_id = stored()
    r = E.edit_angle(db, map_id, 2, {"title": "جديد"}, **kw())
    angles = [{**a, "seq": i} for i, a in enumerate(F.good_two()["angles"], start=1)]
    new_id = write_angle_map(db, message_id=F.MSG, conversation_id=F.CONVO, user_id=USER,
                             items=_items(F.JUDGMENT, F.OBJECTION), map_row=_map_row(F.good_two()), angles=angles)
    assert new_id and new_id != map_id
    assert E.undo_edit(db, r.edit_id, USER).reason == E.R_UNDO_REANALYZED
    # the log survived the replace (no FK to the map)
    assert [row["map_id"] for row in db.tables[LOG]] == [map_id]


# ── snapshot ──────────────────────────────────────────────────────────────────


def test_snapshot_then_restore_after_a_reanalysis():
    db, map_id = stored()
    E.edit_angle(db, map_id, 2, {"title": "عدّله المستخدم"}, **kw())
    snap = E.snapshot_map(db, map_id, "ماعجبني التحليل اعيد تحليله", user_id=USER, conversation_id=F.CONVO)
    assert snap.ok and snap.kind == "snapshot" and snap.map_id == map_id
    assert [a["title"] for a in snap.before["angles"]][1] == "عدّله المستخدم"
    assert [i["item_id"] for i in snap.before["items"]] == [A, B]

    # the re-analysis replaces the map with one thread
    one = F.good_two()
    one["angles"] = one["angles"][:1]
    new_id = write_angle_map(db, message_id=F.MSG, conversation_id=F.CONVO, user_id=USER,
                             items=_items(F.JUDGMENT, F.OBJECTION), map_row=_map_row(one),
                             angles=[{**one["angles"][0], "seq": 1}])
    assert len(load_angle_map_for_item(db, A)["angles"]) == 1

    u = E.undo_edit(db, snap.edit_id, USER, evidence="رجّع الخيوط القديمة")
    assert u.ok and u.map_id and u.map_id not in (map_id, new_id)
    back = load_angle_map_for_item(db, A)
    assert back["map"]["map_id"] == u.map_id and back["map"]["message_id"] == F.MSG
    assert [a["title"] for a in back["angles"]] == [a["title"] for a in snap.before["angles"]]
    assert back["map"]["client"] == snap.before["map"]["client"]
    assert load_fresh_angle_map_for_item(db, A) is not None
    assert E.undo_edit(db, snap.edit_id, USER).reason == E.R_UNDO_DONE


def test_snapshot_of_another_users_map_is_refused():
    db, map_id = stored()
    assert E.snapshot_map(db, map_id, "x", user_id="user-8").reason == E.R_NO_MAP
    assert db.tables[LOG] == []


# ── log reads, freshness, carried maps ────────────────────────────────────────


def test_load_edit_log_by_map_and_by_conversation():
    db, map_id = stored()
    a = E.edit_angle(db, map_id, 2, {"title": "أ"}, **kw())
    E.edit_angle(db, map_id, 2, {"title": "ب"}, **kw(conversation_id="convo-other"))
    assert [r["after"]["title"] for r in E.load_edit_log(db, map_id)] == ["أ", "ب"]
    assert [r["after"]["title"] for r in E.load_edit_log(db, None, conversation_id="convo-other")] == ["ب"]
    assert E.load_edit_log(db, None) == []
    db.fail.add(LOG)
    assert E.load_edit_log(db, map_id) == []


def test_edits_never_make_the_map_stale():
    db, map_id = stored()
    assert load_fresh_angle_map_for_item(db, A)["map"]["map_id"] == map_id
    assert E.edit_angle(db, map_id, 1, {"title": "س", "add_sections": ["D1.S6"]}, **kw()).ok
    assert E.remove_angle(db, map_id, 3, **kw()).ok
    assert E.add_gap(db, map_id, {"issue": "ثغرة"}, **kw()).ok
    assert E.edit_client(db, map_id, {"side": "claimant"}, **kw()).ok
    assert E.edit_context(db, map_id, add=["D1.S5"], **kw()).ok
    fresh = load_fresh_angle_map_for_item(db, A)
    assert fresh is not None and fresh["map"]["map_id"] == map_id
    assert [a["seq"] for a in fresh["angles"]] == [1, 2]


def test_an_edit_is_seen_from_every_member_of_the_map():
    """§7 option (a): one map, edited in place — whoever reads it sees the edit."""
    db, map_id = stored()
    E.edit_angle(db, map_id, 1, {"title": "للجميع"}, **kw(conversation_id="convo-carrier"))
    for item in (A, B):
        assert load_angle_map_for_item(db, item)["angles"][0]["title"] == "للجميع"


def test_refs_checked_against_todays_segment_maps():
    """A member whose segment map went stale has no valid refs today."""
    db, map_id = stored()
    seg = next(r for r in db.tables["attachment_segment_maps"] if r["item_id"] == B)
    seg["content_chars"] = 1
    r = E.edit_angle(db, map_id, 2, {"add_sections": ["D2.S2"]}, **kw())
    assert not r.ok and "D2.S2" in r.reason
    assert E.edit_angle(db, map_id, 2, {"add_sections": ["D1.S6"]}, **kw()).ok


def test_a_promoted_section_is_a_valid_ref_like_any_other():
    from shared.attachments import segment_overrides as so

    so.reset_backoff()
    db, map_id = stored()
    row = next(r for r in db.tables["attachment_segments"] if r["item_id"] == A and r["seq"] == 6)
    assert so.save_segment_override(db, file_id=A, user_id=USER, char_start=row["char_start"],
                                    char_end=row["char_end"], evidence="e")
    assert E.edit_context(db, map_id, add=["D1.S6"], **kw()).ok
