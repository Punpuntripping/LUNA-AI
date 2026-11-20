"""The only code that reads or writes ``user_files`` (Phase 2 — files in their own table).

Plan: ``.claude/plans/my_files_phase2.md`` §C1 (this module), §C4 (the reader
pattern), §C5 (the writer calls). Migrations 221 + 222 create the table, the
``workspace_items.file_id`` link and the triggers that keep a file and its
conversation items (pointers) equal in both directions.

Readers (switch ``USER_FILES_READ_ENABLED``) keep their query and add two lines::

    cols = files_store.with_file_id(columns, user_id=user_id, supabase=supabase)
    rows = <the existing query, with cols>
    rows = files_store.overlay_files(supabase, rows, user_id=user_id)

Writers (switch ``USER_FILES_WRITE_ENABLED``) keep computing what they compute
today and call :func:`write_attachment` with both the full dict (the switch-off
path issues today's exact statement) and their own delta (the switch-on path
patches the file through the ``user_file_patch`` RPC, which MERGES metadata).

Never logs text, summaries, titles or filenames — ids, counts and exception
type names only (the ``files_service`` rule).
"""
from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional

from shared.files.flags import files_read_enabled, files_write_enabled

logger = logging.getLogger(__name__)

FILES_TABLE = "user_files"
ITEMS_TABLE = "workspace_items"

#: The columns a file owns. A pointer row carries a synced copy of each of them
#: (``content_md`` leaves the pointer at contract, Phase 4).
FILE_COLUMNS: tuple[str, ...] = (
    "title", "content_md", "summary", "summary_source_length", "summary_updated_at",
    "metadata", "storage_path", "document_id", "word_count",
)
#: select alias on ``workspace_items`` -> select expression on ``user_files``.
#: ``doc_type:metadata->>doc_type`` on a pointer is the generated column here.
FILE_ALIASES: dict[str, str] = {"doc_type": "doc_type"}

#: Ids per ``in.(…)`` filter — keeps a request URL well inside PostgREST limits.
ID_BATCH = 100

#: The 222 copy reads ``COALESCE(wi.content_md, '')`` / ``COALESCE(wi.metadata,
#: '{}')``. A pointer NULL whose file holds that default is the same value: the
#: overlay keeps the pointer's ``None`` so the reader sees exactly what it saw.
_COALESCED: dict[str, Any] = {"content_md": "", "metadata": {}}
_FILE_COLUMN_SET = frozenset(FILE_COLUMNS)


class FileOverlayError(RuntimeError):
    """``strict=True``: a linked file could not be read."""


@dataclass
class FileWrite:
    """What :func:`write_attachment` returns on the file path — shaped like a
    PostgREST response (``.data`` is a list of rows) so a caller reads it the
    same way whichever path ran."""

    data: list[dict] = field(default_factory=list)
    file_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _data(resp: Any) -> list[dict]:
    data = getattr(resp, "data", None)
    if isinstance(data, dict):
        return [data]
    return [r for r in (data or []) if isinstance(r, dict)]


def _unique(ids: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(str(i) for i in ids if i))


def _batches(ids: list[str]) -> Iterable[list[str]]:
    for i in range(0, len(ids), ID_BATCH):
        yield ids[i:i + ID_BATCH]


