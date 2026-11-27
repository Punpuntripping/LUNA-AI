"""Derived wording fixed once after a text fix (attachment_agent.md §3.3): the
file's summary / title, the items carrying it, the segment rows, and the angle
maps (logged through angle_mapper.edits) — whole words only, never raising.
Synthetic text, fake client."""
from __future__ import annotations

import random
from types import SimpleNamespace

from agents.memory.angle_mapper.edits import EDITS_TABLE
from agents.memory.angle_mapper.tests.fake_supabase import FakeSupabase
from backend.app.services import masking_service
from shared.attachments.derived import PLACES, fix_derived_wording, replace_whole, save_derived, undo_derived
from shared.files import flags as files_flags
from shared.privacy.codec import PrivacyCodec

USER = "user-7"
FILE = "f1"


class RpcFake(FakeSupabase):
    """+ the ``user_file_patch`` RPC (``shared.files.store.patch_file``)."""

    def rpc(self, name: str, params: dict):
        db = self

        class _Call:
            def execute(self_inner):
                db.log.append(("rpc", name))
                if ("rpc", name) in db.fail:
                    raise RuntimeError("rpc failed")
                hit = [r for r in db.tables["user_files"] if str(r["file_id"]) == params["p_file_id"]
                       and (not params.get("p_user_id") or str(r["user_id"]) == params["p_user_id"])]
                for r in hit:
                    r.update(params["p_fields"])
                return SimpleNamespace(data=params["p_file_id"] if hit else None)

        return _Call()


def _db(**kw) -> RpcFake:
    return RpcFake({
        "user_files": [{"file_id": FILE, "user_id": USER, "title": "عقد محمد",
                        "summary": "عقد بين محمد وشركة الأفق؛ لمحمد حق الفسخ. (محمدية لا تتغير)"}],
        "workspace_items": [
            {"item_id": FILE, "file_id": FILE, "user_id": USER, "kind": "attachment", "title": "عقد محمد",
             "summary": "عقد بين محمد وشركة الأفق", "conversation_id": "c1"},
            {"item_id": "i2", "file_id": FILE, "user_id": USER, "kind": "attachment", "title": "عقد محمد",
             "summary": "نسخة: محمد", "conversation_id": "c2"},
            {"item_id": "i3", "file_id": "other", "user_id": USER, "kind": "attachment", "title": "محمد آخر",
             "summary": "محمد", "conversation_id": "c1"},
        ],
        "attachment_segments": [
            {"item_id": FILE, "seq": 1, "what": "بيانات محمد", "owner": "محمد", "brought_by": ""},
            {"item_id": FILE, "seq": 2, "what": "الأجرة", "owner": "المحمدي", "brought_by": None},
            {"item_id": "other", "seq": 1, "what": "محمد", "owner": "", "brought_by": ""},
        ],
        "attachment_angle_maps": [
            {"map_id": "m1", "message_id": "u1", "conversation_id": "c1", "user_id": USER,
             "client": {"side": "claimant", "party": "محمد", "basis": "المؤجر محمد", "confidence": "likely"},
             "context": [], "gaps": [{"kind": "missing", "issue": "لا توقيع من محمد", "sections": []},
                                     {"kind": "missing", "issue": "أخرى", "sections": []}],
             "no_angle": [], "unrelated_documents": [], "left_out": []},
        ],
        "attachment_angle_map_items": [{"map_id": "m1", "item_id": FILE, "doc_index": 1},
                                       {"map_id": "m1", "item_id": "i3", "doc_index": 2}],
        "attachment_angles": [
            {"map_id": "m1", "seq": 1, "title": "حق محمد في الفسخ", "argument": "يملك محمد الفسخ",
             "weakness": "", "legal_question": "", "for_party": "محمد", "sections": ["D1.S1"], "strength": "strong",
             "kind": "strengthen", "for_side": "claimant"},
            {"map_id": "m1", "seq": 2, "title": "الأجرة", "argument": "لا ذكر", "sections": ["D1.S2"]},
        ],
    }, **kw)


