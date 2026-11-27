"""«ملفاتي» — the user's files, read from today's ``workspace_items`` table.

Plan: ``.claude/plans/my_files.md`` — §11 is the API contract, §3.8 the
same-file rule, §5 the endpoint rules. Phase 1 + 1b: read-only screens, the
«حذف من ملفاتي» stamp, and the same-file proposal inside one conversation.
No migration: until Phase 2 moves files to their own table, a file IS a
``workspace_items`` row with ``kind='attachment'``.

Sync functions taking the service-role client and a ``users.user_id`` (the
route resolves it), like ``workspace_service``. The client bypasses RLS, so
every read here is filtered by ``user_id``.

Phase 2 (``.claude/plans/my_files_phase2.md`` §D, last bullet): with the files
read switch on (``shared/files/flags.py``) the same functions read
``user_files`` instead — one row per file, the conversation items (pointers)
embedded for the WI number and the home conversation. The response is the
same §11 contract plus ``file_id``. «حذف من الملفات» becomes one atomic
statement (``user_files_hide``) with the write switch on. Switches off: the
functions below run exactly as in Phase 1.

Never logs document text, summaries or filenames — ids and counts only.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable, Optional, get_args

from supabase import Client as SupabaseClient

from backend.app.errors import ErrorCode, LunaHTTPException
from backend.app.models.responses import FileDocType
from shared.attachments.flags import edits_enabled
from shared.attachments.segment_overrides import apply_segment_overrides, load_segment_overrides
from shared.attachments.text_corrections import (
    TextCorrection,
    apply_text_corrections,
    apply_to_sections,
    load_text_corrections,
)
from shared.config import get_settings
from shared.files import flags as files_flags
from shared.files import store as files_store

logger = logging.getLogger(__name__)

ITEMS_TABLE = "workspace_items"
FILES_TABLE = files_store.FILES_TABLE
MAPS_TABLE = "attachment_segment_maps"
SEGMENTS_TABLE = "attachment_segments"

#: ``workspace_items.conversation_id`` → ``conversations`` (named, because the
#: table carries more than one relation PostgREST could embed).
CONVERSATION_FK = "artifacts_conversation_id_fkey"
_CONVERSATION_EMBED = f"conversations!{CONVERSATION_FK}(deleted_at)"
#: ``workspace_items(file_id, user_id)`` → ``user_files(file_id, user_id)``
#: (migration 222): from a file, its conversation items (pointers).
FILE_OWNER_FK = "workspace_items_file_owner_fkey"
_POINTER_EMBED = (
    f"{ITEMS_TABLE}!{FILE_OWNER_FK}(item_id, conversation_id, wi_seq, deleted_at, created_at, "
    f"{_CONVERSATION_EMBED})"
)

#: The list is one response, newest first (§5); the largest library is ~170.
LIBRARY_CAP = 500
#: ``summary_preview`` length, «…» included.
PREVIEW_MAX_CHARS = 280

#: ``metadata`` key stamped by «حذف من ملفاتي». The row itself is never
#: deleted: hard-deleting an attachment cascades away ``message_attachments``.
HIDDEN_KEY = "library_hidden_at"
#: ``type_counts`` key for a file the summarizer has not typed yet.
UNTYPED = "untyped"
#: Section values the opened file never shows (letterheads, repeats — §3.3).
HIDDEN_SECTION_VALUES = frozenset({"boilerplate", "repeat"})

DOC_TYPES: tuple[str, ...] = get_args(FileDocType)

# ── The same-file proposal (§3.8) ───────────────────────────────────────────
#: PHASE 3 SWITCH. A copy in ANOTHER conversation can only be offered once a
#: file is attachable outside its home conversation (Phase 2's own table +
#: Phase 3's from-file route). Until then only a copy inside the asking
#: conversation is proposed. This flag is the one place that rule lives.
CROSS_CONVERSATION_MATCH = False

#: An extraction that is NOT complete is never proposed — a fresh upload may do
#: better. How the OCR runner (agents/memory/ocr_extractor/runner.py) marks it:
#:   ocr_partial            — the page budget read only some pages (also
#:                            ocr_page_total / ocr_page_ranges, and a 〔[نظام] …〕
#:                            header at the top of content_md)
#:   ocr_lost_ranges        — pages neither Mistral nor the text layer could read
#:   ocr_text_layer_ranges  — pages read from the PDF's own text layer after
#:                            Mistral failed (numbers / tables may be scrambled)
_INCOMPLETE_EXTRACTION_KEYS = ("ocr_partial", "ocr_lost_ranges", "ocr_text_layer_ranges")
#: Before the page budget (commit d8072dc, 2026-11-16) OCR read the first 30
#: pages of every file and recorded no marker: ``ocr_pages == 30`` is the only
#: trace of a file that may have been cut there.
_LEGACY_OCR_PAGE_CAP = 30

#: PostgREST returns at most 1,000 rows per request.
_PAGE_ROWS = 1_000
#: Upper bound on rows scanned for one user (defence against a runaway loop).
_MAX_SCAN_ROWS = 10_000

_LIST_COLUMNS = (
    "item_id, conversation_id, wi_seq, title, summary, metadata, storage_path, "
    f"document_id, created_at, {_CONVERSATION_EMBED}"
)
_KEY_COLUMNS = f"item_id, conversation_id, wi_seq, title, metadata, created_at, {_CONVERSATION_EMBED}"
_DETAIL_COLUMNS = (
    "item_id, conversation_id, wi_seq, title, summary, content_md, metadata, "
    "storage_path, document_id, created_at"
)

# The file path (Phase 2). ``content_chars`` / ``library_hidden_at`` are
# ``user_files`` columns; the file name is never selected as a column (it is
# read from ``metadata`` like above, and compared in Python).
_FILE_LIST_COLUMNS = (
    "file_id, title, summary, metadata, storage_path, document_id, created_at, "
    f"content_chars, library_hidden_at, {_POINTER_EMBED}"
)
_FILE_KEY_COLUMNS = (
    f"file_id, title, metadata, created_at, content_chars, library_hidden_at, {_POINTER_EMBED}"
)
_FILE_DETAIL_COLUMNS = (
    "file_id, title, summary, content_md, metadata, storage_path, document_id, created_at, "
    f"library_hidden_at, {_POINTER_EMBED}"
)


# ============================================
# Rollout flag
# ============================================


def my_files_enabled(user_id: Any) -> bool:
    """``MY_FILES_ENABLED`` and (no ``MY_FILES_USERS`` allow-list, or this
    ``users.user_id`` is on it) — the segmenter flags' rule. Off on error."""
    try:
        settings = get_settings()
        if not getattr(settings, "MY_FILES_ENABLED", False):
            return False
        users = getattr(settings, "MY_FILES_USERS", None)
        allowed = {u.strip() for u in str(users or "").split(",") if u.strip()}
        return not allowed or str(user_id or "") in allowed
    except Exception:  # noqa: BLE001
        return False


