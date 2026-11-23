"""Store (migration 228, one map per message group) — the publish-last replace, the
rollback of every failing step, two writers for one group, read-back from any
member, and the freshness check against the group's members today. Nothing here
raises."""
from __future__ import annotations

import logging
import random
import uuid

import pytest

from agents.memory.angle_mapper import store as store_mod
from agents.memory.angle_mapper.store import (
    ANGLE_COLUMNS,
    ANGLES_TABLE,
    ITEM_COLUMNS,
    ITEMS_TABLE,
    MAP_COLUMNS,
    MAPS_TABLE,
    LEFT_OUT_KEYS,
    load_angle_map_for_item,
    load_angle_map_for_items,
    load_fresh_angle_map_for_item,
    write_angle_map,
)
from agents.memory.attachment_segmenter.paragraphs import PARAGRAPH_RULE_VERSION

from . import fixtures as F
from .fake_supabase import FakeSupabase

A, B, C = F.ITEM_A, F.ITEM_B, F.ITEM_C


def items(*ids: str, chars: int = 5000) -> list[dict]:
    return [
        {"item_id": i, "doc_index": n, "content_chars": chars, "segment_prompt_version": F.SEG_VERSION,
         "paragraph_rule_version": PARAGRAPH_RULE_VERSION, "segmented_at": F.SEG_CREATED_AT}
        for n, i in enumerate(ids, start=1)
    ]


def group_items(*docs: F.Doc) -> list[dict]:
    """What the runner writes for ``docs`` as stored by the fixtures (``GroupDoc.item_record``)."""
    out = items(*(d.item_id for d in docs))
    for it, d in zip(out, docs):
        it["content_chars"] = len(F.content(d))
    return out


def map_row(**kw) -> dict:
    row = {
        "prompt_version": "angles-v5",
        "model": "wafer-deepseek-v4.1-flash-1017",
        "client": dict(F.CLIENT_TWO),
        "context": ["D1.S1", "D2.S1"],
        "gaps": [{"kind": "contradiction", "sections": ["D1.S5", "D2.S2"], "issue": "y", "ocr_suspect": False}],
        "no_angle": [{"section": "D1.S6", "why": "z"}],
        "unrelated_documents": [],
        "input_tokens": 1200,
        "output_tokens": 800,
        "reasoning_tokens": 300,
        "cost_usd": 0.0123,
        "duration_ms": 45000,
    }
    row.update(kw)
    return row


def angles(n: int = 3, **kw) -> list[dict]:
    return [
        {"seq": i, "for_side": "respondent", "for_party": "المدعى عليه", "kind": "strengthen",
         "title": f"وجه {i}", "argument": "حجة", "sections": [f"D1.S{i}", f"D2.S{i}"], "legal_question": "سؤال؟",
         "strength": "strong", "weakness": "", **kw}
        for i in range(1, n + 1)
    ]


def write(db: FakeSupabase, *ids: str, message: str | None = F.MSG, row: dict | None = None,
          ang: list[dict] | None = None, members: list[dict] | None = None) -> str | None:
    return write_angle_map(
        db, message_id=message, conversation_id=F.CONVO, user_id=F.USER,
        items=members if members is not None else items(*(ids or (A, B))),
        map_row=row if row is not None else map_row(), angles=ang if ang is not None else angles(),
    )


def maps(db: FakeSupabase) -> list[dict]:
    return db.tables[MAPS_TABLE]


def members_of(db: FakeSupabase, map_id: str) -> list[str]:
    rows = sorted((r for r in db.tables[ITEMS_TABLE] if r["map_id"] == map_id), key=lambda r: r["doc_index"])
    return [r["item_id"] for r in rows]


def angles_of(db: FakeSupabase, map_id: str) -> list[dict]:
    return [r for r in db.tables[ANGLES_TABLE] if r["map_id"] == map_id]


# ── write ────────────────────────────────────────────────────────────────────