def test_replace_whole_words_only():
    assert replace_whole("محمد ولمحمد والمحمد", "محمد", "محمود") == ("محمود ولمحمود والمحمود", 3)
    assert replace_whole("المحمدي ومحمدية", "محمد", "محمود") == ("المحمدي ومحمدية", 0)
    assert replace_whole(None, "محمد", "محمود") == (None, 0)
    assert replace_whole("محمد", "محمد", "محمد") == ("محمد", 0)


def test_every_place_is_fixed_once():
    db = _db()
    with files_flags.override(read=True, write=True):
        counts = fix_derived_wording(db, file_id=FILE, user_id=USER, wrong="محمد", right="محمود",
                                     evidence="الاسم محمود", message_id="m9")
    assert counts == {
        "user_files.title": 1, "user_files.summary": 1,
        "workspace_items.title": 2, "workspace_items.summary": 2,
        "attachment_segments": 1,
        "attachment_angles": 1, "angle_map.client": 1, "angle_map.gaps": 1,
    }
    f = db.tables["user_files"][0]
    assert f["title"] == "عقد محمود"
    assert f["summary"] == "عقد بين محمود وشركة الأفق؛ لمحمود حق الفسخ. (محمدية لا تتغير)"
    items = {r["item_id"]: r for r in db.tables["workspace_items"]}
    assert items[FILE]["summary"] == "عقد بين محمود وشركة الأفق" and items["i2"]["summary"] == "نسخة: محمود"
    assert items["i3"]["summary"] == "محمد"                     # another file: untouched
    segs = {(r["item_id"], r["seq"]): r for r in db.tables["attachment_segments"]}
    assert (segs[(FILE, 1)]["what"], segs[(FILE, 1)]["owner"]) == ("بيانات محمود", "محمود")
    assert segs[(FILE, 2)]["owner"] == "المحمدي" and segs[("other", 1)]["what"] == "محمد"
    a1 = next(a for a in db.tables["attachment_angles"] if a["seq"] == 1)
    assert (a1["title"], a1["argument"], a1["for_party"]) == ("حق محمود في الفسخ", "يملك محمود الفسخ", "محمود")
    head = db.tables["attachment_angle_maps"][0]
    assert head["client"] == {"side": "claimant", "party": "محمود", "basis": "المؤجر محمود",
                              "confidence": "likely", "by_document": []}   # the confidence is kept
    assert head["gaps"][0]["issue"] == "لا توقيع من محمود" and head["gaps"][1]["issue"] == "أخرى"
    log = db.tables[EDITS_TABLE]
    assert [r["kind"] for r in log] == ["edit_angle", "edit_client", "edit_gap"]
    assert all(r["conversation_id"] is None and r["evidence"] == "الاسم محمود" and r["message_id"] == "m9"
               for r in log)


def test_nothing_to_fix_writes_nothing():
    db = _db()
    counts = fix_derived_wording(db, file_id=FILE, user_id=USER, wrong="خالد", right="خليل")
    assert counts == {p: 0 for p in PLACES}
    assert db.writes() == []


def test_never_raises_and_other_places_still_fixed():
    db = _db(fail={("rpc", "user_file_patch"), ("attachment_segments", "select")})
    counts = fix_derived_wording(db, file_id=FILE, user_id=USER, wrong="محمد", right="محمود")
    assert counts["user_files.summary"] == 0 and counts["attachment_segments"] == 0
    assert counts["workspace_items.summary"] == 2 and counts["attachment_angles"] == 1
    # the default evidence names the fix
    assert db.tables[EDITS_TABLE][0]["evidence"] == "تصحيح نص الملف: «محمد» ← «محمود»"
    assert fix_derived_wording(None, file_id=FILE, user_id=USER, wrong="a", right="b") == {p: 0 for p in PLACES}


def _fixed(db) -> list:
    changes: list = []
    with files_flags.override(read=True, write=True):
        fix_derived_wording(db, file_id=FILE, user_id=USER, wrong="محمد", right="محمود", changes=changes)
    db.tables["attachment_text_corrections"] = [{"id": "c1", "user_id": USER, "derived": []}]
    assert save_derived(db, "c1", USER, changes)
    return changes


