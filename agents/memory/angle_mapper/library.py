"""«الملفات» files and their angle maps (owner 2026-11-27).

A file attached from «الملفات» to another conversation is a NEW item — a copy
(``user_file_attach``, migration 227). Its text, summary, sections (223) and pins
follow the file, but an angle map belongs to the MESSAGE it was made for
(migration 228: its members are conversation items) and starts only when a
segmenter run ends, which a copy never has. The mapper is unchanged (owner: "not
to change the logic"); this module only decides which map a copy uses, and when
a copy gets one:

1. **Carry** — owner: "upload all the map, even if it included other files". A
   copy uses the newest FRESH map that holds another item of its file, whole:
   every document of that map, and the client's side from the conversation it was
   made in (:func:`load_carried_angle_map`). :func:`localize_angle_map` points the
   map's members at this conversation's copies of the same files, so a document
   that is here travels once, as this conversation's item; the map's other
   documents travel as the map's own.
2. **Map** — owner: "if the user attached an item with no map (and it qualify to
   a map) then map it, if it's pro, pro plus, max". A copy whose file has no map
   anywhere is started by the router's wait (``orchestrator._wait_angle_maps``)
   through the helper frame, exactly like a new upload's; the runner maps the
   message's files as it always does (a mixed message → one map of all of them)
   and charges it like any map. Only when the user's EFFECTIVE plan is in
   :data:`LIBRARY_MAP_PLANS` (:func:`library_items_to_map`); whether the file
   qualifies stays the helper's own ``applies_to``.

A copy = an item whose file has an OLDER item (live or deleted): the file's first
item is the upload itself. Behind ``LIBRARY_FILE_MAPS_ENABLED`` /
``LIBRARY_FILE_MAPS_USERS`` (off = a copy has no map, as before), and needs the
files read switch (``file_id``); with either off nothing is carried or started. The sync functions read the database
(call them in a thread); none raises.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Mapping, Sequence

from shared.files import store as files_store

from . import store as _store

logger = logging.getLogger(__name__)

ITEMS = "workspace_items"

#: Owner 2026-11-27: Pro, Pro Plus, Max. ``dev`` = the internal staff / test plan
#: (the main test account), as in ``extended.budget.MARGINS``.
LIBRARY_MAP_PLANS: frozenset[str] = frozenset({"pro", "pro_plus", "max", "dev"})

#: Ids per ``in.(…)`` filter.
_BATCH = 100


def _enabled(user_id: Any) -> bool:
    from agents.utils.angle_map_flags import library_file_maps_enabled

    return library_file_maps_enabled(user_id)


def _data(resp: Any) -> list[dict]:
    data = getattr(resp, "data", None)
    return data if isinstance(data, list) else []


def _ids(values: Sequence[Any] | None) -> list[str]:
    return list(dict.fromkeys(str(v) for v in values or [] if v))


def _ts(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _item_rows(supabase: Any, item_ids: Sequence[str], *, user_id: str | None = None) -> dict[str, dict]:
    """``{item_id: {item_id, user_id, created_at, deleted_at, file_id?}}``. ``file_id``
    is read only with the files read switch on (absent otherwise). Raises on a
    failed read."""
    ids = _ids(item_ids)
    out: dict[str, dict] = {}
    if not ids:
        return out
    cols = files_store.with_file_id("item_id, user_id, created_at, deleted_at", user_id=user_id, supabase=supabase)
    for i in range(0, len(ids), _BATCH):
        q = supabase.table(ITEMS).select(cols).in_("item_id", ids[i:i + _BATCH])
        if user_id:
            q = q.eq("user_id", str(user_id))
        for r in _data(q.execute()):
            out[str(r["item_id"])] = r
    return out


def _file_items(supabase: Any, file_ids: Sequence[str], *, user_id: str) -> list[dict]:
    """Every item of these files of the user, live or deleted. Raises on a failed read."""
    ids = _ids(file_ids)
    out: list[dict] = []
    for i in range(0, len(ids), _BATCH):
        out.extend(_data(
            supabase.table(ITEMS)
            .select("item_id, file_id, created_at, deleted_at")
            .in_("file_id", ids[i:i + _BATCH])
            .eq("user_id", str(user_id))
            .execute()
        ))
    return out


def _file_of(row: Mapping[str, Any] | None) -> str:
    return str((row or {}).get("file_id") or "")


# --------------------------------------------------------------------------- carry


def load_carried_angle_map(supabase: Any, item_id: str) -> dict | None:
    """The newest FRESH map that holds another LIVE item of ``item_id``'s file —
    the map a «الملفات» copy carries — in ``store.load_angle_map_for_item``'s shape,
    as stored (members are the other conversation's items: see
    :func:`localize_angle_map`). ``None`` when the file has no fresh map elsewhere,
    the item has no file, or on a failed read. Never raises.

    Fresh = the mapper's own check (``store.load_fresh_angle_map_for_item``) on a
    member of that map, so a map whose message group changed is never carried."""
    item_id = str(item_id or "")
    if not item_id:
        return None
    try:
        row = _item_rows(supabase, [item_id]).get(item_id)
        file_id, user_id = _file_of(row), str((row or {}).get("user_id") or "")
        if not file_id or not user_id or not _enabled(user_id):
            return None
        others = [
            str(r["item_id"]) for r in _file_items(supabase, [file_id], user_id=user_id)
            if str(r.get("item_id") or "") != item_id and not r.get("deleted_at")
        ]
        if not others:
            return None
        member_of: dict[str, str] = {}
        for link in _data(
            supabase.table(_store.ITEMS_TABLE).select("map_id, item_id").in_("item_id", others).execute()
        ):
            member_of.setdefault(str(link["map_id"]), str(link["item_id"]))
        if not member_of:
            return None
        heads = _data(
            supabase.table(_store.MAPS_TABLE)
            .select("map_id, created_at")
            .in_("map_id", list(member_of))
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
        for head in heads:
            fresh = _store.load_fresh_angle_map_for_item(supabase, member_of[str(head["map_id"])])
            if fresh:
                return fresh
        return None
    except Exception as exc:  # noqa: BLE001 — no map is a valid state for every reader
        logger.warning("angle_mapper: load_carried_angle_map failed for %s (%s)", item_id, type(exc).__name__)
        return None


def localize_angle_map(supabase: Any, stored: dict | None, item_ids: Sequence[str]) -> dict | None:
    """``stored`` with every member that is the same FILE as one of ``item_ids``
    (this conversation's files) replaced by that item — a new dict; ``stored``
    itself is untouched. Members of files that are not here stay as stored (the
    map travels whole). ``stored`` as is when nothing matches, when it is already
    this conversation's map, or on a failed read. Never raises."""
    try:
        members = [dict(m) for m in (stored or {}).get("items") or []]
        ids = _ids(item_ids)
        if not members or not ids:
            return stored
        member_ids = [str(m.get("item_id") or "") for m in members]
        outside = [m for m in member_ids if m and m not in set(ids)]
        if not outside:
            return stored
        user_id = str(((stored or {}).get("map") or {}).get("user_id") or "") or None
        rows = _item_rows(supabase, outside + ids, user_id=user_id)
        here: dict[str, str] = {}
        for i in ids:
            f = _file_of(rows.get(i))
            if f:
                here.setdefault(f, i)
        taken = set(member_ids)
        changed = False
        for m in members:
            mid = str(m.get("item_id") or "")
            if mid in ids:
                continue
            local = here.get(_file_of(rows.get(mid)))
            if local and local not in taken:
                m["item_id"] = local
                taken.add(local)
                changed = True
        return {**stored, "items": members} if changed else stored
    except Exception as exc:  # noqa: BLE001
        logger.warning("angle_mapper: localize_angle_map failed (%s)", type(exc).__name__)
        return stored


# --------------------------------------------------------------------------- map


def library_copies(supabase: Any, item_ids: Sequence[str]) -> dict[str, str]:
    """``{item_id: file_id}`` for the items that are «الملفات» copies — their file
    has an older item (live or deleted). Raises on a failed read."""
    rows = _item_rows(supabase, item_ids)
    by_user: dict[str, set[str]] = {}
    for r in rows.values():
        f, u = _file_of(r), str(r.get("user_id") or "")
        if f and u:
            by_user.setdefault(u, set()).add(f)
    # file_id -> sort key of its first item: (no timestamp?, epoch seconds, item_id)
    first: dict[str, tuple[bool, float, str]] = {}
    for user_id, files in by_user.items():
        for r in _file_items(supabase, sorted(files), user_id=user_id):
            ts = _ts(r.get("created_at"))
            key = (ts is None, ts.timestamp() if ts else 0.0, str(r.get("item_id") or ""))
            f = _file_of(r)
            if f and (f not in first or key < first[f]):
                first[f] = key
    out: dict[str, str] = {}
    for item_id, r in rows.items():
        f = _file_of(r)
        if f and f in first and first[f][2] != item_id:
            out[item_id] = f
    return out


def items_to_map(supabase: Any, item_ids: Sequence[str]) -> list[str]:
    """The «الملفات» copies among ``item_ids`` with no fresh map of their own and
    none to carry — the ones a new map is for (plan not checked here). Never
    raises: a failed read = none (nothing is started, nothing charged)."""
    try:
        ids = _ids(item_ids)
        copies = library_copies(supabase, ids)
        out: list[str] = []
        for item_id in ids:
            if item_id not in copies:
                continue
            if _store.load_fresh_angle_map_for_item(supabase, item_id):
                continue
            if load_carried_angle_map(supabase, item_id):
                continue
            out.append(item_id)
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("angle_mapper: items_to_map failed (%s)", type(exc).__name__)
        return []


async def _effective_plan(supabase: Any, user_id: str) -> str | None:
    from shared.quota import library_state

    return (await library_state(supabase, user_id)).effective_plan_id


async def library_items_to_map(supabase: Any, item_ids: Sequence[str], *, user_id: str) -> list[str]:
    """:func:`items_to_map`, kept only when the user's EFFECTIVE plan (an expired
    paid plan that fell back to free is free) is in :data:`LIBRARY_MAP_PLANS`.
    Never raises; a failed plan read = none."""
    try:
        if not _enabled(user_id):
            return []
        ids = await asyncio.to_thread(items_to_map, supabase, item_ids)
        if not ids:
            return []
        plan = await _effective_plan(supabase, user_id)
        if str(plan or "") not in LIBRARY_MAP_PLANS:
            logger.info("angle_mapper: %d library file(s) unmapped — plan %s", len(ids), plan or "-")
            return []
        return ids
    except Exception as exc:  # noqa: BLE001
        logger.warning("angle_mapper: library_items_to_map failed (%s)", type(exc).__name__)
        return []


__all__ = [
    "LIBRARY_MAP_PLANS",
    "items_to_map",
    "library_copies",
    "library_items_to_map",
    "load_carried_angle_map",
    "localize_angle_map",
]