def test_write_then_load_from_any_member():
    db = FakeSupabase()
    map_id = write(db)
    assert map_id and uuid.UUID(map_id)
    [m] = maps(db)
    assert set(m) == {"map_id", "message_id", "conversation_id", "user_id", *MAP_COLUMNS}
    assert (m["map_id"], m["message_id"], m["conversation_id"], m["user_id"]) == (map_id, F.MSG, F.CONVO, F.USER)
    assert m["cost_usd"] == 0.0123 and m["context"] == ["D1.S1", "D2.S1"]
    assert m["client"]["by_document"] == [{"document": "D2", "side": "claimant", "party": "المعترض"}]
    assert members_of(db, map_id) == [A, B]
    assert all(set(r) == {"map_id", *ITEM_COLUMNS} for r in db.tables[ITEMS_TABLE])
    rows = angles_of(db, map_id)
    assert [r["seq"] for r in rows] == [1, 2, 3] and all(set(r) == {"map_id", *ANGLE_COLUMNS} for r in rows)
    # publish last: the map + angles first (unreachable), then old maps out, then message + members
    assert db.ops(MAPS_TABLE, ITEMS_TABLE, ANGLES_TABLE) == [
        (MAPS_TABLE, "insert"), (ANGLES_TABLE, "insert"), (MAPS_TABLE, "select"), (ITEMS_TABLE, "select"),
        (MAPS_TABLE, "update"), (ITEMS_TABLE, "insert"),
    ]

    out_a, out_b = load_angle_map_for_item(db, A), load_angle_map_for_item(db, B)
    assert out_a == out_b and set(out_a) == {"map", "items", "angles"}
    assert out_a["map"]["map_id"] == map_id and out_a["map"]["prompt_version"] == "angles-v5"
    assert [(i["doc_index"], i["item_id"]) for i in out_a["items"]] == [(1, A), (2, B)]
    assert [a["seq"] for a in out_a["angles"]] == [1, 2, 3]
    assert out_a["angles"][1]["sections"] == ["D1.S2", "D2.S2"]


def test_values_are_made_json_ready():
    db = FakeSupabase()
    row = map_row(
        input_tokens=None, output_tokens="12", reasoning_tokens="x", duration_ms=1.9, cost_usd=None,
        context=["d1·s3", 4, "", None], client={"side": "respondent", "by_document": [{"document": "d2"}]},
        gaps=[{"kind": "missing", "sections": ["D2-S02", ""], "issue": "x"}], no_angle=[{"section": "d1 s6"}],
        unrelated_documents=[{"document": 3, "why": "w"}], extra="ignored",
    )
    a = angles(2)
    a[0].pop("seq")
    a[1].update(seq="2", title=None, sections=["d1·s7", 8, ""], extra="ignored")
    members = items(A, B)
    members[0].update(content_chars="5000", doc_index="1", extra="ignored")
    map_id = write(db, row=row, ang=a, members=members)
    assert map_id
    [m] = maps(db)
    assert (m["input_tokens"], m["output_tokens"], m["reasoning_tokens"], m["duration_ms"]) == (0, 12, 0, 1)
    assert m["cost_usd"] == 0.0 and isinstance(m["cost_usd"], float)
    assert m["context"] == ["D1.S3", "S4"]  # canonical refs, empties dropped
    assert m["client"] == {"side": "respondent", "by_document": [{"document": "D2"}]}
    assert m["gaps"][0]["sections"] == ["D2.S2"] and m["no_angle"] == [{"section": "D1.S6"}]
    assert m["unrelated_documents"] == [{"document": "D3", "why": "w"}]
    assert "extra" not in m
    rows = sorted(angles_of(db, map_id), key=lambda r: r["seq"])
    assert [r["seq"] for r in rows] == [1, 2]  # a missing seq takes its position
    assert rows[1]["title"] == "" and rows[1]["sections"] == ["D1.S7", "S8"] and "extra" not in rows[1]
    first = next(r for r in db.tables[ITEMS_TABLE] if r["item_id"] == A)
    assert first["content_chars"] == 5000 and first["doc_index"] == 1 and "extra" not in first


def test_the_angles_ocr_suspect_is_always_written_as_a_bool():
    db = FakeSupabase()
    a = angles(5)
    for row, flag in zip(a, (True, "true", None, "false")):
        row["ocr_suspect"] = flag  # the fifth angle carries no ocr_suspect at all
    map_id = write(db, ang=a)
    rows = sorted(angles_of(db, map_id), key=lambda r: r["seq"])
    assert [r["ocr_suspect"] for r in rows] == [True, True, False, False, False]
    assert all(type(r["ocr_suspect"]) is bool for r in rows)  # NOT NULL boolean: never None, never text
    out = load_angle_map_for_item(db, A)
    assert [x["ocr_suspect"] for x in out["angles"]] == [True, True, False, False, False]


