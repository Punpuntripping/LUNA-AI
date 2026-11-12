"""Storage of segment maps (migration 215) — write and read.

The client is ``service_role`` (bypasses RLS), so every read here is scoped by
the ``item_id`` list the caller already owns, and by ``user_id`` when given.

Write = replace: delete the map row (its segments cascade), insert the map,
insert the segments. A reader that lands between the steps sees either no map or
a map with no segments yet — :func:`load_segment_maps` treats both as "no map".
A failed segment insert removes the map row again, so a half-written map never
looks complete.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Mapping

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


# ---------------------------------------------------------------------------
# Reads used by the runner
# ---------------------------------------------------------------------------


def load_pages(supabase: Any, item_id: str) -> list[dict]:
    """The item's ``attachment_pages`` rows in page order (``[]`` on any failure)."""
    try:
        return _data(
            supabase.table(PAGES_TABLE)
            .select("page_no, char_start, content_md")
            .eq("item_id", item_id)
            .order("page_no")
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 — no page numbers is a valid state
        logger.warning("attachment_segmenter: page read failed for %s (%s)", item_id, type(exc).__name__)
        return []


def map_state(supabase: Any, item_id: str) -> tuple[int, bool] | None:
    """``(content_chars, has_segments)`` of the item's stored map, or ``None``.

    Raises on a failed read: the runner must not mistake "could not read" for
    "no map" — that only costs a re-run, but it is still a paid call.
    """
    rows = _data(
        supabase.table(MAPS_TABLE)
        .select("content_chars")
        .eq("item_id", item_id)
        .limit(1)
        .execute()
    )
    if not rows:
        return None
    seg = _data(
        supabase.table(SEGMENTS_TABLE)
        .select("seq")
        .eq("item_id", item_id)
        .limit(1)
        .execute()
    )
    return int(rows[0].get("content_chars") or -1), bool(seg)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def write_segment_map(supabase: Any, map_row: Mapping[str, Any], segment_rows: list[Mapping[str, Any]]) -> None:
    """Replace the item's map and segments. Raises :class:`StoreError`."""
    item_id = map_row["item_id"]
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
    q = (
        supabase.table("workspace_items")
        .select("item_id, content_md")
        .in_("item_id", ids)
        .is_("deleted_at", "null")
    )
    if user_id:
        q = q.eq("user_id", user_id)
    return {str(r["item_id"]): len(r.get("content_md") or "") for r in _data(q.execute())}


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
        q = supabase.table(MAPS_TABLE).select("*").in_("item_id", ids)
        if user_id:
            q = q.eq("user_id", user_id)
        fresh = {
            str(r["item_id"]): r
            for r in _data(q.execute())
            if str(r["item_id"]) in lengths and int(r.get("content_chars") or -1) == lengths[str(r["item_id"])]
        }
        if not fresh:
            return {}
        by_item: dict[str, list[StoredSegment]] = {}
        for r in _segment_rows(supabase, list(fresh)):
            by_item.setdefault(str(r["item_id"]), []).append(
                StoredSegment(**{k: r.get(k) for k in StoredSegment.model_fields if k in r})
            )
        out: dict[str, StoredMap] = {}
        for item_id, r in fresh.items():
            segs = sorted(by_item.get(item_id, []), key=lambda s: s.seq)
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
            )
        return out
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
