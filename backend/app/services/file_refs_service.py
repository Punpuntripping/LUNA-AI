"""File refs of a deep-search report — «أقسام من مستنداتك».

Plan: ``.claude/plans/aggregator_file_refs.md`` §4.5 / §4.6 / §4.8 (D10, D11). Table
``workspace_item_file_refs`` (migration 235, modelled on ``workspace_item_references``):
one row per ``[F1·S7]`` / ``[F1]`` tag of a report that resolved to a part of the
user's own file — which report (``wi_id``) links to which file (``item_id``, its
``wi_seq``) and section (``section_seq``).

* :func:`persist_item_file_refs` — the publisher, right after the library refs.
  Rows are written from the STORED real values (the attachment's title, the
  section row's what / owner / brought_by / pages), never from the aggregator's
  encoded copies (وضع السرية).
* :func:`list_file_refs` — ``GET /workspace/{item_id}/file-refs``.
* :func:`file_ref_section` — ``GET /workspace/{item_id}/file-refs/{ref_id}/section``.

WHY THE SECTION CAN ALWAYS BE SHOWN (D7). A row keeps WHERE the section sits in the
file's text (``char_start`` / ``char_end``), not a copy. Nothing removes that text
while the report exists: «حذف من ملفاتي» only stamps ``library_hidden_at``, the
retention sweep purges the original PDF and keeps the text, a re-segment renumbers
sections but never touches the text, and deleting the conversation deletes the
report too. So the route serves the section row's own text when the map still has
that ``seq`` at the same positions (a seg-v5.2 page-break join drops the letterhead
between its halves), else the slice of the file's text at the stored positions —
read through the ``shared.files`` overlay, so a file attached from «الملفات» reads
its file's text — and applies the user's active text fixes (migration 234: an
overlay, positions stay on the original text). Only an attachment the user deleted
on purpose (``workspace_items.deleted_at``) answers «لم يعد هذا الملف متاحًا».

The client is service_role: every read here is scoped by an id the caller already
owns (the route checks the report first) and by ``user_id``.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any, Optional

from shared.files import store as files_store
from shared.workspace.file_refs import (
    FILE_REF_COLUMNS,
    FILE_REFS_TABLE,
    load_file_ref_rows,
    parse_file_ref,
)

logger = logging.getLogger(__name__)

ITEMS_TABLE = "workspace_items"
FILES_TABLE = "user_files"

#: 410 body (the contract, §4.6).
FILE_GONE_AR = "لم يعد هذا الملف متاحًا"

#: What a route returns for one row (the contract's ``FileRefRow``).
_ROW_KEYS = (
    "n", "ref_id", "wi_seq", "section_seq", "page_from", "page_to",
    "doc_title", "what", "owner", "brought_by",
)


class FileRefNotFound(LookupError):
    """No such ref on this report (→ 404)."""


class FileRefGone(LookupError):
    """The attachment was deleted on purpose (→ 410)."""


def _data(resp: Any) -> list[dict]:
    return list(getattr(resp, "data", None) or [])


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _load_items(
    supabase: Any, item_ids: Iterable[str], user_id: str, columns: str
) -> dict[str, dict]:
    """``{item_id: row}`` of the user's items, deleted ones included, with a
    «الملفات» file's own values (title, text) overlaid — the segmenter's rule
    (``attachment_segmenter.runner._load_items``)."""
    ids = list(dict.fromkeys(str(i) for i in item_ids if i))
    if not ids:
        return {}
    cols = files_store.with_file_id(columns, user_id=user_id, supabase=supabase)
    rows: list[dict] = []
    for i in range(0, len(ids), files_store.ID_BATCH):
        rows += _data(
            supabase.table(ITEMS_TABLE)
            .select(cols)
            .in_("item_id", ids[i:i + files_store.ID_BATCH])
            .eq("user_id", str(user_id))
            .execute()
        )
    rows = files_store.overlay_files(supabase, rows, user_id=user_id)
    return {str(r["item_id"]): r for r in rows if r.get("item_id")}


def _segments_by_seq(supabase: Any, item_id: str, user_id: str, content_chars: Optional[int] = None) -> dict[int, Any]:
    """The item's usable stored sections by ``seq`` (``{}`` when there is no fresh
    map) — the delivery's own loader, so promotions and the file key match."""
    from agents.memory.attachment_segmenter.store import load_segment_maps

    kwargs: dict[str, Any] = {"user_id": user_id}
    if content_chars is not None:
        kwargs["content_chars"] = {str(item_id): int(content_chars)}
    smap = load_segment_maps(supabase, [str(item_id)], **kwargs).get(str(item_id))
    return {int(s.seq): s for s in (getattr(smap, "segments", None) or [])}