def test_null_client_and_lists_are_stored_empty():
    db = FakeSupabase()
    assert write(db, row=map_row(client=None, gaps=None, no_angle=None, unrelated_documents=None, context=None))
    [m] = maps(db)
    assert m["client"] == {"by_document": []} and m["gaps"] == [] and m["no_angle"] == []
    assert m["unrelated_documents"] == [] and m["context"] == []


def test_a_file_with_no_message_link():
    db = FakeSupabase()
    map_id = write(db, A, message=None)
    [m] = maps(db)
    assert m["message_id"] is None and members_of(db, map_id) == [A]
    assert (MAPS_TABLE, "update") not in db.ops()
    assert load_angle_map_for_item(db, A)["map"]["map_id"] == map_id


def test_replace_the_message_map():
    db = FakeSupabase()
    old = write(db, A, ang=angles(5))
    new = write(db, A, B, row=map_row(model=None, no_angle=[]), ang=angles(2))
    assert old and new and old != new
    [m] = maps(db)
    assert m["map_id"] == new and m["model"] is None and m["no_angle"] == []
    assert members_of(db, new) == [A, B] and members_of(db, old) == []  # the old members cascaded
    assert angles_of(db, old) == [] and len(angles_of(db, new)) == 2
    assert load_angle_map_for_item(db, A)["map"]["map_id"] == new


def test_replace_takes_a_member_out_of_its_old_map():
    """A member found in another map (a map of one, before its message link) leaves it."""
    db = FakeSupabase()
    alone = write(db, B, message=None)
    new = write(db, A, B)
    assert [m["map_id"] for m in maps(db)] == [new]
    assert members_of(db, alone) == [] and load_angle_map_for_item(db, B)["map"]["map_id"] == new


def test_other_groups_are_untouched():
    db = FakeSupabase()
    other = write(db, C, message="msg-2", ang=angles(2))
    mine = write(db, A, B)
    write(db, A, B)  # replace mine again
    assert sorted(m["message_id"] for m in maps(db)) == [F.MSG, "msg-2"]
    assert members_of(db, other) == [C] and len(angles_of(db, other)) == 2
    assert load_angle_map_for_item(db, C)["map"]["map_id"] == other
    assert load_angle_map_for_item(db, A)["map"]["map_id"] != mine  # replaced


def test_a_map_with_no_angles_is_stored_without_an_angle_insert():
    db = FakeSupabase()
    map_id = write(db, ang=[])
    assert map_id and (ANGLES_TABLE, "insert") not in db.ops()
    assert load_angle_map_for_item(db, A)["angles"] == []


@pytest.mark.parametrize("user_id, members", [("", items(A)), (None, items(A)), (F.USER, []), (F.USER, None)])
def test_no_user_or_no_members_writes_nothing(user_id, members):
    db = FakeSupabase()
    assert write_angle_map(db, message_id=F.MSG, conversation_id=F.CONVO, user_id=user_id, items=members,
                           map_row=map_row(), angles=angles()) is None
    assert db.log == []


@pytest.mark.parametrize("change", [
    lambda it, rows, ang: it[1].update(doc_index=3),          # doc_index must be 1..n
    lambda it, rows, ang: it[1].update(doc_index=1),          # … and unique
    lambda it, rows, ang: it[1].update(item_id=A),            # an item listed twice
    lambda it, rows, ang: it[0].update(item_id=None),         # a member without an id
    lambda it, rows, ang: rows.update(client="not a dict"),
    lambda it, rows, ang: ang[0].update(sections=5),          # not a list of refs
])
def test_a_malformed_group_writes_nothing(change):
    db = FakeSupabase()
    it, row, ang = items(A, B), map_row(), angles()
    change(it, row, ang)
    assert write(db, row=row, ang=ang, members=it) is None
    assert db.log == []


# ── rollback on each failing step ────────────────────────────────────────────