def test_every_change_is_recorded_with_its_key_before_and_after():
    db = _db()
    changes = _fixed(db)
    fields = [(c["place"], c.get("key"), c.get("field")) for c in changes if c["place"] != "angle_map"]
    assert fields == [
        ("user_files", {"file_id": FILE}, "title"), ("user_files", {"file_id": FILE}, "summary"),
        ("workspace_items", {"item_id": FILE}, "title"), ("workspace_items", {"item_id": FILE}, "summary"),
        ("workspace_items", {"item_id": "i2"}, "title"), ("workspace_items", {"item_id": "i2"}, "summary"),
        ("attachment_segments", {"item_id": FILE, "seq": 1}, "what"),
        ("attachment_segments", {"item_id": FILE, "seq": 1}, "owner"),
    ]
    assert changes[0]["before"] == "عقد محمد" and changes[0]["after"] == "عقد محمود"
    maps = [c for c in changes if c["place"] == "angle_map"]
    assert len(maps) == 3 and {c["map_id"] for c in maps} == {"m1"}
    assert [c["edit_id"] for c in maps] == [r["id"] for r in db.tables[EDITS_TABLE]]
    assert db.tables["attachment_text_corrections"][0]["derived"] == changes


def test_undo_restores_only_what_still_reads_the_fix():
    db = _db()
    _fixed(db)
    db.tables["user_files"][0]["title"] = "عنوان غيّره المستخدم"            # changed since the fix
    next(r for r in db.tables["attachment_segments"] if r["seq"] == 1)["what"] = "بيانات محمد"   # already back
    with files_flags.override(read=True, write=True):
        missed = undo_derived(db, "c1", USER, evidence="تراجع")
    assert missed == ["عنوان الملف"]
    f = db.tables["user_files"][0]
    assert f["title"] == "عنوان غيّره المستخدم"
    assert f["summary"] == "عقد بين محمد وشركة الأفق؛ لمحمد حق الفسخ. (محمدية لا تتغير)"
    items = {r["item_id"]: r for r in db.tables["workspace_items"]}
    assert (items[FILE]["title"], items["i2"]["summary"]) == ("عقد محمد", "نسخة: محمد")
    seg = next(r for r in db.tables["attachment_segments"] if r["seq"] == 1)
    assert (seg["what"], seg["owner"]) == ("بيانات محمد", "محمد")
    a1 = next(a for a in db.tables["attachment_angles"] if a["seq"] == 1)
    assert (a1["title"], a1["for_party"]) == ("حق محمد في الفسخ", "محمد")
    head = db.tables["attachment_angle_maps"][0]
    assert head["client"]["party"] == "محمد" and head["gaps"][0]["issue"] == "لا توقيع من محمد"
    assert [r["kind"] for r in db.tables[EDITS_TABLE]].count("undo") == 3


def test_undo_of_a_map_part_changed_since_is_reported():
    db = _db()
    _fixed(db)
    next(a for a in db.tables["attachment_angles"] if a["seq"] == 1)["title"] = "عنوان آخر"
    with files_flags.override(read=True, write=True):
        missed = undo_derived(db, "c1", USER)
    assert missed == ["الخيوط"]


def test_undo_without_a_record_or_the_column_restores_nothing_and_never_raises():
    db = _db()
    db.tables["attachment_text_corrections"] = [{"id": "c1", "user_id": USER}]   # no derived (old row)
    assert undo_derived(db, "c1", USER) == []
    db.fail.add("attachment_text_corrections")                                  # column / table missing
    assert undo_derived(db, "c1", USER) == []
    assert save_derived(db, "c1", USER, [{"place": "x"}]) is False


def test_real_text_is_not_decoded_under_an_active_codec():
    codec = PrivacyCodec(rng=random.Random(2))
    fake = codec.encode("1010234567")
    token = masking_service.set_active_codec(codec)
    try:
        db = _db()
        db.tables["attachment_angles"][0]["argument"] = f"يملك محمد الفسخ، سجل {fake}"
        counts = fix_derived_wording(db, file_id=FILE, user_id=USER, wrong="محمد", right="محمود")
    finally:
        masking_service.reset_active_codec(token)
    assert counts["attachment_angles"] == 1
    # the codec was off inside: text that happens to look like a fake is stored as it is
    assert db.tables["attachment_angles"][0]["argument"] == f"يملك محمود الفسخ، سجل {fake}"
    assert masking_service.active_codec() is None