# ============================================
# summary_preview (§11 — the one implementation)
# ============================================

_HEADING_LINE = re.compile(r"^\s*#{1,6}(?:\s|$)")
# A line that is wholly bold: **…** or __…__, optionally followed by a colon.
_BOLD_LINE = re.compile(r"^\s*(?:\*\*([^*\n]+)\*\*|__([^_\n]+)__)\s*([:：])?\s*$")
# A bold label opening the first line: «**ملخص المستند:** نص…» / «**ملخص**: نص…».
_INLINE_LABEL = re.compile(
    r"^\s*(?:\*\*[^*\n]{1,60}?[:：]\s*\*\*|__[^_\n]{1,60}?[:：]\s*__"
    r"|\*\*[^*\n]{1,60}?\*\*\s*[:：]|__[^_\n]{1,60}?__\s*[:：])\s*"
)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_LINE_MARKS = re.compile(r"^\s*(?:>\s*)*(?:#{1,6}\s+)?(?:(?:[-*+•▪◦]|\d{1,3}[.)]|[٠-٩]{1,3}[.)])\s+)?")
_TRAILING_CUT = " \t،,.;؛:-–—"
#: Short wholly-bold lines are titles even without a colon («**ملخص المستند**»).
_LABEL_MAX_CHARS = 40


def _is_label_line(line: str) -> bool:
    if _HEADING_LINE.match(line):
        return True
    m = _BOLD_LINE.match(line)
    if not m:
        return False
    inner = (m.group(1) or m.group(2) or "").strip()
    return bool(m.group(3)) or inner.endswith((":", "：")) or len(inner) <= _LABEL_MAX_CHARS


def _strip_marks(line: str) -> str:
    line = _LINE_MARKS.sub("", line, count=1)
    line = _IMAGE.sub("", line)
    line = _LINK.sub(r"\1", line)
    for mark in ("**", "__", "~~", "`", "*"):
        line = line.replace(mark, "")
    return line


def _cut(text: str, limit: int = PREVIEW_MAX_CHARS) -> str:
    """At most ``limit`` chars; cut on a word boundary and end with «…»."""
    if len(text) <= limit:
        return text
    budget = limit - 1  # room for «…»
    head = text[:budget]
    if not text[budget].isspace():
        space = head.rfind(" ")
        if space > 0:
            head = head[:space]
    head = head.rstrip(_TRAILING_CUT) or text[:budget]
    return head + "…"


def summary_preview(summary: Optional[str]) -> str:
    """The file card's two lines (§11): the summary's first real paragraph as
    plain text, at most 280 chars. A leading label line («**ملخص المستند:**», a
    heading) is dropped, and so is a bold label opening the paragraph; the
    paragraph ends at a blank line or at the next label line. ``""`` when there
    is no summary."""
    if not summary or not isinstance(summary, str):
        return ""
    paragraph: list[str] = []
    for raw in summary.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        blank = not line
        label = not blank and _is_label_line(line)
        if not paragraph:
            if blank or label:
                continue  # leading blanks and label lines
            line = _INLINE_LABEL.sub("", line, count=1)
            text = _strip_marks(line).strip()
            if text:
                paragraph.append(text)
            continue
        if blank or label:
            break
        text = _strip_marks(line).strip()
        if text:
            paragraph.append(text)
    flat = re.sub(r"\s+", " ", " ".join(paragraph)).strip()
    return _cut(flat)


# ============================================
# Row helpers
# ============================================


def _data(resp: Any) -> list[dict]:
    data = getattr(resp, "data", None)
    if isinstance(data, dict):
        return [data]
    return list(data or [])


def _fetch_all(build: Callable[[], Any]) -> list[dict]:
    """Every row of ``build()`` (a query with a stable order), 1,000 at a time."""
    rows: list[dict] = []
    offset = 0
    while True:
        batch = _data(build().range(offset, offset + _PAGE_ROWS - 1).execute())
        rows.extend(batch)
        if len(batch) < _PAGE_ROWS or len(rows) >= _MAX_SCAN_ROWS:
            return rows
        offset += _PAGE_ROWS


def _meta(row: dict) -> dict:
    meta = row.get("metadata")
    return meta if isinstance(meta, dict) else {}