def _with_old_map() -> tuple[FakeSupabase, str]:
    db = FakeSupabase()
    old = write(db, A, B, row=map_row(prompt_version="angles-old"), ang=angles(4))
    db.log.clear()
    return db, old


def _old_intact(db: FakeSupabase, old: str) -> None:
    db.fail.clear()  # read back with the database healthy again
    [m] = maps(db)
    assert m["map_id"] == old and m["prompt_version"] == "angles-old" and m["message_id"] == F.MSG
    assert members_of(db, old) == [A, B] and len(angles_of(db, old)) == 4
    assert load_angle_map_for_item(db, B)["map"]["map_id"] == old


def test_step1_map_row_not_stored_keeps_the_old_map(caplog):
    db, old = _with_old_map()
    db.fail.add((MAPS_TABLE, "insert"))
    with caplog.at_level(logging.WARNING):
        assert write(db) is None
    assert db.ops() == [(MAPS_TABLE, "insert")]
    assert "fake failure" not in caplog.text  # the class name only, never the message
    _old_intact(db, old)


def test_step2_angles_not_stored_drops_the_new_map_and_keeps_the_old():
    db, old = _with_old_map()
    db.fail.add((ANGLES_TABLE, "insert"))
    assert write(db) is None
    assert db.ops()[-1] == (MAPS_TABLE, "delete")
    _old_intact(db, old)


def test_a_duplicate_angle_seq_is_a_failed_insert_and_rolls_back():
    db, old = _with_old_map()
    assert write(db, ang=angles(1) + angles(1)) is None
    _old_intact(db, old)


@pytest.mark.parametrize("fail", [(MAPS_TABLE, "select"), (ITEMS_TABLE, "select")])
def test_step3_old_maps_not_found_drops_the_new_map_and_keeps_the_old(fail):
    db, old = _with_old_map()
    db.fail.add(fail)
    assert write(db) is None
    _old_intact(db, old)


def test_step3_old_maps_not_deleted_leaves_the_old_map_reachable():
    db, old = _with_old_map()
    db.fail.add((MAPS_TABLE, "delete"))  # the rollback delete fails too
    assert write(db) is None
    # the new row could not be removed — an orphan with no members, invisible to readers
    assert load_angle_map_for_item(db, A)["map"]["map_id"] == old
    orphans = [m for m in maps(db) if m["map_id"] != old]
    assert len(orphans) == 1 and orphans[0]["message_id"] is None and members_of(db, orphans[0]["map_id"]) == []


@pytest.mark.parametrize("fail", [(MAPS_TABLE, "update"), (ITEMS_TABLE, "insert")])
def test_steps4_5_publish_failure_leaves_no_map_never_a_half_one(fail):
    db, _old = _with_old_map()
    db.fail.add(fail)
    assert write(db) is None
    assert maps(db) == [] and db.tables[ITEMS_TABLE] == [] and db.tables[ANGLES_TABLE] == []
    assert load_angle_map_for_item(db, A) is None


def test_a_failed_rollback_still_returns_none():
    db, old = _with_old_map()
    db.fail |= {(ANGLES_TABLE, "insert"), (MAPS_TABLE, "delete")}
    assert write(db) is None
    assert load_angle_map_for_item(db, A)["map"]["map_id"] == old


def test_everything_down_never_raises():
    db = FakeSupabase(fail={MAPS_TABLE, ANGLES_TABLE, ITEMS_TABLE})
    assert write(db) is None
    assert load_angle_map_for_item(db, A) is None
    assert load_fresh_angle_map_for_item(db, A) is None


# ── two writers for one group: the second publisher loses cleanly ────────────


def test_two_writers_the_second_hits_unique_message_id(monkeypatch):
    db = FakeSupabase()
    first = write(db)
    # the second writer read the old maps BEFORE the first one published
    monkeypatch.setattr(store_mod, "_old_map_ids", lambda *a, **k: [])
    assert write(db, ang=angles(1)) is None
    assert [m["map_id"] for m in maps(db)] == [first]
    assert members_of(db, first) == [A, B] and len(angles_of(db, first)) == 3