def _same_place(seg: Any, char_start: Optional[int], char_end: Optional[int]) -> bool:
    """The section row sits where the ref was taken (no positions stored = trust the seq)."""
    if char_start is None or char_end is None:
        return True
    return _int_or_none(_get(seg, "char_start")) == char_start and _int_or_none(_get(seg, "char_end")) == char_end


# ---------------------------------------------------------------------------
# Write — the publisher
# ---------------------------------------------------------------------------


def persist_item_file_refs(
    supabase: Any,
    *,
    wi_id: str,
    user_id: str,
    file_refs: Iterable[Any] | None,
) -> int:
    """Insert one ``workspace_item_file_refs`` row per resolved file ref of the
    report ``wi_id``. Returns the number written. Raises on a failed write — the
    publisher wraps it (a file-ref hiccup never fails the publish)."""
    refs = [r for r in (file_refs or []) if parse_file_ref(_get(r, "ref_id")) is not None]
    if not refs:
        return 0
    source_ids = list(dict.fromkeys(str(_get(r, "item_id")) for r in refs))
    items = _load_items(supabase, source_ids, user_id, "item_id, title, wi_seq")
    seq_ids = list(dict.fromkeys(
        str(_get(r, "item_id")) for r in refs if _get(r, "section_seq") is not None
    ))
    sections: dict[str, dict[int, Any]] = {}
    if seq_ids:
        from agents.memory.attachment_segmenter.store import load_segment_maps

        maps = load_segment_maps(supabase, seq_ids, user_id=user_id)
        for iid, smap in (maps or {}).items():
            sections[str(iid)] = {int(s.seq): s for s in (getattr(smap, "segments", None) or [])}

    rows: list[dict] = []
    seen: set[str] = set()
    for r in refs:
        ref = parse_file_ref(_get(r, "ref_id"))
        if ref is None or ref in seen:
            continue
        seen.add(ref)
        source = str(_get(r, "item_id"))
        wi_seq = _int_or_none(_get(r, "wi_seq"))
        if wi_seq is None:
            wi_seq = _int_or_none((items.get(source) or {}).get("wi_seq"))
        if wi_seq is None:
            continue  # the table needs the file's WI number (D11)
        seq = _int_or_none(_get(r, "section_seq"))
        start = _int_or_none(_get(r, "char_start"))
        end = _int_or_none(_get(r, "char_end"))
        seg = sections.get(source, {}).get(seq) if seq is not None else None
        if seg is not None and not _same_place(seg, start, end):
            seg = None  # re-segmented between the turn and now: keep only the positions
        rows.append({
            "wi_id": str(wi_id),
            "n": len(rows) + 1,
            "ref_id": ref,
            "item_id": source,
            "wi_seq": wi_seq,
            "section_seq": seq,
            "char_start": start,
            "char_end": end,
            "page_from": _int_or_none(_get(seg, "page_from")) if seg is not None else None,
            "page_to": _int_or_none(_get(seg, "page_to")) if seg is not None else None,
            "doc_title": " ".join(str((items.get(source) or {}).get("title") or "").split()),
            "what": str(_get(seg, "what") or "") if seg is not None else "",
            "owner": str(_get(seg, "owner") or "") if seg is not None else "",
            "brought_by": str(_get(seg, "brought_by") or "") if seg is not None else "",
        })
    if not rows:
        return 0
    supabase.table(FILE_REFS_TABLE).insert(rows).execute()
    return len(rows)


# ---------------------------------------------------------------------------
# Read — the routes
# ---------------------------------------------------------------------------


def _public_row(row: Mapping[str, Any], available: bool) -> dict:
    out = {k: row.get(k) for k in _ROW_KEYS}
    out["ref_id"] = parse_file_ref(row.get("ref_id")) or str(row.get("ref_id") or "")
    for k in ("doc_title", "what", "owner", "brought_by"):
        out[k] = str(out.get(k) or "")
    out["available"] = bool(available)
    return out


def _live_items(supabase: Any, item_ids: Iterable[str], user_id: str) -> set[str]:
    """The ids among ``item_ids`` that are the user's and not deleted."""
    ids = list(dict.fromkeys(str(i) for i in item_ids if i))
    live: set[str] = set()
    for i in range(0, len(ids), files_store.ID_BATCH):
        for r in _data(
            supabase.table(ITEMS_TABLE)
            .select("item_id")
            .in_("item_id", ids[i:i + files_store.ID_BATCH])
            .eq("user_id", str(user_id))
            .is_("deleted_at", "null")
            .execute()
        ):
            live.add(str(r.get("item_id")))
    return live