def _int_or_none(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _str_or_none(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _identity(row: dict) -> Optional[tuple[str, int, Optional[str]]]:
    """(filename, size, mime) — the identical-file key (O6 / D12). ``None``
    when the name or the size is missing: such a row is never folded."""
    meta = _meta(row)
    filename = _str_or_none(meta.get("filename"))
    size = _int_or_none(meta.get("file_size_bytes"))
    if filename is None or size is None or size < 0:
        return None
    return filename, size, _str_or_none(meta.get("mime_type"))


def _conversation_deleted(row: dict) -> bool:
    """The embedded home conversation is soft-deleted. A row with no
    conversation (NULL ``conversation_id``) has nothing to delete it with."""
    conv = row.get("conversations")
    if isinstance(conv, list):
        conv = conv[0] if conv else None
    return isinstance(conv, dict) and conv.get("deleted_at") is not None


def _hidden(row: dict) -> bool:
    if "library_hidden_at" in row:   # a file row (Phase 2): the column is the truth
        return row.get("library_hidden_at") is not None
    return bool(_meta(row).get(HIDDEN_KEY))


def _listable(row: dict) -> bool:
    """D5 on a row already filtered in SQL (owner, attachment, not deleted,
    non-empty text): not hidden from the library and its conversation alive (O4)."""
    return not _hidden(row) and not _conversation_deleted(row)


def _upload_finished(meta: dict) -> bool:
    # None = the legacy multipart route, which writes no upload_status.
    return meta.get("upload_status") in (None, "ready")


def _extraction_complete(meta: dict) -> bool:
    """OCR finished and read every page (§3.8). Conservative: a file that is
    not proposed is simply uploaded again, exactly as today."""
    if meta.get("ocr_status") != "done":
        return False
    if any(meta.get(key) for key in _INCOMPLETE_EXTRACTION_KEYS):
        return False
    read = _int_or_none(meta.get("ocr_pages"))
    pages = _int_or_none(meta.get("page_count"))
    if read is not None and pages is not None and read < pages:
        return False  # fewer pages read than the file has (the pre-budget cap)
    if read == _LEGACY_OCR_PAGE_CAP and pages is None:
        return False  # possibly cut at the old 30-page cap — cannot tell
    return True


def reattach_advised(meta: dict) -> bool:
    """«ننصح بإعادة إرفاق هذا الملف» (owner, 2026-11-20): the extraction is known
    to be shorter than the file and nothing told the user. That is the
    pre-budget 30-page cap (see ``_LEGACY_OCR_PAGE_CAP``): fewer pages read
    than the file has, with NO ``ocr_partial`` marker. A budget-cut file is
    excluded — it carries the marker and its own notice. The UI shows the
    advice without the reason."""
    if (meta or {}).get("ocr_status") != "done" or meta.get("ocr_partial"):
        return False
    read = _int_or_none(meta.get("ocr_pages"))
    pages = _int_or_none(meta.get("page_count"))
    return read is not None and pages is not None and 0 < read < pages


def _ts(row: dict) -> float:
    raw = row.get("created_at")
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _newest_first(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (_ts(r), str(r.get("item_id") or "")), reverse=True)


def _doc_type(meta: dict) -> Optional[str]:
    value = meta.get("doc_type")
    return value if value in DOC_TYPES else None


def _page_count(meta: dict) -> Optional[int]:
    """The file's page count: the document total the OCR cut found, else the
    count taken at upload, else the pages OCR read (legacy uploads)."""
    for key in ("ocr_page_total", "page_count", "ocr_pages"):
        n = _int_or_none(meta.get(key))
        if n is not None and n > 0:
            return n
    return None


def _original_available(row: dict) -> bool:
    meta = _meta(row)
    has_source = bool(row.get("storage_path") or row.get("document_id"))
    return has_source and not meta.get("original_purged_at") and _upload_finished(meta)


def _file_dict(row: dict, *, chars: int, has_sections: bool, copies: int) -> dict:
    """A ``LibraryFile`` (§11) from a light row. Never carries text."""
    meta = _meta(row)
    filename = _str_or_none(meta.get("filename"))
    conversation_id = row.get("conversation_id")
    return {
        "item_id": str(row.get("item_id") or ""),
        "title": str(row.get("title") or filename or ""),
        "filename": filename,
        "mime_type": _str_or_none(meta.get("mime_type")),
        "doc_type": _doc_type(meta),
        "summary_preview": summary_preview(row.get("summary")),
        "page_count": _page_count(meta),
        "chars": max(0, chars),
        "has_sections": has_sections,
        "original_available": _original_available(row),
        "conversation_id": str(conversation_id) if conversation_id else None,
        "wi_seq": _int_or_none(row.get("wi_seq")),
        "copies": max(1, copies),
        "created_at": str(row.get("created_at") or ""),
        "reattach_advised": reattach_advised(meta),
        # True only when the retention sweep removed the original — the one case
        # the viewer may say so. (original_available is also false for a file
        # with no stored original or an unfinished upload.)
        "original_purged": bool(meta.get("original_purged_at")),
    }


def _load_attachments(
    supabase: SupabaseClient,
    user_id: str,
    columns: str,
    *,
    with_text: bool,
    size: Optional[int] = None,
) -> list[dict]:
    """The caller's non-deleted attachment rows, newest first, light columns.

    ``with_text`` filters on non-empty ``content_md`` IN SQL — the column is
    never selected here. ``size`` narrows to one ``metadata.file_size_bytes``;
    the file NAME is compared by the caller in Python on purpose: a filter
    value travels in the request URL, and URLs land in the httpx spans.
    """

    def build() -> Any:
        q = (
            supabase.table(ITEMS_TABLE)
            .select(columns)
            .eq("user_id", user_id)
            .eq("kind", "attachment")
            .is_("deleted_at", "null")
        )
        if with_text:
            q = q.neq("content_md", "")
        if size is not None:
            q = q.eq("metadata->>file_size_bytes", str(size))
        return q.order("created_at", desc=True).order("item_id", desc=True)

    return _fetch_all(build)


def _first(resp: Any) -> Optional[dict]:
    rows = _data(resp)
    return rows[0] if rows else None


def _fold(rows: list[dict]) -> list[tuple[dict, int]]:
    """Identical files → the newest copy + the group size, newest first. A row
    without a name or size stands alone."""
    out: list[list] = []
    groups: dict[tuple, list] = {}
    for row in _newest_first(rows):
        key = _identity(row)
        if key is None:
            out.append([row, 1])
            continue
        entry = groups.get(key)
        if entry is not None:
            entry[1] += 1
            continue
        entry = [row, 1]
        groups[key] = entry
        out.append(entry)
    return [(row, n) for row, n in out]


# ============================================
# The file path (Phase 2 — user_files)
# ============================================

#: A conversation item that links no file (not expected once 222 is in): the
#: caller answers it from the item, exactly as with the switch off.
_UNLINKED = object()


def _load_files(
    supabase: SupabaseClient,
    user_id: str,
    columns: str,
    *,
    with_text: bool,
    size: Optional[int] = None,
) -> list[dict]:
    """``_load_attachments`` on ``user_files``: the caller's live files, newest
    first. ``with_text`` filters on ``content_chars > 0`` in SQL (the text is
    never selected); ``size`` on the ``file_size_bytes`` column. The file NAME
    is still compared by the caller in Python (see ``_load_attachments``)."""

    def build() -> Any:
        q = (
            supabase.table(FILES_TABLE)
            .select(columns)
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
        )
        if with_text:
            q = q.gt("content_chars", 0)
        if size is not None:
            q = q.eq("file_size_bytes", size)
        return q.order("created_at", desc=True).order("file_id", desc=True)

    return _fetch_all(build)


def _live_pointers(frow: dict) -> list[dict]:
    """The file's live conversation items (embedded), oldest first."""
    raw = frow.get(ITEMS_TABLE)
    if isinstance(raw, dict):
        raw = [raw]
    live = [
        p for p in (raw or [])
        if isinstance(p, dict) and p.get("item_id") and p.get("deleted_at") is None
    ]
    return sorted(live, key=lambda p: (_ts(p), str(p.get("item_id") or "")))


def _file_row(frow: dict, pointer: Optional[dict] = None) -> dict:
    """A ``user_files`` row in the shape the row helpers above read. The
    conversation facts (``item_id``, WI number, home conversation) come from
    ``pointer`` when given, else from the home pointer — the oldest live one
    whose conversation is not deleted (a file attached to several conversations
    stays in the library while any of them lives, O4), else the oldest live one.
    A file with no live pointer answers with its own id."""
    live = _live_pointers(frow)
    alive = [p for p in live if not _conversation_deleted(p)]
    home = pointer or (alive[0] if alive else None) or (live[0] if live else None) or {}
    file_id = str(frow.get("file_id") or "")
    return {
        "item_id": str(home.get("item_id") or file_id),
        "file_id": file_id,
        "conversation_id": home.get("conversation_id"),
        "wi_seq": home.get("wi_seq"),
        "conversations": home.get("conversations"),
        "title": frow.get("title"),
        "summary": frow.get("summary"),
        "metadata": frow.get("metadata"),
        "storage_path": frow.get("storage_path"),
        "document_id": frow.get("document_id"),
        "created_at": frow.get("created_at"),
        "content_chars": frow.get("content_chars"),
        "library_hidden_at": frow.get("library_hidden_at"),
        "_pointers": live,
    }


def _resolve_pointer(
    supabase: SupabaseClient, user_id: str, item_id: str, columns: str
) -> Optional[dict]:
    """The caller's live attachment item ``item_id`` (raises on a failed read)."""
    return _first(
        supabase.table(ITEMS_TABLE)
        .select(columns)
        .eq("item_id", item_id)
        .eq("user_id", user_id)
        .eq("kind", "attachment")
        .is_("deleted_at", "null")
        .limit(1)
        .execute()
    )


def _load_one_file(supabase: SupabaseClient, user_id: str, file_id: str, columns: str) -> Optional[dict]:
    """The caller's live file ``file_id`` (raises on a failed read)."""
    return _first(
        supabase.table(FILES_TABLE)
        .select(columns)
        .eq("file_id", file_id)
        .eq("user_id", user_id)
        .is_("deleted_at", "null")
        .limit(1)
        .execute()
    )


def _list_from_files(supabase: SupabaseClient, user_id: str) -> dict:
    """``list_library_files`` on ``user_files``. ``chars`` is the file's
    ``content_chars``; maps are looked up by the file's id."""
    try:
        rows = [_file_row(f) for f in _load_files(supabase, user_id, _FILE_LIST_COLUMNS, with_text=True)]
    except Exception as exc:  # noqa: BLE001
        logger.exception("files: library read failed (%s)", type(exc).__name__)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب الملفات",
        )

    folded = _fold([r for r in rows if _listable(r)])
    shown = folded[:LIBRARY_CAP]

    expected: dict[str, int] = {}
    for row, _n in shown:
        chars = _int_or_none(row.get("content_chars"))
        if chars is not None and chars > 0:
            expected[row["file_id"]] = chars
    fresh = _fresh_map_ids(supabase, user_id, expected)

    type_counts: dict[str, int] = {}
    for row, _n in folded:
        key = _doc_type(_meta(row)) or UNTYPED
        type_counts[key] = type_counts.get(key, 0) + 1

    files = []
    for row, copies in shown:
        fid = row["file_id"]
        entry = _file_dict(row, chars=expected.get(fid, 0), has_sections=fid in fresh, copies=copies)
        entry["file_id"] = fid
        files.append(entry)
    return {"files": files, "total": len(folded), "type_counts": type_counts}


def _copies_from_files(supabase: SupabaseClient, user_id: str, row: dict) -> int:
    """``_copies`` on ``user_files``: group membership by file id."""
    key = _identity(row)
    if key is None:
        return 1
    try:
        rows = [
            _file_row(f)
            for f in _load_files(supabase, user_id, _FILE_KEY_COLUMNS, with_text=True, size=key[1])
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("files: copies read failed for %s (%s)", row.get("item_id"), type(exc).__name__)
        return 1
    group = [r for r in rows if _listable(r) and _identity(r) == key]
    if not any(r["file_id"] == row["file_id"] for r in group):
        return 1
    return len(group)


def _get_from_files(supabase: SupabaseClient, user_id: str, item_id: str) -> Any:
    """``get_library_file`` on ``user_files``. ``item_id`` is resolved as one of
    the caller's live attachment items first (the pane and the page pass that),
    then as a file id. ``_UNLINKED`` when the item links no file."""
    try:
        pointer = _resolve_pointer(
            supabase, user_id, item_id, "item_id, file_id, conversation_id, wi_seq, created_at",
        )
        if pointer is not None and not pointer.get("file_id"):
            return _UNLINKED
        file_id = str(pointer["file_id"]) if pointer is not None else item_id
        frow = _load_one_file(supabase, user_id, file_id, _FILE_DETAIL_COLUMNS)
    except Exception as exc:  # noqa: BLE001
        logger.exception("files: file read failed for %s (%s)", item_id, type(exc).__name__)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب الملف",
        )
    if frow is None:
        return None

    row = _file_row(frow, pointer)
    content = frow.get("content_md") or ""
    chars = len(content)   # the REAL length: map freshness and the card's size
    fixes = _text_fixes(supabase, user_id, row["file_id"]) if chars else []
    sections, documents = (
        _sections(supabase, user_id, row["file_id"], chars, fixes=fixes) if chars else (None, [])
    )
    detail = _file_dict(
        row,
        chars=chars,
        has_sections=sections is not None,
        copies=_copies_from_files(supabase, user_id, row),
    )
    summary = frow.get("summary")
    detail.update({
        "file_id": row["file_id"],
        "summary": summary if isinstance(summary, str) else None,
        "content_md": apply_text_corrections(content, fixes),   # FileViewer text + Copy
        "documents": documents,
        "sections": sections,
    })
    return detail


def _hide_with_files(supabase: SupabaseClient, user_id: str, item_id: str) -> Any:
    """``hide_library_file`` as ONE statement (``user_files_hide``): the file
    and every identical copy, first stamp kept, no read-merge-write of
    ``metadata``. ``_UNLINKED`` when the item links no file."""
    try:
        pointer = _resolve_pointer(supabase, user_id, item_id, "item_id, file_id")
        if pointer is not None and not pointer.get("file_id"):
            return _UNLINKED
        file_id = str(pointer["file_id"]) if pointer is not None else item_id
        frow = _load_one_file(supabase, user_id, file_id, "file_id, metadata")
        if frow is None:
            return False
        targets = [file_id]
        key = _identity(frow)
        if key is not None:
            siblings = _load_files(supabase, user_id, "file_id, metadata", with_text=False, size=key[1])
            targets += [
                str(r.get("file_id")) for r in siblings
                if str(r.get("file_id")) != file_id and _identity(r) == key
            ]
    except Exception as exc:  # noqa: BLE001
        logger.exception("files: hide read failed for %s (%s)", item_id, type(exc).__name__)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء حذف الملف",
        )
    try:
        files_store.hide_files(supabase, user_id, targets)
    except Exception as exc:  # noqa: BLE001
        logger.warning("files: hide write failed for %s (%s)", item_id, type(exc).__name__)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء حذف الملف",
        )
    logger.info("files: hid %s (%d row(s) in its group)", item_id, len(targets))
    return True