def test_two_writers_without_a_message_the_second_hits_unique_item_id(monkeypatch):
    db = FakeSupabase()
    first = write(db, A, message=None)
    monkeypatch.setattr(store_mod, "_old_map_ids", lambda *a, **k: [])
    assert write(db, A, message=None) is None
    assert [m["map_id"] for m in maps(db)] == [first] and members_of(db, first) == [A]


# ── read ─────────────────────────────────────────────────────────────────────


def _seeded(n_angles: int = 3, **map_kw) -> FakeSupabase:
    map_id = "m-1"
    rows = angles(n_angles)
    random.Random(4).shuffle(rows)
    return FakeSupabase({
        MAPS_TABLE: [{"map_id": map_id, "message_id": F.MSG, "user_id": F.USER, "prompt_version": "angles-v5",
                      **map_kw}],
        ITEMS_TABLE: [{"map_id": map_id, "item_id": B, "doc_index": 2}, {"map_id": map_id, "item_id": A,
                                                                         "doc_index": 1}],
        # text seqs: the fake orders them as text ("1", "10", "11", "12", "2"…) — the loader
        # still returns them by number
        ANGLES_TABLE: [{**r, "map_id": map_id, "seq": str(r["seq"]), "sections": [s.lower() for s in r["sections"]]}
                       for r in rows],
    })


def test_load_orders_and_normalises():
    db = _seeded(12, client=None, context=["d1·s2", 3], gaps=None, no_angle=None, unrelated_documents=None,
                 cost_usd="0.0105")
    out = load_angle_map_for_item(db, B)
    assert [a["seq"] for a in out["angles"]] == list(range(1, 13))
    assert out["angles"][0]["sections"] == ["D1.S1", "D2.S1"]
    assert [(i["doc_index"], i["item_id"]) for i in out["items"]] == [(1, A), (2, B)]
    m = out["map"]
    assert m["client"] == {"by_document": []} and m["context"] == ["D1.S2", "S3"]
    assert m["gaps"] == [] and m["no_angle"] == [] and m["unrelated_documents"] == []
    assert m["cost_usd"] == 0.0105 and m["user_id"] == F.USER and m["message_id"] == F.MSG


def test_load_reads_ocr_suspect_as_a_bool():
    db = _seeded(4)
    flags = {1: True, 2: "true", 3: None}  # seq 4: no key at all → the column default
    for r in db.tables[ANGLES_TABLE]:
        if int(r["seq"]) in flags:
            r["ocr_suspect"] = flags[int(r["seq"])]
    out = load_angle_map_for_item(db, A)
    assert [a["ocr_suspect"] for a in out["angles"]] == [True, True, False, False]


def test_load_pages_past_a_thousand_angles():
    db = _seeded(1001)
    out = load_angle_map_for_item(db, A)
    assert len(out["angles"]) == 1001 and out["angles"][-1]["seq"] == 1001
    assert db.ops(ANGLES_TABLE) == [(ANGLES_TABLE, "select")] * 2


def test_load_a_map_with_no_angles():
    db = _seeded(0)
    assert load_angle_map_for_item(db, A)["angles"] == []


@pytest.mark.parametrize("item_id", [C, "", None])
def test_load_without_a_map(item_id):
    assert load_angle_map_for_item(_seeded(), item_id) is None


def test_a_member_row_whose_map_is_gone_loads_none():
    db = _seeded()
    db.tables[MAPS_TABLE] = []  # the fake does not cascade a direct table reset
    assert load_angle_map_for_item(db, A) is None


@pytest.mark.parametrize("fail", [MAPS_TABLE, ITEMS_TABLE, ANGLES_TABLE])
def test_a_failed_read_is_none(fail):
    db = _seeded()
    db.fail.add(fail)
    assert load_angle_map_for_item(db, A) is None


# ── freshness: the stored map against the group's members today ──────────────


def _stored_group(*docs: F.Doc, message: str | None = F.MSG, **tables_kw) -> tuple[FakeSupabase, str]:
    docs = docs or (F.JUDGMENT, F.OBJECTION)
    db = FakeSupabase(F.tables(*docs, message=message, **tables_kw))
    map_id = write_angle_map(db, message_id=message, conversation_id=F.CONVO, user_id=F.USER,
                             items=group_items(*docs), map_row=map_row(), angles=angles())
    assert map_id
    return db, map_id


