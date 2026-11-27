"""Storage of segment maps (migration 215) — write and read.

The client is ``service_role`` (bypasses RLS), so every read here is scoped by
the ``item_id`` list the caller already owns, and by ``user_id`` when given.

Write = replace: delete the map row (its segments cascade), insert the map,
insert the segments. A reader that lands between the steps sees either no map or
a map with no segments yet — :func:`load_segment_maps` treats both as "no map".
A failed segment insert removes the map row again, so a half-written map never
looks complete.

Pages, maps and segments hang on the FILE (migration 223: their ``item_id``
column holds ``user_files.file_id``). Callers pass conversation item ids; every
function here first turns each into its file's id (:func:`_file_keys`). For the
file's first item the two are equal (``file_id = item_id``); a library file
attached to another conversation (migration 227) has a new item id and the same
file id, so it finds the same pages and map. Results are keyed by the ids the
caller passed.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Mapping

from shared.attachments.flags import edits_enabled
from shared.attachments.segment_overrides import apply_segment_overrides, load_segment_overrides
from shared.files import store as files_store

from .models import StoredMap, StoredSegment

logger = logging.getLogger(__name__)

PAGES_TABLE = "attachment_pages"
MAPS_TABLE = "attachment_segment_maps"
SEGMENTS_TABLE = "attachment_segments"

#: PostgREST returns at most 1,000 rows per request.
_PAGE_ROWS = 1_000
_INSERT_BATCH = 500


class StoreError(RuntimeError):
    """The map could not be written."""


def _data(resp: Any) -> list[dict]:
    return list(getattr(resp, "data", None) or [])


def _file_keys(
    supabase: Any, item_ids: Iterable[str], *, user_id: str | None = None, strict: bool = False
) -> dict[str, str]:
    """``{item_id: id its pages / map are stored under}`` — the item's file, or
    the item itself when it links none. ``strict``: a failed read raises;
    otherwise each item keeps its own id (right for every file's first item)."""
    ids = list(dict.fromkeys(str(i) for i in item_ids if i))
    if not ids:
        return {}
    try:
        files = files_store.file_ids_for_items(supabase, ids, user_id=user_id)
    except Exception as exc:  # noqa: BLE001
        if strict:
            raise
        logger.warning("attachment_segmenter: file lookup failed (%s) — item ids used", type(exc).__name__)
        files = {}
    return {i: files.get(i, i) for i in ids}


def _file_key(supabase: Any, item_id: str, *, strict: bool = False) -> str:
    return _file_keys(supabase, [item_id], strict=strict).get(str(item_id), str(item_id))


# ---------------------------------------------------------------------------
# Reads used by the runner
# ---------------------------------------------------------------------------


def load_pages(supabase: Any, item_id: str) -> list[dict]:
    """The item's file's ``attachment_pages`` rows in page order (``[]`` on any failure)."""
    try:
        key = _file_key(supabase, item_id)
        return _data(
            supabase.table(PAGES_TABLE)
            .select("page_no, char_start, content_md")
            .eq("item_id", key)
            .order("page_no")
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 — no page numbers is a valid state
        logger.warning("attachment_segmenter: page read failed for %s (%s)", item_id, type(exc).__name__)
        return []


def map_state(supabase: Any, item_id: str) -> tuple[int, bool] | None:
    """``(content_chars, has_segments)`` of the item's file's stored map, or ``None``.

    Raises on a failed read: the runner must not mistake "could not read" for
    "no map" — that only costs a re-run, but it is still a paid call.
    """
    key = _file_key(supabase, item_id, strict=True)
    rows = _data(
        supabase.table(MAPS_TABLE)
        .select("content_chars")
        .eq("item_id", key)
        .limit(1)
        .execute()
    )
    if not rows:
        return None
    seg = _data(
        supabase.table(SEGMENTS_TABLE)
        .select("seq")
        .eq("item_id", key)
        .limit(1)
        .execute()
    )
    return int(rows[0].get("content_chars") or -1), bool(seg)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def write_segment_map(supabase: Any, map_row: Mapping[str, Any], segment_rows: list[Mapping[str, Any]]) -> None:
    """Replace the map and segments of the item's file (rows are re-keyed from the
    item id to the file id). Raises :class:`StoreError`."""
    try:
        item_id = _file_key(supabase, str(map_row["item_id"]), strict=True)
    except Exception as exc:  # noqa: BLE001
        raise StoreError(f"file lookup: {type(exc).__name__}") from exc
    map_row = {**map_row, "item_id": item_id}
    segment_rows = [{**r, "item_id": item_id} if "item_id" in r else dict(r) for r in segment_rows]
    try:
        supabase.table(MAPS_TABLE).delete().eq("item_id", item_id).execute()
        supabase.table(MAPS_TABLE).insert(dict(map_row)).execute()
    except Exception as exc:  # noqa: BLE001
        raise StoreError(f"map row: {type(exc).__name__}") from exc
    try:
        for i in range(0, len(segment_rows), _INSERT_BATCH):
            supabase.table(SEGMENTS_TABLE).insert([dict(r) for r in segment_rows[i:i + _INSERT_BATCH]]).execute()
    except Exception as exc:  # noqa: BLE001
        try:
            supabase.table(MAPS_TABLE).delete().eq("item_id", item_id).execute()
        except Exception:  # noqa: BLE001
            logger.warning("attachment_segmenter: could not remove half-written map %s", item_id)
        raise StoreError(f"segment rows: {type(exc).__name__}") from exc


# ---------------------------------------------------------------------------
# Read — what delivery code calls
# ---------------------------------------------------------------------------


def _content_lengths(supabase: Any, ids: list[str], user_id: str | None) -> dict[str, int]:
    lengths: dict[str, int] = {}
    if files_store.files_read_enabled(user_id, supabase=supabase):
        # Files in their own table (my_files_phase2.md §C4): the file's
        # content_chars, without reading the text. The live-file filter stands in
        # for the item's deleted_at (equal while the 222 mirror runs); the item's
        # own deleted_at is kept too. An item without a file is read as before.
        lengths = files_store.content_chars_for_items(supabase, ids, user_id=user_id)
        ids = [i for i in ids if i not in lengths]
        if not ids:
            return lengths
    q = (
        supabase.table("workspace_items")
        .select("item_id, content_md")
        .in_("item_id", ids)
        .is_("deleted_at", "null")
    )
    if user_id:
        q = q.eq("user_id", user_id)
    return {**{str(r["item_id"]): len(r.get("content_md") or "") for r in _data(q.execute())}, **lengths}


def _segment_rows(supabase: Any, ids: list[str]) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        batch = _data(
            supabase.table(SEGMENTS_TABLE)
            .select("*")
            .in_("item_id", ids)
            .order("item_id")
            .order("seq")
            .range(offset, offset + _PAGE_ROWS - 1)
            .execute()
        )
        rows.extend(batch)
        if len(batch) < _PAGE_ROWS:
            return rows
        offset += _PAGE_ROWS


def _with_promotions(
    supabase: Any, maps: dict[str, StoredMap], keys: Mapping[str, str], user_id: str | None
) -> dict[str, StoredMap]:
    """``maps`` with the sections the user promoted turned ``core`` (migration 234,
    ``shared/attachments/segment_overrides.py``; attachment_agent.md §3.1): ONE read
    for every file of the call; a section that is not core and overlaps an active
    promotion range of its file loads as ``core`` with ``promoted_by_user=True``.
    Promote only. No promotion, or any failure → ``maps`` unchanged (never breaks
    delivery).

    Gated by the attachment agent's flag (``shared.attachments.flags``; only the
    agent writes promotions): a map is looked at only when the flag is on for its
    owner — ``user_id``, else the map row's own ``user_id``. None on → ``maps``
    with no request at all (rollback = flag off: promotions stop applying)."""
    if not maps:
        return maps
    try:
        on = {
            item_id for item_id, smap in maps.items()
            if edits_enabled(user_id or smap.user_id)
        }
        if not on:
            return maps
        overrides = load_segment_overrides(
            supabase, list(dict.fromkeys(keys[i] for i in maps if i in on)), user_id=user_id
        )
        if not overrides:
            return maps
        out: dict[str, StoredMap] = {}
        for item_id, smap in maps.items():
            mine = overrides.get(keys[item_id]) if item_id in on else None
            out[item_id] = (
                smap.model_copy(update={"segments": apply_segment_overrides(smap.segments, mine)})
                if mine else smap
            )
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("attachment_segmenter: promotions not applied (%s)", type(exc).__name__)
        return maps


def load_segment_maps(
    supabase: Any,
    item_ids: Iterable[str],
    *,
    user_id: str | None = None,
    content_chars: Mapping[str, int] | None = None,
) -> dict[str, StoredMap]:
    """The usable stored map of each item, keyed by ``item_id``.

    An item is left out when it has no map, its map is STALE
    (``content_chars != len(workspace_items.content_md)``), its map has no
    segments, or the item is missing / soft-deleted. Segments come in document
    order (``seq``) with their original text in ``content_md``.

    Sections the user promoted (attachment agent, migration 234) come back
    ``core`` with ``promoted_by_user=True`` — :func:`_with_promotions`, one extra
    read per call; its failure leaves the stored values.

    ``content_chars`` — the caller's ``{item_id: len(content_md)}`` when it
    already holds the rows; the ``workspace_items`` read (and with it the
    soft-delete filter) is then skipped. ``user_id`` — also filter by owner
    (defence in depth; the client is ``service_role``).

    Never raises: a failed read returns ``{}`` (the consumer falls back to the
    full text, plan D16).
    """
    ids = list(dict.fromkeys(str(i) for i in item_ids if i))
    if not ids:
        return {}
    try:
        lengths = (
            {str(k): int(v) for k, v in content_chars.items()}
            if content_chars is not None
            else _content_lengths(supabase, ids, user_id)
        )
        keys = _file_keys(supabase, ids, user_id=user_id)
        q = supabase.table(MAPS_TABLE).select("*").in_("item_id", list(dict.fromkeys(keys.values())))
        if user_id:
            q = q.eq("user_id", user_id)
        maps = {str(r["item_id"]): r for r in _data(q.execute())}
        # item id -> its file's map row, when that map was made from this text
        fresh = {
            item: maps[key]
            for item, key in keys.items()
            if key in maps and item in lengths
            and int(maps[key].get("content_chars") or -1) == lengths[item]
        }
        if not fresh:
            return {}
        by_key: dict[str, list[StoredSegment]] = {}
        for r in _segment_rows(supabase, list(dict.fromkeys(keys[i] for i in fresh))):
            by_key.setdefault(str(r["item_id"]), []).append(
                StoredSegment(**{k: r.get(k) for k in StoredSegment.model_fields if k in r})
            )
        out: dict[str, StoredMap] = {}
        for item_id, r in fresh.items():
            segs = sorted(by_key.get(keys[item_id], []), key=lambda s: s.seq)
            if not segs:
                continue
            out[item_id] = StoredMap(
                item_id=item_id,
                user_id=str(r.get("user_id") or "") or None,
                paragraph_rule_version=str(r.get("paragraph_rule_version") or ""),
                prompt_version=str(r.get("prompt_version") or ""),
                model=r.get("model"),
                split=bool(r.get("split")),
                documents_in_file=list(r.get("documents_in_file") or []),
                key_paragraphs=list(r.get("key_paragraphs") or []),
                content_chars=int(r["content_chars"]),
                created_at=str(r.get("created_at") or "") or None,
                segments=segs,
                file_key=keys[item_id],
            )
        return _with_promotions(supabase, out, keys, user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("attachment_segmenter: load_segment_maps failed (%s)", type(exc).__name__)
        return {}


def load_segment_map(supabase: Any, item_id: str) -> StoredMap | None:
    """One item's usable map, or ``None`` — the helper frame's ``LoadFn`` shape
    (``agents/helpers/spec.py``). Same rules as :func:`load_segment_maps`."""
    return load_segment_maps(supabase, [item_id]).get(str(item_id))


__all__ = [
    "MAPS_TABLE",
    "PAGES_TABLE",
    "SEGMENTS_TABLE",
    "StoreError",
    "load_pages",
    "load_segment_map",
    "load_segment_maps",
    "map_state",
    "write_segment_map",
]