def _match_from_files(
    supabase: SupabaseClient,
    user_id: str,
    key: tuple[str, int, Optional[str]],
    conversation_id: Optional[str],
) -> Optional[dict]:
    """``match_library_file`` on ``user_files``. A same-conversation match
    answers with THAT conversation's item (its ``item_id`` / WI number)."""
    try:
        rows = [
            _file_row(f)
            for f in _load_files(supabase, user_id, _FILE_KEY_COLUMNS, with_text=True, size=key[1])
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("files: match read failed (%s) — no proposal", type(exc).__name__)
        return None

    candidates = _newest_first([
        r for r in rows
        if _identity(r) == key
        and _listable(r)
        and _upload_finished(_meta(r))
        and _extraction_complete(_meta(r))
    ])
    target_conv = (conversation_id or "").strip().lower()
    same: list[tuple[dict, dict]] = []
    if target_conv:
        for r in candidates:
            p = next(
                (p for p in r["_pointers"]
                 if str(p.get("conversation_id") or "").strip().lower() == target_conv),
                None,
            )
            if p is not None:
                same.append((r, p))
    if same:
        pick, pointer = same[0]
        item_id, wi_seq, same_conversation = pointer.get("item_id"), pointer.get("wi_seq"), True
    elif CROSS_CONVERSATION_MATCH and candidates:  # Phase 3 — see the flag
        pick = candidates[0]
        item_id, wi_seq, same_conversation = pick.get("item_id"), pick.get("wi_seq"), False
    else:
        return None

    meta = _meta(pick)
    return {
        "item_id": str(item_id or ""),
        "title": str(pick.get("title") or _str_or_none(meta.get("filename")) or ""),
        "created_at": str(pick.get("created_at") or ""),
        "same_conversation": same_conversation,
        "wi_seq": _int_or_none(wi_seq),
    }


# ============================================
# GET /files
# ============================================


def _fresh_map_ids(supabase: SupabaseClient, user_id: str, expected: dict[str, int]) -> set[str]:
    """Items whose stored map was made from text of the expected length.
    Fail-soft: a failed read shows every file without sections."""
    if not expected:
        return set()
    try:
        rows = _fetch_all(
            lambda: supabase.table(MAPS_TABLE)
            .select("item_id, content_chars")
            .eq("user_id", user_id)
            .order("item_id")
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("files: map read failed (%s) — listing without sections", type(exc).__name__)
        return set()
    fresh: set[str] = set()
    for r in rows:
        iid = str(r.get("item_id") or "")
        if iid in expected and _int_or_none(r.get("content_chars")) == expected[iid]:
            fresh.add(iid)
    return _with_shown_sections(supabase, fresh)


def _with_shown_sections(supabase: SupabaseClient, ids: set[str]) -> set[str]:
    """Of ``ids`` (fresh maps), those with at least one section the viewer shows.
    A map whose every section is a letterhead or a repeat opens as plain text
    (``_sections`` returns None), so its card must not say «مقسّم إلى أقسام».
    Reads ``item_id, value`` only. Fail-soft: on a failed read the ids stay."""
    if not ids:
        return ids
    shown: set[str] = set()
    ordered = sorted(ids)
    try:
        for i in range(0, len(ordered), 50):
            chunk = ordered[i:i + 50]
            rows = _fetch_all(
                lambda chunk=chunk: supabase.table(SEGMENTS_TABLE)
                .select("item_id, value")
                .in_("item_id", chunk)
                .order("item_id")
            )
            for r in rows:
                if r.get("value") not in HIDDEN_SECTION_VALUES:
                    shown.add(str(r.get("item_id") or ""))
    except Exception as exc:  # noqa: BLE001
        logger.warning("files: section-value read failed (%s) — keeping the map list", type(exc).__name__)
        return ids
    return ids & shown


def list_library_files(supabase: SupabaseClient, user_id: str) -> dict:
    """``LibraryFileListResponse`` (§11) — the user's library, light rows only.

    D5: ``kind='attachment'``, not deleted, non-empty extracted text, home
    conversation not soft-deleted, not hidden from the library. Identical
    files are folded into the newest copy (O6). ``content_md`` is never
    selected and the full summary never returned: ``chars`` comes from
    ``metadata.ocr_chars`` (equal to ``len(content_md)`` on every row with
    text) and ``has_sections`` compares the map against it.

    ``total`` and ``type_counts`` describe the whole folded library; ``files``
    is its first ``LIBRARY_CAP`` rows.

    Files read switch on: the same list from ``user_files`` (``_list_from_files``).
    """
    if files_flags.files_read_enabled(user_id, supabase=supabase):
        return _list_from_files(supabase, user_id)
    try:
        rows = _load_attachments(supabase, user_id, _LIST_COLUMNS, with_text=True)
    except Exception as exc:  # noqa: BLE001
        logger.exception("files: library read failed (%s)", type(exc).__name__)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب الملفات",
        )

    folded = _fold([r for r in rows if _listable(r)])
    shown = folded[:LIBRARY_CAP]

    expected: dict[str, int] = {}
    for row, _n in shown:
        chars = _int_or_none(_meta(row).get("ocr_chars"))
        if chars is not None and chars > 0:
            expected[str(row.get("item_id"))] = chars
    fresh = _fresh_map_ids(supabase, user_id, expected)

    type_counts: dict[str, int] = {}
    for row, _n in folded:
        key = _doc_type(_meta(row)) or UNTYPED
        type_counts[key] = type_counts.get(key, 0) + 1

    files = []
    for row, copies in shown:
        iid = str(row.get("item_id"))
        files.append(
            _file_dict(row, chars=expected.get(iid, 0), has_sections=iid in fresh, copies=copies)
        )
    return {"files": files, "total": len(folded), "type_counts": type_counts}


# ============================================
# GET /files/{item_id}
# ============================================


def _documents(raw: Any) -> list[tuple[str, Optional[int], Optional[int]]]:
    """``documents_in_file`` → [(name, from_p, to_p)] in stored order."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if not isinstance(raw, list):
        return []
    docs = []
    for d in raw:
        if isinstance(d, dict):
            docs.append(
                (str(d.get("name") or "").strip(), _int_or_none(d.get("from_p")), _int_or_none(d.get("to_p")))
            )
    return docs


def _document_index(docs: list[tuple[str, Optional[int], Optional[int]]], from_p: Optional[int]) -> int:
    """The document whose paragraph range holds ``from_p``; a section in a gap
    joins the last document that starts before it (else the first)."""
    if from_p is None:
        return 0
    for i, (_name, a, b) in enumerate(docs):
        if a is not None and b is not None and a <= from_p <= b:
            return i
    best: Optional[int] = None
    for i, (_name, a, _b) in enumerate(docs):
        if a is not None and a <= from_p and (best is None or a >= (docs[best][1] or 0)):
            best = i
    return best if best is not None else 0


def _text_fixes(supabase: SupabaseClient, user_id: str, file_key: str) -> list[TextCorrection]:
    """The file's active text corrections, oldest first (attachment_agent.md §3.3:
    an overlay — ``content_md`` is never rewritten). ``file_key`` — the file id on
    the files path, the item id on the items path (a file's first item has
    ``file_id = item_id``). ``[]`` when there are none or the read failed (the
    loader never raises) — and, with NO request, when the attachment agent is off
    for the user (only the agent writes corrections)."""
    if not edits_enabled(user_id):
        return []
    return load_text_corrections(supabase, [file_key], user_id=user_id).get(str(file_key)) or []


def _sections(
    supabase: SupabaseClient,
    user_id: str,
    item_id: str,
    chars: int,
    *,
    fixes: Optional[list[TextCorrection]] = None,
) -> tuple[Optional[list[dict]], list[dict]]:
    """``(sections, documents)`` for the opened file (§11 sections rule).

    No map, a stale map (``content_chars`` ≠ the text's length), no rows left
    once letterheads and repeats are removed, or a failed read → ``(None, [])``:
    the client shows the full text. The English ``label`` is never selected
    and the core/supporting rating leaves only as ``is_metadata``.

    The user's edits (attachment_agent.md §3.1 / §3.3), both keyed by the file
    (``item_id`` here is the key the map is stored under): a section they
    PROMOTED reads as ``core`` — so a promoted letterhead / repeat is shown and a
    promoted metadata section is no longer ``is_metadata`` (this reads the
    segments directly, not through ``load_segment_maps``, so the promotions are
    applied here); ``fixes`` — the file's text corrections — on each section's
    text. Positions and the freshness check stay on the REAL text. A failed
    promotions read keeps the stored values; with the attachment agent off for the
    user, promotions are not read at all.
    """
    try:
        smap = _first(
            supabase.table(MAPS_TABLE)
            .select("content_chars, documents_in_file")
            .eq("item_id", item_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if smap is None or _int_or_none(smap.get("content_chars")) != chars:
            return None, []
        rows = _fetch_all(
            lambda: supabase.table(SEGMENTS_TABLE)
            .select(
                "seq, from_p, page_from, page_to, value, what, content_md, "
                "owner_class, owner, brought_by, "   # seg-v5.2 (migration 220); NULL on older maps
                "char_start, char_end"               # where the user's promotions fall (234)
            )
            .eq("item_id", item_id)
            .order("seq")
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("files: section read failed for %s (%s) — full text", item_id, type(exc).__name__)
        return None, []

    promoted = (
        load_segment_overrides(supabase, [item_id], user_id=user_id).get(str(item_id))
        if edits_enabled(user_id) else None
    )
    if promoted:
        rows = apply_segment_overrides(rows, promoted)
    shown = [r for r in rows if r.get("value") not in HIDDEN_SECTION_VALUES]
    if not shown:
        return None, []
    docs = _documents(smap.get("documents_in_file"))
    several = len(docs) >= 2
    sections = []
    for r in sorted(shown, key=lambda r: _int_or_none(r.get("seq")) or 0):
        sections.append({
            "seq": _int_or_none(r.get("seq")) or 0,
            "what": str(r.get("what") or ""),
            "is_metadata": r.get("value") == "metadata",
            "page_from": _int_or_none(r.get("page_from")),
            "page_to": _int_or_none(r.get("page_to")),
            "document_index": _document_index(docs, _int_or_none(r.get("from_p"))) if several else None,
            "content_md": str(r.get("content_md") or ""),
            **_section_owner(r),
        })
    documents = [{"name": name} for name, _a, _b in docs] if several else []
    return apply_to_sections(sections, fixes), documents


def _section_owner(row: dict) -> dict:
    """Whose words a section carries (seg-v5.2: ``owner_class`` is a fixed key,
    ``owner`` the document's own role words, ``brought_by`` the party that filed
    it when that is not the owner). ``unknown`` and blanks leave as ``None`` —
    the client then shows nothing rather than a guess. Maps made before
    seg-v5.2 have all three NULL."""
    owner_class = str(row.get("owner_class") or "").strip().lower() or None
    if owner_class == "unknown":
        owner_class = None
    owner = " ".join(str(row.get("owner") or "").split()) or None
    brought_by = " ".join(str(row.get("brought_by") or "").split()) or None
    return {"owner_class": owner_class, "owner": owner, "brought_by": brought_by}


def _copies(supabase: SupabaseClient, user_id: str, item_id: str, row: dict) -> int:
    """The ``copies`` the list shows for this file: the size of its folded
    group when the file is itself listed, else 1. Fail-soft to 1."""
    key = _identity(row)
    if key is None:
        return 1
    try:
        rows = _load_attachments(supabase, user_id, _KEY_COLUMNS, with_text=True, size=key[1])
    except Exception as exc:  # noqa: BLE001
        logger.warning("files: copies read failed for %s (%s)", item_id, type(exc).__name__)
        return 1
    group = [r for r in rows if _listable(r) and _identity(r) == key]
    if not any(str(r.get("item_id")) == str(item_id) for r in group):
        return 1
    return len(group)


def get_library_file(supabase: SupabaseClient, user_id: str, item_id: str) -> Optional[dict]:
    """``LibraryFileDetail`` (§11), or ``None`` when the item is not one of the
    caller's non-deleted attachments.

    Works for ANY such attachment — also one the library does not list
    (hidden, a folded copy, no text yet, a deleted conversation's file): the
    conversation pane opens its files through this same endpoint. Staleness
    is judged on the real ``len(content_md)`` here.

    Files read switch on: read from ``user_files`` (``_get_from_files``) —
    ``item_id`` may then also be a file id.
    """
    if files_flags.files_read_enabled(user_id, supabase=supabase):
        out = _get_from_files(supabase, user_id, item_id)
        if out is not _UNLINKED:
            return out
    try:
        row = _first(
            supabase.table(ITEMS_TABLE)
            .select(_DETAIL_COLUMNS)
            .eq("item_id", item_id)
            .eq("user_id", user_id)
            .eq("kind", "attachment")
            .is_("deleted_at", "null")
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("files: file read failed for %s (%s)", item_id, type(exc).__name__)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب الملف",
        )
    if row is None:
        return None

    content = row.get("content_md") or ""
    chars = len(content)   # the REAL length: map freshness and the card's size
    fixes = _text_fixes(supabase, user_id, item_id) if chars else []
    sections, documents = (
        _sections(supabase, user_id, item_id, chars, fixes=fixes) if chars else (None, [])
    )
    detail = _file_dict(
        row,
        chars=chars,
        has_sections=sections is not None,
        copies=_copies(supabase, user_id, item_id, row),
    )
    summary = row.get("summary")
    detail.update({
        "summary": summary if isinstance(summary, str) else None,
        "content_md": apply_text_corrections(content, fixes),   # FileViewer text + Copy
        "documents": documents,
        "sections": sections,
    })
    return detail


# ============================================
# DELETE /files/{item_id}
# ============================================


def hide_library_file(supabase: SupabaseClient, user_id: str, item_id: str) -> bool:
    """«حذف من ملفاتي» (§3.7, D9): stamp ``metadata.library_hidden_at`` on the
    file AND on every identical copy the user holds (same name + size + type),
    so a folded group leaves the library as one. Existing metadata keys are
    kept; a row already hidden keeps its first stamp. No row is deleted and the
    conversation's item is otherwise untouched.

    ``False`` when ``item_id`` is not one of the caller's non-deleted
    attachments (→ 404). Raises 500 when a write fails, so the client retries
    (the stamp is idempotent).

    Known limit: ``metadata`` is rewritten whole (read-merge-write, like every
    other metadata writer on this table). A writer that read the row before
    this stamp and writes after it — OCR or the summarizer finishing on a copy
    uploaded seconds ago — can drop the stamp on that copy.

    Files WRITE switch on: one atomic ``user_files_hide`` statement instead
    (``_hide_with_files``) — the ``library_hidden_at`` column cannot be dropped
    by another writer's stale metadata (plan §12 item 7).
    """
    if files_flags.files_write_enabled(user_id, supabase=supabase):
        out = _hide_with_files(supabase, user_id, item_id)
        if out is not _UNLINKED:
            return out
    try:
        row = _first(
            supabase.table(ITEMS_TABLE)
            .select("item_id, metadata")
            .eq("item_id", item_id)
            .eq("user_id", user_id)
            .eq("kind", "attachment")
            .is_("deleted_at", "null")
            .limit(1)
            .execute()
        )
        if row is None:
            return False
        targets = [row]
        key = _identity(row)
        if key is not None:
            siblings = _load_attachments(
                supabase, user_id, "item_id, metadata", with_text=False, size=key[1]
            )
            targets += [
                r for r in siblings
                if str(r.get("item_id")) != str(row.get("item_id")) and _identity(r) == key
            ]
    except Exception as exc:  # noqa: BLE001
        logger.exception("files: hide read failed for %s (%s)", item_id, type(exc).__name__)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء حذف الملف",
        )

    stamp = datetime.now(timezone.utc).isoformat()
    failed = 0
    for target in targets:
        meta = _meta(target)
        if meta.get(HIDDEN_KEY):
            continue
        merged = dict(meta)
        merged[HIDDEN_KEY] = stamp
        try:
            (
                supabase.table(ITEMS_TABLE)
                .update({"metadata": merged})
                .eq("item_id", target.get("item_id"))
                .eq("user_id", user_id)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.warning(
                "files: hide write failed for %s (%s)", target.get("item_id"), type(exc).__name__
            )
    if failed:
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء حذف الملف",
        )
    logger.info("files: hid %s (%d row(s) in its group)", item_id, len(targets))
    return True


# ============================================
# POST /files/match (Phase 1b)
# ============================================


def match_library_file(
    supabase: SupabaseClient,
    user_id: str,
    *,
    filename: str,
    size_bytes: int,
    mime_type: str,
    conversation_id: Optional[str] = None,
) -> Optional[dict]:
    """``FileMatch`` (§11) for the file about to be uploaded, or ``None``.

    §3.8: same user + same file name + same size + same type; only a copy
    whose text was extracted completely (``ocr_status='done'``, non-empty
    text, no partial / lossy / text-layer marker — see
    ``_extraction_complete``); not deleted, not hidden from the library, its
    conversation alive, its upload finished. Several copies → the newest.

    A copy inside ``conversation_id`` wins over a newer one elsewhere: it is
    re-attached as the same item (same WI number). Copies in other
    conversations are offered only when ``CROSS_CONVERSATION_MATCH`` is on
    (Phase 3). Never raises: a failed read is "no match" — the upload then
    goes ahead exactly as today.

    Files read switch on: matched on ``user_files`` (``_match_from_files``); a
    same-conversation match answers with that conversation's item.
    """
    size = _int_or_none(size_bytes)
    if not filename or size is None:
        return None
    key = (filename, size, mime_type or None)
    if files_flags.files_read_enabled(user_id, supabase=supabase):
        return _match_from_files(supabase, user_id, key, conversation_id)
    try:
        rows = _load_attachments(supabase, user_id, _KEY_COLUMNS, with_text=True, size=size)
    except Exception as exc:  # noqa: BLE001
        logger.warning("files: match read failed (%s) — no proposal", type(exc).__name__)
        return None

    candidates = _newest_first([
        r for r in rows
        if _identity(r) == key
        and _listable(r)
        and _upload_finished(_meta(r))
        and _extraction_complete(_meta(r))
    ])
    target_conv = (conversation_id or "").strip().lower()
    same = [
        r for r in candidates
        if target_conv and str(r.get("conversation_id") or "").strip().lower() == target_conv
    ]
    if same:
        pick, same_conversation = same[0], True
    elif CROSS_CONVERSATION_MATCH and candidates:  # Phase 3 — see the flag
        pick, same_conversation = candidates[0], False
    else:
        return None

    meta = _meta(pick)
    return {
        "item_id": str(pick.get("item_id") or ""),
        "title": str(pick.get("title") or _str_or_none(meta.get("filename")) or ""),
        "created_at": str(pick.get("created_at") or ""),
        "same_conversation": same_conversation,
        "wi_seq": _int_or_none(pick.get("wi_seq")),
    }


# ============================================
# Attach a library file to a conversation (Phase 3, migration 227)
# ============================================

#: PostgREST: the function is not in its schema cache (227 not applied yet).
_PGRST_NO_FUNCTION = "PGRST202"
#: user_file_attach refuses (file not the caller's / deleted / hidden / no text,
#: or conversation not the caller's / deleted).
_SQL_NO_DATA = "P0002"


def attach_library_file(
    supabase: SupabaseClient, user_id: str, conversation_id: str, file_or_item_id: str
) -> dict:
    """Put one of the caller's library files into ``conversation_id``; returns
    the conversation's item row (``workspace_items``).

    ``file_or_item_id`` is the file's id (``GET /files`` → ``file_id``) or one
    of its conversation items' ids (the card's ``item_id``). One call to
    ``user_file_attach``: a new item whose fields are copied from the file in
    the database — no new extraction, summary or segmentation — or the file's
    live item already in that conversation. The caller verifies the
    conversation's ownership first; the function checks it again.
    """
    wanted = str(file_or_item_id)
    try:
        file_id = files_store.file_ids_for_items(supabase, [wanted], user_id=user_id).get(wanted, wanted)
        resp = supabase.rpc("user_file_attach", {
            "p_user_id": str(user_id),
            "p_conversation_id": str(conversation_id),
            "p_file_id": file_id,
        }).execute()
    except Exception as exc:  # noqa: BLE001
        code = str(getattr(exc, "code", "") or "")
        if code == _SQL_NO_DATA:
            raise LunaHTTPException(status_code=404, code=ErrorCode.DOC_NOT_FOUND, detail="الملف غير موجود")
        if code == _PGRST_NO_FUNCTION:
            logger.error("files: user_file_attach missing (migration 227 not applied)")
            raise LunaHTTPException(status_code=503, code=ErrorCode.FEATURE_OFF, detail="هذه الميزة غير متاحة حالياً")
        logger.exception("files: attach failed for %s (%s)", wanted, type(exc).__name__)
        raise LunaHTTPException(
            status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء إرفاق الملف",
        )
    data = getattr(resp, "data", None)
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict) or not data.get("item_id"):
        logger.error("files: attach returned no row for %s", wanted)
        raise LunaHTTPException(
            status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء إرفاق الملف",
        )
    logger.info("files: attached file %s to conversation %s as item %s", file_id, conversation_id, data["item_id"])
    return data


__all__ = [
    "CROSS_CONVERSATION_MATCH",
    "DOC_TYPES",
    "HIDDEN_KEY",
    "LIBRARY_CAP",
    "PREVIEW_MAX_CHARS",
    "attach_library_file",
    "get_library_file",
    "hide_library_file",
    "list_library_files",
    "match_library_file",
    "my_files_enabled",
    "summary_preview",
]