def test_a_fresh_map_loads_from_every_member():
    db, map_id = _stored_group()
    for item in (A, B):
        out = load_fresh_angle_map_for_item(db, item)
        assert out is not None and out["map"]["map_id"] == map_id
    assert db.writes()[-1] == (ITEMS_TABLE, "insert")  # reads only after the write
    assert load_fresh_angle_map_for_item(db, C) is None


def test_a_group_of_one_without_a_message_is_fresh():
    db, map_id = _stored_group(F.JUDGMENT, message=None)
    assert load_fresh_angle_map_for_item(db, A)["map"]["map_id"] == map_id


def _seg(db: FakeSupabase, item_id: str) -> dict:
    return next(r for r in db.tables["attachment_segment_maps"] if r["item_id"] == item_id)


def _item(db: FakeSupabase, item_id: str) -> dict:
    return next(r for r in db.tables["workspace_items"] if r["item_id"] == item_id)


def _add_member(db: FakeSupabase, doc: F.Doc) -> None:
    for k, v in F.tables(doc).items():
        if k != "messages":
            db.tables[k] += v


STALE = {
    "the text changed": lambda db: _item(db, B).update(content_md=_item(db, B)["content_md"] + " ملحق"),
    "re-segmented": lambda db: _seg(db, B).update(created_at="2999-01-01T00:00:00+00:00"),
    "another segment prompt": lambda db: _seg(db, B).update(prompt_version="seg-v5.3"),
    "another paragraph rule": lambda db: _seg(db, B).update(paragraph_rule_version="v3"),
    "segments older than v5.2": lambda db: _seg(db, B).update(prompt_version="seg-v5.1"),
    "a stale segment map": lambda db: _seg(db, B).update(content_chars=1),
    "segment map removed": lambda db: db.tables.update(
        attachment_segment_maps=[r for r in db.tables["attachment_segment_maps"] if r["item_id"] != B]),
    "a member soft-deleted": lambda db: _item(db, B).update(deleted_at="2026-11-21T00:00:00+00:00"),
    "a new member was segmented": lambda db: _add_member(db, F.LEASE),
    "failed segment-map read": lambda db: db.fail.add("attachment_segment_maps"),
    "failed link read": lambda db: db.fail.add("message_attachments"),
    "failed item read": lambda db: db.fail.add("workspace_items"),
    "failed angles read": lambda db: db.fail.add(ANGLES_TABLE),
}

#: What only the TEXT shows (no header changes): the freshness check reads headers
#: only, so the map of a member already in it stays fresh — its sections are what
#: the map's refs name. (A run that maps the group again would leave B out.)
TEXT_ONLY = {
    "no sections": lambda db: db.tables.update(
        attachment_segments=[r for r in db.tables["attachment_segments"] if r["item_id"] != B]),
    "paragraphs no longer fit the sections": lambda db: next(
        r for r in db.tables["attachment_segments"] if r["item_id"] == B and r["seq"] == 2
    ).update(char_start=1),
    "a same-length edit moved its paragraphs": lambda db: _item(db, B).update(
        content_md=_item(db, B)["content_md"].replace("\n", " ", 1)),
    "retyped (another paragraph max)": lambda db: _item(db, B).update(metadata={"doc_type": "judgment"}),
}


@pytest.mark.parametrize("change", list(STALE.values()), ids=list(STALE))
def test_a_map_that_no_longer_fits_its_group_is_not_loaded(change):
    db, _ = _stored_group()
    assert load_fresh_angle_map_for_item(db, A) is not None
    change(db)
    assert load_fresh_angle_map_for_item(db, A) is None
    assert load_angle_map_for_item(db, A) is not None or ANGLES_TABLE in db.fail  # still stored, just stale


def test_a_member_still_being_segmented_does_not_count_yet():
    db, map_id = _stored_group()
    db.tables["workspace_items"].append(F.item_row(F.LEASE))
    db.tables["message_attachments"].append(F.link(F.LEASE))  # linked, no segment map yet
    assert load_fresh_angle_map_for_item(db, A)["map"]["map_id"] == map_id