def list_file_refs(supabase: Any, wi_id: str, owner_user_id: str) -> list[dict]:
    """The report's rows for the chips and «من مستنداتك», in ``n`` order.
    ``owner_user_id`` — the REPORT's owner (the route has checked the caller)."""
    rows = load_file_ref_rows(supabase, wi_id)
    if not rows:
        return []
    live = _live_items(supabase, [r.get("item_id") for r in rows], owner_user_id)
    return [_public_row(r, str(r.get("item_id")) in live) for r in rows]


def _listed_in_files(supabase: Any, caller_user_id: str, item_id: str) -> bool:
    """«الملفات» still lists this file for the caller (so «افتح الملف» can open it).
    Never raises; False on any doubt."""
    try:
        from backend.app.services.files_service import HIDDEN_KEY, my_files_enabled

        if not my_files_enabled(caller_user_id):
            return False
        file_ids = files_store.file_ids_for_items(supabase, [item_id], user_id=caller_user_id)
        file_id = file_ids.get(str(item_id))
        if file_id:
            rows = _data(
                supabase.table(FILES_TABLE)
                .select("library_hidden_at, deleted_at")
                .eq("file_id", file_id)
                .eq("user_id", str(caller_user_id))
                .limit(1)
                .execute()
            )
            return bool(rows) and rows[0].get("library_hidden_at") is None and rows[0].get("deleted_at") is None
        rows = _data(
            supabase.table(ITEMS_TABLE)
            .select("metadata")
            .eq("item_id", str(item_id))
            .eq("user_id", str(caller_user_id))
            .is_("deleted_at", "null")
            .limit(1)
            .execute()
        )
        meta = (rows[0].get("metadata") if rows else None) or {}
        return bool(rows) and not (isinstance(meta, Mapping) and meta.get(HIDDEN_KEY))
    except Exception as exc:  # noqa: BLE001
        logger.warning("file_refs: «الملفات» check failed (%s)", type(exc).__name__)
        return False


def _section_text(supabase: Any, row: Mapping[str, Any], item: Mapping[str, Any], user_id: str) -> str:
    """The cited part's REAL text (§4.5): the section row when it still sits where
    it was cited, else the slice at the stored positions; a whole document = its
    text. The user's text fixes applied last."""
    from shared.attachments.overlay import correct_text

    source = str(row.get("item_id"))
    content = str(item.get("content_md") or "")
    seq = _int_or_none(row.get("section_seq"))
    start, end = _int_or_none(row.get("char_start")), _int_or_none(row.get("char_end"))
    if seq is None:
        text = content
    else:
        text = ""
        seg = _segments_by_seq(supabase, source, user_id, content_chars=len(content)).get(seq)
        if seg is not None and _same_place(seg, start, end):
            text = str(_get(seg, "content_md") or "")
        if not text.strip() and start is not None and end is not None and 0 <= start < end <= len(content):
            text = content[start:end]
    if not text:
        return ""
    return correct_text(supabase, text, item_id=source, user_id=user_id)


def file_ref_section(
    supabase: Any,
    wi_id: str,
    ref: str,
    *,
    owner_user_id: str,
    caller_user_id: str,
) -> dict:
    """One cited part for the popup: the row's fields + ``content_md`` +
    ``files_item_id``. Raises :class:`FileRefNotFound` / :class:`FileRefGone`."""
    canon = parse_file_ref(ref)
    if canon is None:
        raise FileRefNotFound(ref)
    rows = _data(
        supabase.table(FILE_REFS_TABLE)
        .select(FILE_REF_COLUMNS)
        .eq("wi_id", str(wi_id))
        .eq("ref_id", canon)
        .limit(1)
        .execute()
    )
    if not rows:
        raise FileRefNotFound(canon)
    row = rows[0]
    source = str(row.get("item_id") or "")
    items = _load_items(
        supabase, [source], owner_user_id, "item_id, user_id, kind, title, content_md, deleted_at",
    )
    item = items.get(source)
    if item is None:
        raise FileRefNotFound(canon)
    if item.get("deleted_at") is not None:
        raise FileRefGone(canon)
    out = _public_row(row, True)
    out["content_md"] = _section_text(supabase, row, item, owner_user_id)
    out["files_item_id"] = source if (
        caller_user_id and str(caller_user_id) == str(owner_user_id)
        and _listed_in_files(supabase, caller_user_id, source)
    ) else None
    return out


__all__ = [
    "FILE_GONE_AR",
    "FileRefGone",
    "FileRefNotFound",
    "file_ref_section",
    "list_file_refs",
    "persist_item_file_refs",
]