def _top_level_columns(columns: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in columns:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def _ids_text(ids: list[str], limit: int = 10) -> str:
    head = ", ".join(ids[:limit])
    return head + (f" (+{len(ids) - limit})" if len(ids) > limit else "")


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def with_file_id(columns: str, *, user_id: Any = None, supabase: Any = None) -> str:
    """``columns`` + ``", file_id"`` when the read switch is on, else ``columns``
    unchanged. ``*`` already carries ``file_id`` once migration 222 is in."""
    if not files_read_enabled(user_id, supabase=supabase):
        return columns
    cols = _top_level_columns(columns)
    if "*" in cols or "file_id" in cols:
        return columns
    return f"{columns}, file_id"


def load_files(
    supabase: Any, file_ids: Iterable[Any], *, columns: str, user_id: Any = None
) -> dict[str, dict]:
    """``{file_id: row}`` for the given files, ``columns`` selected (plus
    ``file_id``), one request per ``ID_BATCH`` ids, filtered by owner when
    ``user_id`` is given. Raises on a failed read."""
    ids = _unique(file_ids)
    if not ids:
        return {}
    cols = columns if "file_id" in _top_level_columns(columns) else f"file_id, {columns}"
    out: dict[str, dict] = {}
    for batch in _batches(ids):
        q = supabase.table(FILES_TABLE).select(cols).in_("file_id", batch)
        if user_id:
            q = q.eq("user_id", str(user_id))
        for row in _data(q.execute()):
            if row.get("file_id"):
                out[str(row["file_id"])] = row
    return out


def overlay_files(
    supabase: Any,
    rows: list[dict],
    *,
    user_id: Any = None,
    strict: bool = False,
) -> list[dict]:
    """Replace a pointer row's copy of the file's fields with the file's values.

    For every row with a truthy ``file_id``: each key the row already carries
    that is in ``FILE_COLUMNS`` / ``FILE_ALIASES`` takes the file's value; no key
    is added and none removed, and key order is kept. One ``user_files`` select
    per ``ID_BATCH`` ids, filtered by ``user_id`` when given. Rows without
    ``file_id`` — and every row while the read switch is off — come back
    untouched (switch off: the very same list).

    ``strict=False`` (Phase 2): a failed read or a missing file keeps the
    pointer's values and logs ids only — this function then never raises, so a
    reader can call it outside its own ``try``. ``strict=True`` (from
    contract): raises :class:`FileOverlayError`.
    """
    if not rows or not files_read_enabled(user_id, supabase=supabase):
        return rows
    try:
        return _overlay(supabase, rows, user_id=user_id, strict=strict)
    except FileOverlayError:
        raise
    except Exception as exc:  # noqa: BLE001
        if strict:
            raise FileOverlayError(f"overlay failed: {type(exc).__name__}") from exc
        logger.warning("files: overlay failed (%s) — pointer values kept", type(exc).__name__)
        return rows


def _overlay(supabase: Any, rows: list[dict], *, user_id: Any, strict: bool) -> list[dict]:
    linked = [r for r in rows if isinstance(r, dict) and r.get("file_id")]
    if not linked:
        return rows

    wanted: dict[str, str] = {}   # row key -> user_files select expression
    for row in linked:
        for key in row:
            if key in _FILE_COLUMN_SET:
                wanted[key] = key
            elif key in FILE_ALIASES:
                wanted[key] = FILE_ALIASES[key]
    if not wanted:
        return rows
    select = ", ".join(key if key == expr else f"{key}:{expr}" for key, expr in wanted.items())

    file_ids = _unique(r["file_id"] for r in linked)
    try:
        files = load_files(supabase, file_ids, columns=select, user_id=user_id)
    except Exception as exc:  # noqa: BLE001
        if strict:
            raise FileOverlayError(f"user_files read failed: {type(exc).__name__}") from exc
        logger.warning(
            "files: overlay read failed for %d file(s) (%s) — pointer values kept",
            len(file_ids), type(exc).__name__,
        )
        return rows

    out: list[dict] = []
    missing: list[str] = []
    for row in rows:
        if not (isinstance(row, dict) and row.get("file_id")):
            out.append(row)
            continue
        fid = str(row["file_id"])
        frow = files.get(fid)
        if frow is None:
            missing.append(fid)
            out.append(row)
            continue
        merged = dict(row)
        for key in row:
            if key not in wanted:
                continue
            value = frow.get(key)
            if row[key] is None and key in _COALESCED and value == _COALESCED[key]:
                continue
            merged[key] = copy.deepcopy(value) if isinstance(value, (dict, list)) else value
        out.append(merged)

    if missing:
        if strict:
            raise FileOverlayError(f"{len(missing)} linked file(s) missing")
        logger.warning(
            "files: %d linked file(s) not found (%s) — pointer values kept",
            len(missing), _ids_text(missing),
        )
    return out


def file_ids_for_items(
    supabase: Any, item_ids: Iterable[Any], *, user_id: Any = None
) -> dict[str, str]:
    """``{item_id: file_id}`` for the pointers that link a file (deleted
    pointers included — a writer may finish on one). Raises on a failed read."""
    ids = _unique(item_ids)
    out: dict[str, str] = {}
    for batch in _batches(ids):
        q = supabase.table(ITEMS_TABLE).select("item_id, file_id").in_("item_id", batch)
        if user_id:
            q = q.eq("user_id", str(user_id))
        for row in _data(q.execute()):
            if row.get("item_id") and row.get("file_id"):
                out[str(row["item_id"])] = str(row["file_id"])
    return out


def content_chars_for_items(
    supabase: Any, item_ids: Iterable[Any], *, user_id: Any = None
) -> dict[str, int]:
    """``{item_id: user_files.content_chars}`` for live pointers of live files
    (the length of the text, without reading it). An item without a file is
    left out. Raises on a failed read."""
    ids = _unique(item_ids)
    pointer_file: dict[str, str] = {}
    for batch in _batches(ids):
        q = (
            supabase.table(ITEMS_TABLE)
            .select("item_id, file_id")
            .in_("item_id", batch)
            .is_("deleted_at", "null")
        )
        if user_id:
            q = q.eq("user_id", str(user_id))
        for row in _data(q.execute()):
            if row.get("item_id") and row.get("file_id"):
                pointer_file[str(row["item_id"])] = str(row["file_id"])
    if not pointer_file:
        return {}
    chars: dict[str, int] = {}
    for batch in _batches(_unique(pointer_file.values())):
        q = (
            supabase.table(FILES_TABLE)
            .select("file_id, content_chars")
            .in_("file_id", batch)
            .is_("deleted_at", "null")
        )
        if user_id:
            q = q.eq("user_id", str(user_id))
        for row in _data(q.execute()):
            if row.get("file_id") and row.get("content_chars") is not None:
                chars[str(row["file_id"])] = int(row["content_chars"])
    return {item: chars[fid] for item, fid in pointer_file.items() if fid in chars}


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def patch_file(
    supabase: Any,
    file_id: str,
    *,
    fields: Optional[Mapping[str, Any]] = None,
    metadata_patch: Optional[Mapping[str, Any]] = None,
    user_id: Any = None,
) -> bool:
    """``user_file_patch``: set ``fields``, MERGE ``metadata_patch`` into the
    file's metadata, in one statement. True when a row was written. Raises on a
    failed call."""
    params = {
        "p_file_id": str(file_id),
        "p_fields": dict(fields or {}),
        "p_metadata_patch": dict(metadata_patch or {}),
        "p_user_id": str(user_id) if user_id else None,
    }
    resp = supabase.rpc("user_file_patch", params).execute()
    data = getattr(resp, "data", None)
    if isinstance(data, list):
        return any(bool(x) for x in data)
    return bool(data)


def _legacy_update(
    supabase: Any, item_id: str, payload: Mapping[str, Any], filters: Optional[Mapping[str, Any]]
) -> Any:
    q = supabase.table(ITEMS_TABLE).update(dict(payload)).eq("item_id", item_id)
    for key, value in (filters or {}).items():
        q = q.eq(key, value)
    return q.execute()


def write_attachment(
    supabase: Any,
    item_id: str,
    *,
    fields: Optional[Mapping[str, Any]] = None,
    metadata: Optional[Mapping[str, Any]] = None,
    metadata_patch: Optional[Mapping[str, Any]] = None,
    owner_id: Any = None,
    filters: Optional[Mapping[str, Any]] = None,
    legacy_update: Optional[Mapping[str, Any]] = None,
    pointer_update: Optional[Mapping[str, Any]] = None,
    returning: bool = False,
) -> Any:
    """One attachment write, on whichever path the write switch picks.

    Switch off: ``workspace_items.update(payload).eq("item_id", item_id)``
    plus ``.eq(k, v)`` per ``filters`` — today's statement, unchanged.
    ``payload`` is ``legacy_update`` when the caller passes its exact dict
    (key order, ``updated_at`` …), else ``{**fields, "metadata": metadata}``.
    Returns the PostgREST response.

    Switch on: ``user_file_patch(file, fields, metadata_patch, owner_id)``; the
    222 trigger copies the file's fields onto its pointers in the same
    transaction. ``pointer_update`` (cancel: ``deleted_at``) is then written on
    the pointer itself, because the file→pointer sync never copies it. A
    pointer with no file, or a patch that wrote no row, falls back to today's
    statement (the pointer→file trigger carries it over). Returns a
    :class:`FileWrite`; with ``returning`` its ``data`` is the pointer row
    re-read (``select("*")``), like the ``.data`` of today's update.

    Failures raise on both paths, so every caller keeps its own error handling.
    """
    payload: dict[str, Any]
    if legacy_update is not None:
        payload = dict(legacy_update)
    else:
        payload = dict(fields or {})
        if metadata is not None:
            payload["metadata"] = metadata

    if not files_write_enabled(owner_id, supabase=supabase):
        return _legacy_update(supabase, item_id, payload, filters)

    file_id = file_ids_for_items(supabase, [item_id], user_id=owner_id).get(str(item_id))
    if not file_id:
        logger.warning("files: item %s has no file — written on the item", item_id)
        return _legacy_update(supabase, item_id, payload, filters)
    if not patch_file(
        supabase, file_id, fields=fields, metadata_patch=metadata_patch, user_id=owner_id,
    ):
        logger.warning(
            "files: patch wrote no row for file %s (item %s) — written on the item",
            file_id, item_id,
        )
        return _legacy_update(supabase, item_id, payload, filters)
    if pointer_update:
        _legacy_update(supabase, item_id, pointer_update, filters)
    rows: list[dict] = []
    if returning:
        q = supabase.table(ITEMS_TABLE).select("*").eq("item_id", item_id)
        for key, value in (filters or {}).items():
            q = q.eq(key, value)
        rows = _data(q.execute())
    return FileWrite(data=rows, file_id=file_id)


def hide_files(supabase: Any, user_id: Any, file_ids: Iterable[Any]) -> int:
    """«حذف من الملفات» in one statement (``user_files_hide``): stamps
    ``library_hidden_at`` (and its metadata copy) on the user's live, not yet
    hidden files; the first stamp is kept. Returns the rows stamped. Raises on
    a failed call."""
    ids = _unique(file_ids)
    if not ids:
        return 0
    resp = supabase.rpc("user_files_hide", {"p_user_id": str(user_id), "p_file_ids": ids}).execute()
    data = getattr(resp, "data", None)
    if isinstance(data, list):
        data = data[0] if data else 0
    try:
        return int(data or 0)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "FILES_TABLE",
    "FILE_ALIASES",
    "FILE_COLUMNS",
    "FileOverlayError",
    "FileWrite",
    "ID_BATCH",
    "content_chars_for_items",
    "file_ids_for_items",
    "hide_files",
    "load_files",
    "overlay_files",
    "patch_file",
    "with_file_id",
    "write_attachment",
]