def test_another_users_item_on_the_message_does_not_count():
    db, map_id = _stored_group()
    _add_member(db, F.LEASE)
    _item(db, C)["user_id"] = F.OTHER_USER
    _seg(db, C)["user_id"] = F.OTHER_USER
    assert load_fresh_angle_map_for_item(db, B)["map"]["map_id"] == map_id


def test_a_stored_member_without_segmented_at_compares_the_rest():
    db = FakeSupabase(F.tables(F.JUDGMENT, F.OBJECTION))
    members = group_items(F.JUDGMENT, F.OBJECTION)
    for it in members:
        it["segmented_at"] = None
    assert write_angle_map(db, message_id=F.MSG, conversation_id=F.CONVO, user_id=F.USER, items=members,
                           map_row=map_row(), angles=[])
    _seg(db, B).update(created_at="2999-01-01T00:00:00+00:00")
    assert load_fresh_angle_map_for_item(db, A) is not None


@pytest.mark.parametrize("change", list(TEXT_ONLY.values()), ids=list(TEXT_ONLY))
def test_what_only_the_text_shows_does_not_make_a_map_stale(change):
    """The freshness check reads HEADERS only (no segments, no paragraphs): a
    member already in the map is judged by its versions."""
    db, map_id = _stored_group()
    change(db)
    n = len(db.selects)
    assert load_fresh_angle_map_for_item(db, A)["map"]["map_id"] == map_id
    assert not any(t == "attachment_segments" for t, _cols in db.selects[n:])


# ── left out: attachments the map left out after reading their text ──────────


def _left_out(doc: F.Doc, **kw) -> dict:
    """A ``left_out`` entry for ``doc`` at the versions the fixtures store it with."""
    entry = {"item_id": doc.item_id, "reason": "skipped_paragraph_rule", "content_chars": len(F.content(doc)),
             "segment_prompt_version": F.SEG_VERSION, "paragraph_rule_version": PARAGRAPH_RULE_VERSION,
             "segmented_at": F.SEG_CREATED_AT}
    entry.update(kw)
    return entry


def _stored_with_left_out(**entry_kw) -> tuple[FakeSupabase, str]:
    db = FakeSupabase(F.tables(F.JUDGMENT, F.OBJECTION, F.LEASE))
    map_id = write_angle_map(db, message_id=F.MSG, conversation_id=F.CONVO, user_id=F.USER,
                             items=group_items(F.JUDGMENT, F.OBJECTION),
                             map_row=map_row(left_out=[_left_out(F.LEASE, **entry_kw)]), angles=angles())
    assert map_id
    return db, map_id


def test_an_attachment_left_out_at_the_same_versions_keeps_the_map_fresh():
    db, map_id = _stored_with_left_out()
    for item in (A, B):
        assert load_fresh_angle_map_for_item(db, item)["map"]["map_id"] == map_id
    assert load_fresh_angle_map_for_item(db, C) is None  # in no map itself
    assert load_angle_map_for_items(db, [C, A, B])["map"]["map_id"] == map_id  # the runner finds it from any


@pytest.mark.parametrize("change", [
    lambda db: _seg(db, C).update(created_at="2999-01-01T00:00:00+00:00"),   # segmented again
    lambda db: _seg(db, C).update(prompt_version="seg-v5.3"),
], ids=["re-segmented", "another segment prompt"])
def test_a_left_out_attachment_that_changed_makes_the_map_stale(change):
    db, _ = _stored_with_left_out()
    change(db)
    assert load_fresh_angle_map_for_item(db, A) is None


def test_a_left_out_entry_at_other_versions_is_stale():
    db, _ = _stored_with_left_out(content_chars=12)
    assert load_fresh_angle_map_for_item(db, A) is None


@pytest.mark.parametrize("change", [
    lambda db: _item(db, C).update(deleted_at="2026-11-21T00:00:00+00:00"),
    lambda db: _seg(db, C).update(prompt_version="seg-v5.1"),
    # its text changed but its segment map did not follow: stale map, not usable
    lambda db: _item(db, C).update(content_md=_item(db, C)["content_md"] + " ملحق"),
], ids=["deleted", "no longer header-usable", "text changed, map not yet"])
def test_a_left_out_attachment_that_is_no_longer_usable_is_ignored(change):
    db, map_id = _stored_with_left_out()
    change(db)
    assert load_fresh_angle_map_for_item(db, A)["map"]["map_id"] == map_id


