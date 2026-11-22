"""Store — write (replace) and read back (``load_segment_maps``)."""
from __future__ import annotations

import pytest

from agents.memory.attachment_segmenter.models import StoredMap
from agents.memory.attachment_segmenter.store import (
    StoreError,
    load_segment_map,
    load_segment_maps,
    map_state,
    write_segment_map,
)

from .fake_supabase import FakeSupabase

ITEM_A = "aaaaaaaa-0000-0000-0000-000000000001"
ITEM_B = "bbbbbbbb-0000-0000-0000-000000000002"
USER = "user-1"
CONTENT = "رأس الحكم\nأسباب الحكم: وحيث إن\nمنطوق الحكم: رفض الدعوى"


def _item(item_id=ITEM_A, content=CONTENT, **kw):
    return {"item_id": item_id, "user_id": USER, "content_md": content, "deleted_at": None, **kw}


def _map_row(item_id=ITEM_A, content_chars=len(CONTENT), **kw):
    return {
        "item_id": item_id,
        "user_id": USER,
        "paragraph_rule_version": "lines-1200-v1",
        "prompt_version": "seg-v3.1-p2p4",
        "model": "baseten-deepseek-v4-flash-0731",
        "split": False,
        "documents_in_file": [{"from_p": 1, "to_p": 3, "name": "حكم"}],
        "key_paragraphs": [{"p": 3, "why": "المنطوق"}],
        "content_chars": content_chars,
        **kw,
    }


def _seg_rows(item_id=ITEM_A):
    lines = CONTENT.split("\n")
    rows, pos = [], 0
    for seq, (line, value) in enumerate(zip(lines, ["metadata", "core", "core"]), start=1):
        start = CONTENT.index(line, pos)
        pos = start + len(line)
        rows.append({
            "item_id": item_id, "seq": seq, "from_p": seq, "to_p": seq, "page_from": None, "page_to": None,
            "char_start": start, "char_end": pos, "label": ["header", "grounds", "ruling"][seq - 1],
            "value": value, "what": f"وصف {seq}", "content_md": line,
        })
    return rows


def test_round_trip():
    db = FakeSupabase({"workspace_items": [_item()]})
    write_segment_map(db, _map_row(), list(reversed(_seg_rows())))
    out = load_segment_maps(db, [ITEM_A])
    assert set(out) == {ITEM_A}
    m = out[ITEM_A]
    assert isinstance(m, StoredMap)
    assert [s.seq for s in m.segments] == [1, 2, 3]  # document order, whatever the insert order
    assert [s.value for s in m.segments] == ["metadata", "core", "core"]
    assert "".join(s.content_md for s in m.segments) == CONTENT.replace("\n", "")
    assert m.content_chars == len(CONTENT)
    assert m.documents_in_file[0]["name"] == "حكم"
    assert [s.what for s in m.by_value("core")] == ["وصف 2", "وصف 3"]
    assert map_state(db, ITEM_A) == (len(CONTENT), True)


def test_single_item_loader():
    db = FakeSupabase({"workspace_items": [_item()]})
    assert load_segment_map(db, ITEM_A) is None
    write_segment_map(db, _map_row(), _seg_rows())
    m = load_segment_map(db, ITEM_A)
    assert m is not None and m.item_id == ITEM_A and len(m.segments) == 3


def test_rewrite_replaces_the_old_map():
    db = FakeSupabase({"workspace_items": [_item()]})
    write_segment_map(db, _map_row(), _seg_rows())
    rows = _seg_rows()[:2]
    rows[1] = {**rows[1], "to_p": 3, "char_end": len(CONTENT), "content_md": CONTENT[rows[1]["char_start"]:]}
    write_segment_map(db, _map_row(prompt_version="v-next"), rows)
    m = load_segment_maps(db, [ITEM_A])[ITEM_A]
    assert m.prompt_version == "v-next"
    assert len(m.segments) == 2
    assert len(db.tables["attachment_segment_maps"]) == 1


def test_stale_map_is_dropped():
    db = FakeSupabase({"workspace_items": [_item(content=CONTENT + " (معدل)")]})
    write_segment_map(db, _map_row(), _seg_rows())
    assert load_segment_maps(db, [ITEM_A]) == {}
    assert map_state(db, ITEM_A) == (len(CONTENT), True)  # the runner compares


def test_soft_deleted_or_missing_items_are_dropped():
    db = FakeSupabase({"workspace_items": [_item(deleted_at="2026-11-11T00:00:00Z")]})
    write_segment_map(db, _map_row(), _seg_rows())
    assert load_segment_maps(db, [ITEM_A]) == {}
    assert load_segment_maps(FakeSupabase(), [ITEM_A]) == {}


def test_map_without_segments_is_dropped():
    db = FakeSupabase({"workspace_items": [_item()], "attachment_segment_maps": [_map_row()]})
    assert load_segment_maps(db, [ITEM_A]) == {}
    assert map_state(db, ITEM_A) == (len(CONTENT), False)


