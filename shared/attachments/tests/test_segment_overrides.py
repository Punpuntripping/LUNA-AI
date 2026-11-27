"""Section promotions (migration 234, attachment_agent.md §3.1): the pure promote-
only overlay, the store round trip, and the hook inside ``load_segment_maps`` —
every reader sees a promotion; a failed read leaves the stored values."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from agents.memory.angle_mapper.tests import fixtures as F
from agents.memory.angle_mapper.tests.fake_supabase import FakeSupabase
from agents.memory.attachment_segmenter.models import StoredSegment
from agents.memory.attachment_segmenter.store import load_segment_maps
from agents.utils.attachment_delivery import Section
from shared.attachments import segment_overrides as so
from shared.attachments.segment_overrides import (
    SegmentOverride,
    apply_segment_overrides,
    load_segment_overrides,
    revert_segment_override,
    save_segment_override,
)

USER = F.USER


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    """The attachment agent's flag ON (the promotions hook is gated by it); the
    flag-off tests below turn it off."""
    from shared.config import get_settings

    monkeypatch.setattr(get_settings(), "ATTACHMENT_AGENT_ENABLED", True)
    monkeypatch.setattr(get_settings(), "ATTACHMENT_AGENT_USERS", None)
    so.reset_backoff()
    yield
    so.reset_backoff()


def seg(seq: int, start: int, end: int, value: str = "supporting") -> StoredSegment:
    return StoredSegment(seq=seq, from_p=seq, to_p=seq, char_start=start, char_end=end, label="x", value=value,
                         content_md="t")


def ov(start: int, end: int) -> SegmentOverride:
    return SegmentOverride(id="o", file_id="f", char_start=start, char_end=end)


# ── apply (pure) ──────────────────────────────────────────────────────────────


def test_promotes_an_overlapping_section_and_marks_it():
    segs = [seg(1, 0, 100, "metadata"), seg(2, 100, 200), seg(3, 200, 300, "boilerplate")]
    out = apply_segment_overrides(segs, [ov(100, 200)])
    assert [s.value for s in out] == ["metadata", "core", "boilerplate"]
    assert out[1].promoted_by_user is True and out[0].promoted_by_user is False
    assert out[0] is segs[0] and out[2] is segs[2]          # untouched sections are the same objects
    assert segs[1].value == "supporting"                    # input not modified
    assert out[1].model_dump(exclude={"value", "promoted_by_user"}) == segs[1].model_dump(
        exclude={"value", "promoted_by_user"})


def test_partial_overlap_promotes_touching_does_not():
    segs = [seg(1, 0, 100), seg(2, 100, 200), seg(3, 200, 300)]
    out = apply_segment_overrides(segs, [ov(150, 201)])
    assert [s.value for s in out] == ["supporting", "core", "core"]
    out = apply_segment_overrides(segs, [ov(100, 200)])     # [100,200) touches S1's end and S3's start
    assert [s.value for s in out] == ["supporting", "core", "supporting"]


def test_never_demotes_and_core_stays_the_same_object():
    core = seg(1, 0, 100, "core")
    out = apply_segment_overrides([core], [ov(0, 100)])
    assert out[0] is core and out[0].promoted_by_user is False


def test_repeat_sections_and_several_ranges():
    segs = [seg(1, 0, 10, "repeat"), seg(2, 10, 20), seg(3, 20, 30)]
    out = apply_segment_overrides(segs, [ov(0, 5), ov(25, 26)])
    assert [s.value for s in out] == ["core", "supporting", "core"]


def test_no_overrides_or_bad_ranges_change_nothing():
    segs = [seg(1, 0, 10)]
    assert apply_segment_overrides(segs, []) == segs
    assert apply_segment_overrides(segs, None) == segs
    assert apply_segment_overrides(segs, [{"char_start": 5, "char_end": 5}, {"char_start": None, "char_end": 3}]) == segs


def test_section_without_position_is_left_alone():
    d = {"seq": 1, "value": "supporting", "char_start": -1, "char_end": -1}
    assert apply_segment_overrides([d], [ov(0, 100)]) == [d]


def test_dicts_get_the_marker_and_dataclasses_keep_their_fields():
    d = {"seq": 1, "value": "supporting", "char_start": 0, "char_end": 10, "what": "w"}
    out = apply_segment_overrides([d], [{"char_start": 0, "char_end": 1}])
    assert out[0] == {**d, "value": "core", "promoted_by_user": True}
    assert d["value"] == "supporting"

    sec = Section(seq=1, value="supporting", what="w", text="t", char_start=0, char_end=10)
    out = apply_segment_overrides([sec], [ov(0, 10)])
    assert out[0].value == "core" and out[0].text == "t" and out[0].char_end == 10

    @dataclass(frozen=True)
    class Marked:
        value: str
        char_start: int
        char_end: int
        promoted_by_user: bool = False

    assert apply_segment_overrides([Marked("metadata", 0, 5)], [ov(4, 9)])[0] == Marked("core", 0, 5, True)


# ── store ─────────────────────────────────────────────────────────────────────


def test_save_load_revert_round_trip():
    db = FakeSupabase()
    oid = save_segment_override(db, file_id=F.FILE_J, user_id=USER, char_start=10, char_end=50,
                                evidence="خل قسم الدفوع جوهري", conversation_id=F.CONVO_2, message_id=F.MSG_2)
    assert oid
    row = db.tables[so.TABLE][0]
    assert row["value"] == "core" and row["char_start"] == 10 and row["char_end"] == 50 and row["evidence"]
    got = load_segment_overrides(db, [F.FILE_J, F.FILE_O], user_id=USER)
    assert got == {F.FILE_J: [SegmentOverride(id=oid, file_id=F.FILE_J, char_start=10, char_end=50)]}
    assert revert_segment_override(db, oid, "user-8") is False
    assert revert_segment_override(db, oid, USER) is True
    assert load_segment_overrides(db, [F.FILE_J]) == {}
    assert revert_segment_override(db, oid, USER) is False


@pytest.mark.parametrize("start,end", [(10, 10), (20, 10), (-1, 5), (None, 5), ("x", 5)])
def test_save_refuses_a_bad_range(start, end):
    db = FakeSupabase()
    assert save_segment_override(db, file_id=F.FILE_J, user_id=USER, char_start=start, char_end=end,
                                 evidence="e") is None
    assert db.writes() == []


def test_store_never_raises():
    db = FakeSupabase(fail={so.TABLE})
    assert save_segment_override(db, file_id=F.FILE_J, user_id=USER, char_start=0, char_end=5, evidence="e") is None
    assert revert_segment_override(db, "o", USER) is False
    assert load_segment_overrides(db, [F.FILE_J]) == {}


# ── the hook in load_segment_maps ─────────────────────────────────────────────


def _db_two_files(**extra) -> FakeSupabase:
    return FakeSupabase(F.file_tables((F.JUDGMENT, F.ITEM_G, F.FILE_J), (F.OBJECTION, F.ITEM_H, F.FILE_O), **extra))


def _lengths(db) -> dict[str, int]:
    return {r["item_id"]: len(r["content_md"]) for r in db.tables["workspace_items"]}


def _promote(db, file_id: str, doc: F.Doc, seq: int) -> None:
    row = next(r for r in db.tables["attachment_segments"] if r["item_id"] == file_id and r["seq"] == seq)
    assert save_segment_override(db, file_id=file_id, user_id=USER, char_start=row["char_start"],
                                 char_end=row["char_end"], evidence="e")


def test_every_reader_sees_the_promotion_in_one_read():
    db = _db_two_files()
    _promote(db, F.FILE_J, F.JUDGMENT, 4)        # S4 expert_report: supporting → core
    maps = load_segment_maps(db, [F.ITEM_G, F.ITEM_H], user_id=USER, content_chars=_lengths(db))
    j = {s.seq: s for s in maps[F.ITEM_G].segments}
    assert j[4].value == "core" and j[4].promoted_by_user is True
    assert j[1].value == "metadata" and j[6].value == "boilerplate" and j[2].promoted_by_user is False
    assert [s.value for s in maps[F.ITEM_H].segments] == [s[4] for s in F.OBJECTION.sections]   # other file untouched
    assert len([x for x in db.ops(so.TABLE) if x[1] == "select"]) == 1
    # the stored rows are not changed
    stored = next(r for r in db.tables["attachment_segments"] if r["item_id"] == F.FILE_J and r["seq"] == 4)
    assert stored["value"] == "supporting"


def test_the_promotion_follows_the_file_into_another_conversation():
    db = _db_two_files()
    _promote(db, F.FILE_J, F.JUDGMENT, 4)
    # the same file attached again in a third conversation (another item id)
    other = {**db.tables["workspace_items"][0], "item_id": F.ITEM_K, "conversation_id": "convo-11"}
    db.tables["workspace_items"].append(other)
    maps = load_segment_maps(db, [F.ITEM_K], user_id=USER, content_chars={F.ITEM_K: len(other["content_md"])})
    assert {s.seq: s.value for s in maps[F.ITEM_K].segments}[4] == "core"


def test_a_reverted_promotion_is_gone():
    db = _db_two_files()
    _promote(db, F.FILE_J, F.JUDGMENT, 4)
    assert revert_segment_override(db, db.tables[so.TABLE][0]["id"], USER)
    maps = load_segment_maps(db, [F.ITEM_G], user_id=USER, content_chars=_lengths(db))
    assert {s.seq: s.value for s in maps[F.ITEM_G].segments}[4] == "supporting"


def test_failed_override_read_leaves_the_maps_unchanged():
    db = _db_two_files()
    _promote(db, F.FILE_J, F.JUDGMENT, 4)
    db.fail.add(so.TABLE)
    maps = load_segment_maps(db, [F.ITEM_G, F.ITEM_H], user_id=USER, content_chars=_lengths(db))
    assert set(maps) == {F.ITEM_G, F.ITEM_H}
    assert {s.seq: s.value for s in maps[F.ITEM_G].segments}[4] == "supporting"


def test_hook_error_leaves_the_maps_unchanged(monkeypatch):
    import agents.memory.attachment_segmenter.store as store

    db = _db_two_files()
    _promote(db, F.FILE_J, F.JUDGMENT, 4)
    monkeypatch.setattr(store, "apply_segment_overrides", lambda *a, **k: 1 / 0)
    maps = load_segment_maps(db, [F.ITEM_G], user_id=USER, content_chars=_lengths(db))
    assert {s.seq: s.value for s in maps[F.ITEM_G].segments}[4] == "supporting"


def test_no_promotions_same_maps_as_before():
    db = _db_two_files()
    maps = load_segment_maps(db, [F.ITEM_G, F.ITEM_H], user_id=USER, content_chars=_lengths(db))
    assert all(not s.promoted_by_user for m in maps.values() for s in m.segments)
    assert [s.value for s in maps[F.ITEM_G].segments] == [s[4] for s in F.JUDGMENT.sections]


def test_another_users_promotion_is_ignored():
    db = _db_two_files()
    row = next(r for r in db.tables["attachment_segments"] if r["item_id"] == F.FILE_J and r["seq"] == 4)
    save_segment_override(db, file_id=F.FILE_J, user_id="user-8", char_start=row["char_start"],
                          char_end=row["char_end"], evidence="e")
    maps = load_segment_maps(db, [F.ITEM_G], user_id=USER, content_chars=_lengths(db))
    assert {s.seq: s.value for s in maps[F.ITEM_G].segments}[4] == "supporting"


def test_the_angle_index_sees_the_promotion():
    from agents.memory.angle_mapper.labels import section_index

    db = _db_two_files()
    _promote(db, F.FILE_J, F.JUDGMENT, 4)
    maps = load_segment_maps(db, [F.ITEM_G], user_id=USER, content_chars=_lengths(db))
    index = section_index([{"doc_index": 1, "item_id": F.ITEM_G}], maps)
    assert index["D1.S4"]["value"] == "core" and index["D1.S4"]["promoted_by_user"] is True


# ── the flag gate (only the attachment agent writes promotions) ───────────────


@pytest.mark.parametrize("users", [None, "someone-else"])
def test_flag_off_reads_no_promotions_at_all(monkeypatch, users):
    from shared.config import get_settings

    db = _db_two_files()
    _promote(db, F.FILE_J, F.JUDGMENT, 4)
    monkeypatch.setattr(get_settings(), "ATTACHMENT_AGENT_ENABLED", users is not None)
    monkeypatch.setattr(get_settings(), "ATTACHMENT_AGENT_USERS", users)
    maps = load_segment_maps(db, [F.ITEM_G], user_id=USER, content_chars=_lengths(db))
    assert {s.seq: s.value for s in maps[F.ITEM_G].segments}[4] == "supporting"
    assert [x for x in db.ops(so.TABLE) if x[1] == "select"] == []


def test_without_a_user_the_maps_owner_decides():
    db = _db_two_files()
    _promote(db, F.FILE_J, F.JUDGMENT, 4)
    maps = load_segment_maps(db, [F.ITEM_G], content_chars=_lengths(db))     # helper-frame style: no user
    assert {s.seq: s.value for s in maps[F.ITEM_G].segments}[4] == "core"
    assert maps[F.ITEM_G].file_key == F.FILE_J                               # readers reuse it