def test_left_out_is_stored_json_ready_with_its_keys_only():
    db = FakeSupabase(F.tables(F.JUDGMENT, F.OBJECTION))
    entry = {**_left_out(F.LEASE), "content_chars": "777", "extra": "dropped"}
    map_id = write(db, row=map_row(left_out=[entry]))
    [stored] = maps(db)
    assert stored["left_out"] == [{**_left_out(F.LEASE), "content_chars": 777}]
    assert set(stored["left_out"][0]) == set(LEFT_OUT_KEYS)
    assert load_angle_map_for_item(db, A)["map"]["left_out"] == stored["left_out"]
    assert write(db, row=map_row()) and maps(db)[0]["left_out"] == []  # none → []
    assert map_id


# ── the runner's reader: the ONE map holding a group's attachments ────────────


def test_load_for_items_finds_the_one_map():
    db = FakeSupabase()
    map_id = write(db, A, B)
    assert load_angle_map_for_items(db, [C, B])["map"]["map_id"] == map_id
    assert load_angle_map_for_items(db, [C]) is None and load_angle_map_for_items(db, []) is None
    other = write(db, C, message="msg-other", members=items(C))
    assert other and load_angle_map_for_items(db, [A, C]) is None  # spread over two maps: not one group's
    db.fail.add(ITEMS_TABLE)
    assert load_angle_map_for_items(db, [A]) is None


# ── files («الملفات»): the item is the conversation's, the segment map the file's ─


def test_a_library_file_is_fresh_through_its_files_segment_map():
    """The map's member is the CONVERSATION's item (G, H); their segment maps hang on
    their FILES only — the freshness check finds them through the file mapping."""
    t = F.file_tables((F.JUDGMENT, F.ITEM_G, F.FILE_J), (F.OBJECTION, F.ITEM_H, F.FILE_O))
    db = FakeSupabase(t)
    members = [{**it, "item_id": i} for it, i in zip(group_items(F.JUDGMENT, F.OBJECTION), (F.ITEM_G, F.ITEM_H))]
    map_id = write_angle_map(db, message_id=F.MSG_2, conversation_id=F.CONVO_2, user_id=F.USER, items=members,
                             map_row=map_row(), angles=angles())
    assert map_id and members_of(db, map_id) == [F.ITEM_G, F.ITEM_H]
    for item in (F.ITEM_G, F.ITEM_H):
        assert load_fresh_angle_map_for_item(db, item)["map"]["map_id"] == map_id
    # the FILE is segmented again: every conversation item pointing at it is stale
    next(r for r in db.tables["attachment_segment_maps"] if r["item_id"] == F.FILE_O)["created_at"] = "2999-01-01"
    assert load_fresh_angle_map_for_item(db, F.ITEM_G) is None


def test_one_file_in_two_conversations_has_two_maps():
    """A file attached from «الملفات» to a second conversation is a new item: its
    group there gets its own map; writing it leaves the first conversation's map."""
    t = F.tables(F.JUDGMENT)
    t["workspace_items"][0]["file_id"] = A
    t["workspace_items"].append(F.item_row(F.JUDGMENT, item_id=F.ITEM_G, conversation_id=F.CONVO_2, file_id=A))
    t["message_attachments"].append({"message_id": F.MSG_2, "document_id": F.ITEM_G, "created_at": F.MSG_2_AT})
    db = FakeSupabase(t)
    first = write_angle_map(db, message_id=F.MSG, conversation_id=F.CONVO, user_id=F.USER,
                            items=group_items(F.JUDGMENT), map_row=map_row(), angles=angles())
    second = write_angle_map(db, message_id=F.MSG_2, conversation_id=F.CONVO_2, user_id=F.USER,
                             items=[{**group_items(F.JUDGMENT)[0], "item_id": F.ITEM_G}], map_row=map_row(),
                             angles=angles())
    assert first and second and first != second
    assert load_fresh_angle_map_for_item(db, A)["map"]["map_id"] == first
    assert load_fresh_angle_map_for_item(db, F.ITEM_G)["map"]["map_id"] == second
