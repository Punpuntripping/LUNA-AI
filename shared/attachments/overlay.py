"""The text-correction overlay for readers that hold ROWS (attachment_agent.md §3.3).

A whole-text reader (the router's no-summary fallback, ``unfold_workspace_item``,
``_load_attached_items``, the writer walkers, the workspace and «الملفات» routes, the
template ingester, the summary input, the segmenter's model message) already holds
rows with ``content_md``. :func:`correct_rows` hands them back with each attachment's
active text corrections applied (:mod:`.text_corrections`), in ONE corrections read
for every row of the call.

Section readers that hold item ids use :func:`corrections_for_item_ids` — with the
file keys ``load_segment_maps`` already resolved (``StoredMap.file_key``), so no
second lookup — and ``apply_to_sections`` / ``apply_text_corrections``.

Gated by the attachment agent's flag (:func:`.flags.edits_enabled`): only the agent
writes corrections, so for a user it is off for — or a row whose owner is unknown —
nothing is read (no corrections read, no file lookup): zero extra requests. The
backoff is checked before any lookup too.

The stored text is never changed: lengths, offsets and freshness stay on the REAL
``content_md`` — callers that compute ``content_chars`` or ``_source_length`` do so
on the text they read, before correcting it.

Which file a row's corrections hang on (the table is keyed by ``user_files.file_id``):

- the row carries ``file_id`` (the read switch's ``with_file_id``, or ``*``): that
  file; ``file_id`` NULL = the item links no file → nothing to correct;
- it carries none and the files read switch is on: one ``workspace_items`` lookup
  (``files_store.file_ids_for_items``) for every such row; an unlinked item, or a
  failed lookup, gets nothing;
- the switch is off: the item's own id (a file's first item has ``file_id =
  item_id``), with no ``file_id`` select — the switch-off contract of
  ``shared/files/tests/test_user_files_readers.py``.

No correction rows → the very same list back (byte-identical). Any failure → the
rows unchanged (the loader logs and backs off for 60 s). Never raises.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from typing import Any

from . import text_corrections as tc
from .flags import edits_enabled
from .text_corrections import TextCorrection, apply_text_corrections, load_text_corrections

logger = logging.getLogger(__name__)

#: The text every whole-text reader shows.
TEXT_KEYS: tuple[str, ...] = ("content_md",)
#: Only uploaded files have corrections (reports, memos and notes never do).
ATTACHMENT_KINDS: frozenset[str] = frozenset({"attachment"})

_UNSET: Any = object()


def _wanted(
    row: Any,
    *,
    id_key: str,
    kinds: Collection[str] | None,
    where: Callable[[Mapping[str, Any]], bool] | None,
) -> bool:
    if not isinstance(row, Mapping) or not row.get(id_key):
        return False
    if kinds is not None and str(row.get("kind") or "") not in kinds:
        return False
    return where is None or bool(where(row))


def _file_keys(
    supabase: Any, rows: Sequence[Mapping[str, Any]], *, id_key: str, user_id: Any
) -> dict[str, str]:
    """``{row id: file id}`` by the rule in the module docstring (rows without a
    file are left out)."""
    keys: dict[str, str] = {}
    missing: list[str] = []
    for row in rows:
        rid = str(row[id_key])
        if "file_id" in row:
            if row.get("file_id"):
                keys[rid] = str(row["file_id"])
        else:
            missing.append(rid)
    if not missing:
        return keys
    from shared.files import store as files_store

    if files_store.files_read_enabled(user_id, supabase=supabase):
        try:
            linked = files_store.file_ids_for_items(supabase, missing, user_id=user_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("attachments: file lookup failed (%s) — no text corrections", type(exc).__name__)
            linked = {}
        keys.update({rid: str(linked[rid]) for rid in missing if linked.get(rid)})
    else:
        keys.update({rid: rid for rid in missing})
    return keys


def corrections_for_rows(
    supabase: Any,
    rows: Iterable[Any] | None,
    *,
    user_id: Any = None,
    id_key: str = "item_id",
    kinds: Collection[str] | None = ATTACHMENT_KINDS,
    where: Callable[[Mapping[str, Any]], bool] | None = None,
) -> dict[str, list[TextCorrection]]:
    """``{row[id_key]: [active corrections, oldest first]}`` for the rows that have
    some — at most one file lookup (rows without ``file_id``, switch on) and ONE
    corrections read. ``kinds`` — only rows of these kinds (``None``: any row);
    ``where`` — a further filter. ``user_id`` (``users.user_id``) — whose fixes, and
    whose flag; absent, each row's own ``user_id`` decides. A row whose owner the
    flag is off for (or unknown) is skipped, so with none left no request is made.
    Never raises: ``{}`` on any failure."""
    try:
        on: dict[str, bool] = {}

        def flag_on(row: Mapping[str, Any]) -> bool:
            owner = str(user_id or row.get("user_id") or "")
            if owner not in on:
                on[owner] = edits_enabled(owner)
            return on[owner]

        picked = [
            r for r in rows or []
            if _wanted(r, id_key=id_key, kinds=kinds, where=where) and flag_on(r)
        ]
        if not picked or supabase is None or tc._backoff.down():
            return {}
        keys = _file_keys(supabase, picked, id_key=id_key, user_id=user_id)
        if not keys:
            return {}
        by_file = load_text_corrections(supabase, keys.values(), user_id=user_id)
        return {rid: by_file[fid] for rid, fid in keys.items() if fid in by_file}
    except Exception as exc:  # noqa: BLE001
        logger.warning("attachments: text corrections not loaded (%s)", type(exc).__name__)
        return {}


def corrections_for_item_ids(
    supabase: Any,
    item_ids: Iterable[Any] | None,
    *,
    user_id: Any,
    file_keys: Mapping[str, Any] | None = None,
) -> dict[str, list[TextCorrection]]:
    """``{item_id: its file's active corrections}`` for section readers that hold
    item ids. ``file_keys`` — ``{item_id: file id}`` already resolved (e.g.
    ``StoredMap.file_key`` from ``load_segment_maps``): those items need no lookup;
    the rest are looked up once (``text_corrections.file_keys_for_items``, the segment
    store's key rule). The flag off for ``user_id`` (or no user), the backoff down,
    or no item → ``{}`` with no request at all. Never raises."""
    try:
        ids = list(dict.fromkeys(str(i) for i in item_ids or [] if i))
        if not ids or supabase is None or not edits_enabled(user_id) or tc._backoff.down():
            return {}
        known = {str(k): str(v) for k, v in (file_keys or {}).items() if k and v}
        keys = {i: known[i] for i in ids if i in known}
        missing = [i for i in ids if i not in keys]
        if missing:
            keys.update(tc.file_keys_for_items(supabase, missing, user_id=user_id))
        by_file = load_text_corrections(supabase, keys.values(), user_id=user_id)
        return {item: by_file[fid] for item, fid in keys.items() if fid in by_file}
    except Exception as exc:  # noqa: BLE001
        logger.warning("attachments: text corrections not loaded (%s)", type(exc).__name__)
        return {}


def correct_rows(
    supabase: Any,
    rows: list[Any] | None,
    *,
    user_id: Any = None,
    text_keys: Sequence[str] = TEXT_KEYS,
    id_key: str = "item_id",
    kinds: Collection[str] | None = ATTACHMENT_KINDS,
    where: Callable[[Mapping[str, Any]], bool] | None = None,
) -> list[Any]:
    """``rows`` with each picked row's ``text_keys`` corrected (every ``wrong`` →
    ``right``, oldest fix first). A changed row is a copy; every other row is the
    same object, in the same place. Rows with no text in ``text_keys`` cost no read.
    Nothing to correct, or any failure → the SAME list object back.

    ``kinds`` / ``where`` / ``id_key`` / ``user_id`` as in :func:`corrections_for_rows`."""
    if not rows:
        return rows if rows is not None else []
    try:
        def has_text(row: Mapping[str, Any]) -> bool:
            return any(isinstance(row.get(k), str) and row.get(k) for k in text_keys) and (
                where is None or bool(where(row))
            )

        fixes = corrections_for_rows(
            supabase, rows, user_id=user_id, id_key=id_key, kinds=kinds, where=has_text,
        )
        if not fixes:
            return rows
        out: list[Any] = []
        changed = False
        for row in rows:
            mine = fixes.get(str(row.get(id_key))) if isinstance(row, Mapping) and row.get(id_key) else None
            if not mine:
                out.append(row)
                continue
            update = {
                k: apply_text_corrections(row[k], mine)
                for k in text_keys if isinstance(row.get(k), str) and row.get(k)
            }
            update = {k: v for k, v in update.items() if v != row[k]}
            if update:
                out.append({**row, **update})
                changed = True
            else:
                out.append(row)
        return out if changed else rows
    except Exception as exc:  # noqa: BLE001
        logger.warning("attachments: text corrections not applied (%s)", type(exc).__name__)
        return rows


def correct_text(
    supabase: Any,
    text: str,
    *,
    item_id: Any,
    user_id: Any = None,
    file_id: Any = _UNSET,
) -> str:
    """One item's text with its file's corrections — :func:`correct_rows` on one
    row, for readers that hold a text and an item id but no row kind (the caller
    has already decided the item may be an attachment). ``file_id`` when known
    (``None`` = links no file); else found by the module's rule. ``text`` itself
    back when there is nothing to correct or anything fails."""
    if not text or not item_id:
        return text
    row: dict[str, Any] = {"item_id": str(item_id), "content_md": text}
    if file_id is not _UNSET:
        row["file_id"] = file_id
    [out] = correct_rows(supabase, [row], user_id=user_id, kinds=None)
    return out["content_md"] if out is not row else text


__all__ = [
    "ATTACHMENT_KINDS",
    "TEXT_KEYS",
    "correct_rows",
    "correct_text",
    "corrections_for_item_ids",
    "corrections_for_rows",
]
