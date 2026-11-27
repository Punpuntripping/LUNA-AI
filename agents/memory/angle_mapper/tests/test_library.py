"""«الملفات» files and their angle maps (owner 2026-11-27, ``angle_mapper/library.py``):
a copy carries its file's map whole, the map's members are pointed at this
conversation's copies, and a copy with no map anywhere is picked for a new map
only on Pro / Pro Plus / Max (dev = the test account). The mapper is not touched.

Offline (FakeSupabase); the mapper's freshness check is stubbed — its own tests
cover it. ``asyncio.run`` (CI has no pytest-asyncio).
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agents.memory.angle_mapper import library as lib
from agents.memory.angle_mapper import store as store_mod

from .fake_supabase import FakeSupabase

USER = "u-1"
OTHER_USER = "u-2"


def _item(item_id: str, file_id: str | None, convo: str, at: str, *, deleted: bool = False,
          user: str = USER) -> dict:
    return {
        "item_id": item_id, "file_id": file_id, "conversation_id": convo, "user_id": user,
        "kind": "attachment", "created_at": at, "deleted_at": "2026-11-26T00:00:00+00:00" if deleted else None,
    }


def _map(map_id: str, at: str, *, user: str = USER) -> dict:
    return {"map_id": map_id, "user_id": user, "created_at": at, "conversation_id": "c-old", "message_id": None}


def _member(map_id: str, item_id: str, doc_index: int) -> dict:
    return {"map_id": map_id, "item_id": item_id, "doc_index": doc_index}


@pytest.fixture(autouse=True)
def file_ids_on(monkeypatch):
    """The files read switch on (``file_id`` is selected) and LIBRARY_FILE_MAPS on."""
    monkeypatch.setattr(lib.files_store, "with_file_id", lambda cols, **_k: f"{cols}, file_id")
    monkeypatch.setattr(lib, "_enabled", lambda _user: True)


@pytest.fixture
def fresh(monkeypatch):
    """``store.load_fresh_angle_map_for_item`` → the map an item is a member of,
    unless its map id is in ``stale``; records the calls."""
    state: dict[str, Any] = {"stale": set(), "calls": [], "db": None}

    def load(supabase: Any, item_id: str) -> dict | None:
        state["calls"].append(item_id)
        links = [r for r in supabase.tables[store_mod.ITEMS_TABLE] if r["item_id"] == item_id]
        if not links or links[0]["map_id"] in state["stale"]:
            return None
        mid = links[0]["map_id"]
        head = next(m for m in supabase.tables[store_mod.MAPS_TABLE] if m["map_id"] == mid)
        items = sorted(
            (dict(r) for r in supabase.tables[store_mod.ITEMS_TABLE] if r["map_id"] == mid),
            key=lambda r: r["doc_index"],
        )
        return {"map": dict(head), "items": items, "angles": [{"seq": 1, "title": "t"}]}

    monkeypatch.setattr(store_mod, "load_fresh_angle_map_for_item", load)
    return state


def _db(**extra: list[dict]) -> FakeSupabase:
    """File F uploaded in c-old (item H, member D1 of map M1 with file G's item X1),
    attached from «الملفات» to c-new (item C). Fresh upload U (file U) in c-new."""
    tables: dict[str, list[dict]] = {
        "workspace_items": [
            _item("H", "F", "c-old", "2026-11-20T10:00:00+00:00"),
            _item("X1", "G", "c-old", "2026-11-20T10:00:01+00:00"),
            _item("C", "F", "c-new", "2026-11-27T09:00:00+00:00"),
            _item("U", "U", "c-new", "2026-11-27T09:00:05+00:00"),
        ],
        store_mod.MAPS_TABLE: [_map("M1", "2026-11-20T10:05:00+00:00")],
        store_mod.ITEMS_TABLE: [_member("M1", "H", 1), _member("M1", "X1", 2)],
    }
    for k, rows in extra.items():
        tables.setdefault(k, []).extend(rows)
    return FakeSupabase(tables)


# --------------------------------------------------------------------------- carry


def test_a_copy_carries_its_files_map_whole(fresh):
    got = lib.load_carried_angle_map(_db(), "C")
    assert got["map"]["map_id"] == "M1"
    # Whole, with the other file of that message — as stored (the other conversation's items).
    assert [i["item_id"] for i in got["items"]] == ["H", "X1"]


def test_the_newest_fresh_map_is_carried(fresh):
    db = _db(**{
        "workspace_items": [_item("H2", "F", "c-mid", "2026-11-25T10:00:00+00:00")],
        store_mod.MAPS_TABLE: [_map("M2", "2026-11-25T10:05:00+00:00")],
        store_mod.ITEMS_TABLE: [_member("M2", "H2", 1)],
    })
    assert lib.load_carried_angle_map(db, "C")["map"]["map_id"] == "M2"
    fresh["stale"].add("M2")
    assert lib.load_carried_angle_map(db, "C")["map"]["map_id"] == "M1"


def test_nothing_is_carried_without_another_live_item_with_a_fresh_map(fresh):
    assert lib.load_carried_angle_map(_db(), "U") is None            # a fresh upload: its file is only here
    fresh["stale"].add("M1")
    assert lib.load_carried_angle_map(_db(), "C") is None            # the only map is stale
    fresh["stale"].clear()
    db = _db()
    db.tables["workspace_items"][0]["deleted_at"] = "2026-11-26T00:00:00+00:00"
    assert lib.load_carried_angle_map(db, "C") is None               # its item deleted
    assert lib.load_carried_angle_map(_db(), "") is None


def test_a_map_of_another_user_is_never_carried(fresh):
    db = _db()
    db.tables[store_mod.MAPS_TABLE][0]["user_id"] = OTHER_USER
    assert lib.load_carried_angle_map(db, "C") is None


def test_no_file_id_means_nothing_is_carried(fresh, monkeypatch):
    monkeypatch.setattr(lib.files_store, "with_file_id", lambda cols, **_k: cols)
    db = _db()
    for r in db.tables["workspace_items"]:
        r.pop("file_id")
    assert lib.load_carried_angle_map(db, "C") is None


def test_a_failed_read_carries_nothing(fresh):
    db = _db()
    db.fail.add(store_mod.MAPS_TABLE)
    assert lib.load_carried_angle_map(db, "C") is None


# --------------------------------------------------------------------------- localize


def _carried() -> dict:
    return {"map": {"map_id": "M1", "user_id": USER}, "items": [
        {"map_id": "M1", "item_id": "H", "doc_index": 1},
        {"map_id": "M1", "item_id": "X1", "doc_index": 2},
    ], "angles": []}


def test_members_point_at_this_conversations_copies():
    stored = _carried()
    got = lib.localize_angle_map(_db(), stored, ["C", "U"])
    assert [i["item_id"] for i in got["items"]] == ["C", "X1"]   # X1's file is not here: stays the map's own
    assert [i["doc_index"] for i in got["items"]] == [1, 2]
    assert [i["item_id"] for i in stored["items"]] == ["H", "X1"]  # untouched


def test_both_files_here_both_point_here():
    db = _db(workspace_items=[_item("C2", "G", "c-new", "2026-11-27T09:00:01+00:00")])
    got = lib.localize_angle_map(db, _carried(), ["C", "C2"])
    assert [i["item_id"] for i in got["items"]] == ["C", "C2"]


def test_an_own_map_or_no_match_comes_back_as_is():
    own = {"map": {"map_id": "M9", "user_id": USER}, "items": [{"item_id": "C", "doc_index": 1}]}
    assert lib.localize_angle_map(_db(), own, ["C"]) is own
    stored = _carried()
    assert lib.localize_angle_map(_db(), stored, ["U"]) is stored
    db = _db()
    db.fail.add("workspace_items")
    assert lib.localize_angle_map(db, stored, ["C"]) is stored


# --------------------------------------------------------------------------- map


def test_a_copy_is_an_item_whose_file_has_an_older_item():
    db = _db()
    assert lib.library_copies(db, ["C", "U", "H"]) == {"C": "F"}
    # The file's first item deleted: the later one is still a copy.
    db.tables["workspace_items"][0]["deleted_at"] = "2026-11-26T00:00:00+00:00"
    assert lib.library_copies(db, ["C"]) == {"C": "F"}


def test_only_copies_with_no_map_anywhere_are_picked(fresh):
    db = _db(**{
        "workspace_items": [
            _item("H3", "K", "c-old", "2026-11-19T10:00:00+00:00"),   # file K: never mapped
            _item("C3", "K", "c-new", "2026-11-27T09:00:02+00:00"),
        ],
    })
    # C carries M1 → not picked; C3 has no map anywhere → picked; U is a new upload.
    assert lib.items_to_map(db, ["C", "C3", "U"]) == ["C3"]
    fresh["stale"].add("M1")
    assert lib.items_to_map(db, ["C", "C3", "U"]) == ["C", "C3"]


def test_a_copy_with_its_own_fresh_map_is_not_picked(fresh):
    db = _db(**{
        store_mod.MAPS_TABLE: [_map("M5", "2026-11-27T09:10:00+00:00")],
        store_mod.ITEMS_TABLE: [_member("M5", "C", 1), _member("M5", "U", 2)],
    })
    fresh["stale"].add("M1")
    assert lib.items_to_map(db, ["C", "U"]) == []


def test_a_failed_read_picks_nothing(fresh):
    db = _db()
    db.fail.add("workspace_items")
    assert lib.items_to_map(db, ["C"]) == []


def test_the_plans_are_the_owners_list():
    assert lib.LIBRARY_MAP_PLANS == frozenset({"pro", "pro_plus", "max", "dev"})


@pytest.mark.parametrize("plan, picked", [
    ("pro", True), ("pro_plus", True), ("max", True), ("dev", True),
    ("free", False), ("basic", False), ("marketing_lawyer_14", False), ("marketing_lawyer_3", False), (None, False),
])
def test_a_new_map_only_on_pro_pro_plus_max(monkeypatch, plan, picked):
    monkeypatch.setattr(lib, "items_to_map", lambda *_a: ["C3"])

    async def effective(*_a: Any) -> str | None:
        return plan

    monkeypatch.setattr(lib, "_effective_plan", effective)
    got = asyncio.run(lib.library_items_to_map(object(), ["C3"], user_id=USER))
    assert got == (["C3"] if picked else [])


def test_no_plan_read_when_nothing_needs_a_map(monkeypatch):
    monkeypatch.setattr(lib, "items_to_map", lambda *_a: [])

    async def boom(*_a: Any) -> str:
        raise AssertionError("plan read")

    monkeypatch.setattr(lib, "_effective_plan", boom)
    assert asyncio.run(lib.library_items_to_map(object(), ["C"], user_id=USER)) == []


def test_a_failed_plan_read_maps_nothing(monkeypatch):
    monkeypatch.setattr(lib, "items_to_map", lambda *_a: ["C3"])

    async def boom(*_a: Any) -> str:
        raise RuntimeError("rpc down")

    monkeypatch.setattr(lib, "_effective_plan", boom)
    assert asyncio.run(lib.library_items_to_map(object(), ["C3"], user_id=USER)) == []


# --------------------------------------------------------------------------- the switch


def test_switch_off_carries_nothing_and_maps_nothing(fresh, monkeypatch):
    monkeypatch.setattr(lib, "_enabled", lambda _user: False)
    assert lib.load_carried_angle_map(_db(), "C") is None

    def no_reads(*_a: Any) -> list[str]:
        raise AssertionError("read with the switch off")

    monkeypatch.setattr(lib, "items_to_map", no_reads)
    assert asyncio.run(lib.library_items_to_map(object(), ["C"], user_id=USER)) == []


def test_the_switch_is_per_user(fresh, monkeypatch):
    monkeypatch.setattr(lib, "_enabled", lambda user: user == OTHER_USER)
    assert lib.load_carried_angle_map(_db(), "C") is None


@pytest.mark.parametrize("enabled, users, user, on", [
    (False, None, USER, False),
    (True, None, USER, True),
    (True, "", USER, True),
    (True, f"x, {USER}", USER, True),
    (True, "x,y", USER, False),
])
def test_the_switch_and_its_allowlist(monkeypatch, enabled, users, user, on):
    from types import SimpleNamespace

    from agents.utils import angle_map_flags as flags

    monkeypatch.setattr(flags, "get_settings", lambda: SimpleNamespace(
        LIBRARY_FILE_MAPS_ENABLED=enabled, LIBRARY_FILE_MAPS_USERS=users))
    assert flags.library_file_maps_enabled(user) is on


def test_the_switch_is_off_by_default_and_named_like_its_env_var():
    from shared.config import Settings

    for name, default in (("LIBRARY_FILE_MAPS_ENABLED", False), ("LIBRARY_FILE_MAPS_USERS", None)):
        field = Settings.model_fields[name]
        assert field.default is default
        assert field.validation_alias in (None, name)