def test_user_scope_and_caller_lengths():
    db = FakeSupabase({"workspace_items": [_item(), _item(ITEM_B, user_id="other")]})
    write_segment_map(db, _map_row(), _seg_rows())
    write_segment_map(db, _map_row(ITEM_B, user_id="other"), _seg_rows(ITEM_B))
    assert set(load_segment_maps(db, [ITEM_A, ITEM_B])) == {ITEM_A, ITEM_B}
    assert set(load_segment_maps(db, [ITEM_A, ITEM_B], user_id=USER)) == {ITEM_A}
    # caller-supplied lengths skip the workspace_items read
    db.fail.add("workspace_items")
    out = load_segment_maps(db, [ITEM_A, ITEM_B], content_chars={ITEM_A: len(CONTENT), ITEM_B: 1})
    assert set(out) == {ITEM_A}


def test_many_segments_are_paged():
    content = "\n".join(f"س{i}" for i in range(1, 2501))
    db = FakeSupabase({"workspace_items": [_item(content=content)]})
    rows, pos = [], 0
    for i, line in enumerate(content.split("\n"), start=1):
        start = content.index(line, pos)
        pos = start + len(line)
        rows.append({"item_id": ITEM_A, "seq": i, "from_p": i, "to_p": i, "char_start": start, "char_end": pos,
                     "label": "x", "value": "core", "what": "", "content_md": line})
    write_segment_map(db, _map_row(content_chars=len(content)), rows)
    m = load_segment_maps(db, [ITEM_A])[ITEM_A]
    assert len(m.segments) == 2500
    assert [s.seq for s in m.segments] == list(range(1, 2501))


def test_read_failure_returns_empty():
    db = FakeSupabase({"workspace_items": [_item()]})
    write_segment_map(db, _map_row(), _seg_rows())
    db.fail.add("attachment_segments")
    assert load_segment_maps(db, [ITEM_A]) == {}
    assert load_segment_maps(db, []) == {}


def test_failed_segment_insert_removes_the_map():
    db = FakeSupabase({"workspace_items": [_item()]}, fail={("attachment_segments", "insert")})
    with pytest.raises(StoreError):
        write_segment_map(db, _map_row(), _seg_rows())
    assert db.tables["attachment_segment_maps"] == []


def test_failed_map_insert_raises():
    db = FakeSupabase({"workspace_items": [_item()]}, fail={("attachment_segment_maps", "insert")})
    with pytest.raises(StoreError):
        write_segment_map(db, _map_row(), _seg_rows())


# ── one file in two conversations (migrations 223 + 227) ─────────────────────
# The file's first item has file_id = item_id; a library file attached to a
# second conversation is a new item with the SAME file_id. Pages and maps hang
# on the file, so both items must find the one map.

COPY = "cccccccc-0000-0000-0000-000000000003"


def _two_copies(**kw):
    return FakeSupabase({"workspace_items": [
        _item(ITEM_A, file_id=ITEM_A, **kw),
        _item(COPY, file_id=ITEM_A, **kw),
    ]})


def test_second_copy_finds_the_files_map():
    db = _two_copies()
    write_segment_map(db, _map_row(), _seg_rows())
    out = load_segment_maps(db, [ITEM_A, COPY])
    assert set(out) == {ITEM_A, COPY}                     # keyed by the ids asked for
    assert out[COPY].item_id == COPY
    assert [s.seq for s in out[COPY].segments] == [1, 2, 3]
    assert load_segment_map(db, COPY) is not None
    assert map_state(db, COPY) == (len(CONTENT), True)
    assert len(db.tables["attachment_segment_maps"]) == 1  # one map, not one per copy


def test_a_write_through_the_second_copy_lands_on_the_file():
    db = _two_copies()
    write_segment_map(db, _map_row(COPY), _seg_rows(COPY))
    assert {r["item_id"] for r in db.tables["attachment_segment_maps"]} == {ITEM_A}
    assert {r["item_id"] for r in db.tables["attachment_segments"]} == {ITEM_A}
    assert map_state(db, ITEM_A) == (len(CONTENT), True)


def test_second_copy_reads_the_files_pages():
    from agents.memory.attachment_segmenter.store import load_pages

    db = _two_copies()
    db.tables["attachment_pages"] = [
        {"item_id": ITEM_A, "page_no": 2, "char_start": 10, "content_md": "ب"},
        {"item_id": ITEM_A, "page_no": 1, "char_start": 0, "content_md": "أ"},
    ]
    assert [p["page_no"] for p in load_pages(db, COPY)] == [1, 2]


def test_map_state_raises_when_the_file_lookup_fails():
    db = _two_copies()
    db.fail.add("workspace_items")
    with pytest.raises(Exception):
        map_state(db, COPY)
