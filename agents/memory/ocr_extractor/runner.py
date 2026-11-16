"""Entry point for the OCR extraction step.

``run_ocr_extraction`` runs once per turn (before the router): it finds
un-extracted PDF/image attachments on the conversation, OCRs each via Mistral,
and writes the text back into the attachment's own ``workspace_items.content_md``.
With ``ATTACHMENT_SEGMENTER_ENABLED`` on for the user it also writes one
``attachment_pages`` row per page (migration 215), best-effort.

How much of each file is read is the OCR page budget's call
(``shared/quota/ocr_budget.py``, plan ``ocr_page_budget.md``): one quota read
per run, all of the run's files planned together (smallest first). A file the
budget cannot fully cover is read in part — the text then marks every skipped
run of pages (``partial.py``) and the stats carry a notice for the user. A file
with no budget left is left untouched (no ``ocr_status``), so it is read on a
later turn once pages are available again.

Best-effort by contract — it must **never raise**. Every per-candidate failure
is caught, logged, and recorded on the row as an ``ocr_status`` marker so the
file is never re-attempted. The function returns whatever succeeded.
"""
from __future__ import annotations

import logging

from supabase import Client as SupabaseClient

from agents.utils.segmenter_flags import segmenter_enabled
from agents.utils.tracking import track_stage
from agents.utils.usage_sink import record_call
from shared import quota
from shared.config import get_settings
from shared.observability import get_logfire
from shared.quota.ocr_budget import FilePlan, OcrBudget, page_ranges, plan_files
from shared.storage.client import get_signed_url

from .big_file import ocr_big_pdf
from .mistral_ocr import MISTRAL_MAX_BYTES, MISTRAL_MAX_PAGES, ocr_document
from .models import OcrDocumentResult, OcrExtractionStats, OcrPage, PartialRead
from .partial import assemble_partial

logger = logging.getLogger(__name__)
_logfire = get_logfire()

# ---------------------------------------------------------------------------
# Module constants (see .claude/plans/ocr_extraction_agent.md)
# ---------------------------------------------------------------------------
# Over this → skipped_too_large. Files up to MISTRAL_MAX_BYTES go to Mistral by
# URL; bigger ones take the big-file path (big_file.py). The upload routes cap
# attachments separately (upload_session_service.MAX_FILE_SIZE).
OCR_MAX_FILE_BYTES = 512 * 1024 * 1024
__all__ = ["run_ocr_extraction", "OCR_MAX_FILE_BYTES", "budget_notices"]

_SUPPORTED_MIME_TYPES = {"application/pdf", "image/png", "image/jpeg"}
_SIGNED_URL_EXPIRY_S = 3600  # ~1 hour — Mistral fetches the file server-side


def _mark_status(
    supabase: SupabaseClient,
    item_id: str,
    metadata: dict,
    status: str,
    extra: dict | None = None,
) -> None:
    """Merge OCR result keys into the attachment row's ``metadata`` and persist.

    ``metadata.ocr_status`` is the idempotency marker — any non-null value means
    "never attempt this file again". Best-effort: a write failure is logged, not
    raised (the file would simply be retried on a later turn).
    """
    merged = dict(metadata or {})
    merged["ocr_status"] = status
    if extra:
        merged.update(extra)
    try:
        (
            supabase.table("workspace_items")
            .update({"metadata": merged})
            .eq("item_id", item_id)
            .execute()
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "OCR: failed to write metadata.ocr_status=%s for item %s",
            status,
            item_id,
            exc_info=True,
        )


def _store_pages(
    supabase: SupabaseClient,
    item_id: str,
    user_id: str,
    pages: list[OcrPage],
) -> int:
    """Upsert one ``attachment_pages`` row per OCR page (migration 215).

    ``char_start`` is the page's offset inside the ``content_md`` just written,
    so the segmenter can number pages. Best-effort: a failure is logged and
    costs only the page numbers (the segmenter then works from ``content_md``
    alone) — it never fails the OCR. Returns the rows written.
    """
    if not pages:
        return 0
    rows = [
        {
            "item_id": item_id,
            "user_id": user_id,
            "page_no": p.page_no,
            "content_md": p.markdown,
            "char_start": p.char_start,
        }
        for p in pages
    ]
    try:
        (
            supabase.table("attachment_pages")
            .upsert(rows, on_conflict="item_id,page_no")
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "OCR: attachment_pages write failed for item %s (%s) — the segmenter "
            "will work without page numbers",
            item_id,
            type(exc).__name__,
        )
        return 0
    return len(rows)


def _file_size(metadata: dict) -> int:
    try:
        return int(metadata.get("file_size_bytes") or 0)
    except (TypeError, ValueError):
        return 0


def _page_count(metadata: dict) -> int | None:
    """The file's page count for planning: 1 for an image, else the upload-time
    estimate (``metadata.page_count``); None when unknown."""
    if str(metadata.get("mime_type") or "").startswith("image/"):
        return 1
    try:
        n = int(metadata.get("page_count"))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _display_name(row: dict) -> str:
    meta = row.get("metadata") or {}
    return str(meta.get("filename") or row.get("title") or "المرفق")


def _needs_cutting(metadata: dict, plan: FilePlan) -> bool:
    """Read a PDF by cutting out its pages (big_file.py) rather than handing
    Mistral the whole file: when it is read in part (fewer bytes to Mistral, and
    the real page count replaces the client's estimate), when its length is
    unknown, or when it is over one of Mistral's limits."""
    if metadata.get("mime_type") != "application/pdf":
        return False
    return (
        not plan.complete
        or plan.total_pages is None
        or plan.total_pages > MISTRAL_MAX_PAGES
        or _file_size(metadata) > MISTRAL_MAX_BYTES
    )


async def _ocr(
    signed_url: str, mime_type: str, metadata: dict, plan: FilePlan, item_id: str,
) -> OcrDocumentResult:
    if not _needs_cutting(metadata, plan):
        return await ocr_document(signed_url, mime_type, plan.pages)
    try:
        return await ocr_big_pdf(signed_url, plan.pages, plan.total_pages)
    except Exception:  # noqa: BLE001
        # A PDF pypdf cannot open (encrypted, damaged) can still go to Mistral
        # whole, with the page list — when it is within Mistral's limits.
        if _file_size(metadata) > MISTRAL_MAX_BYTES or (plan.total_pages or 0) > MISTRAL_MAX_PAGES:
            raise
        logger.warning("OCR: cutting failed for item %s — sending the whole file", item_id, exc_info=True)
        return await ocr_document(signed_url, mime_type, plan.pages)


def _partial_total(plan: FilePlan, result: OcrDocumentResult) -> int | None | bool:
    """``False`` when the whole document was read; otherwise the document's page
    total for the partial-read notice (None = not known)."""
    total = result.total_pages if result.total_pages is not None else plan.total_pages
    if total is not None:
        return False if result.page_count >= total else total
    if plan.pages is None:
        return False
    # Unknown length: fewer pages back than asked for means the document ended.
    return False if result.page_count < len(plan.pages) else None


async def _read_budget(supabase: SupabaseClient, user_id: str) -> OcrBudget:
    return await quota.ocr_budget(supabase, user_id)


def budget_notices(stats: OcrExtractionStats | None) -> list[str]:
    """The user-facing lines for this run's partial and unread files (D8b).

    Shown by the orchestrator as ``status`` events, on both OCR paths.
    """
    if stats is None:
        return []
    from shared.quota.ocr_budget import format_ranges

    lines: list[str] = []
    for pr in getattr(stats, "partial", None) or []:
        if pr.total_pages is None:
            lines.append(
                f"قرأنا أول {len(pr.pages)} صفحة من «{pr.name}» حسب رصيدك، "
                "وقد تكون فيه صفحات أخرى لم تُقرأ."
            )
        else:
            lines.append(
                f"قرأنا {len(pr.pages)} صفحة من أصل {pr.total_pages} من «{pr.name}» "
                f"حسب رصيدك: {format_ranges(pr.pages)}. بقية الصفحات لم تُقرأ."
            )
    for name in getattr(stats, "no_budget", None) or []:
        lines.append(f"لم نقرأ «{name}» لأن رصيد قراءة الملفات نفد.")
    return lines


async def run_ocr_extraction(
    supabase: SupabaseClient,
    conversation_id: str,
    user_id: str,
) -> OcrExtractionStats:
    """Extract text from new PDF/image attachments on a conversation.

    Loads ``kind='attachment'`` workspace items for the conversation, OCRs each
    one that has never been attempted (no ``metadata.ocr_status``), writes the
    text into the row's ``content_md``, and records an ``agent_runs`` row per
    successful extraction.

    Args:
        supabase: A (sync) Supabase client — the established pattern is to use
            it inside async functions.
        conversation_id: The conversation whose attachments to scan.
        user_id: The ``users.user_id`` of the conversation owner.

    Returns:
        The run's ``OcrExtractionStats`` — ``filled_item_ids`` lists the
        attachments whose ``content_md`` was filled this run, and ``failed``
        counts attachments that errored (the orchestrator surfaces a status
        event to the user when ``failed`` > 0). On a global failure the stats
        carry whatever succeeded (dev-safe no-op).

    Never raises — every failure path is caught and logged.
    """
    stats = OcrExtractionStats()
    settings = get_settings()

    # Dev-safe no-op: with no API key there is nothing to call.
    if not (settings.MISTRAL_API_KEY or "").strip():
        logger.warning("OCR: MISTRAL_API_KEY unset — skipping OCR extraction")
        return stats

    with track_stage(
        "ocr_extraction.run",
        conversation_id=str(conversation_id),
        agent_family="memory",
    ) as _span:
        try:
            await _run_inner(supabase, conversation_id, user_id, stats, settings)
        except Exception:  # noqa: BLE001
            # Belt-and-braces: the whole step is best-effort.
            logger.warning("OCR: extraction step failed", exc_info=True)

        try:
            _span.set(**{
                "candidates": stats.candidates,
                "extracted": stats.extracted,
                "skipped_unsupported": stats.skipped_unsupported,
                "skipped_too_large": stats.skipped_too_large,
                "failed": stats.failed,
                "empty": stats.empty,
                "pages_stored": stats.pages_stored,
                "partial": len(stats.partial),
                "no_budget": len(stats.no_budget),
                "budget_unavailable": stats.budget_unavailable,
            })
        except Exception:  # noqa: BLE001
            pass

        if stats.candidates:
            logger.info(
                "OCR: conversation %s — %d candidate(s): %d extracted, "
                "%d unsupported, %d too_large, %d failed, %d empty",
                conversation_id,
                stats.candidates,
                stats.extracted,
                stats.skipped_unsupported,
                stats.skipped_too_large,
                stats.failed,
                stats.empty,
            )
        return stats


async def _run_inner(
    supabase: SupabaseClient,
    conversation_id: str,
    user_id: str,
    stats: OcrExtractionStats,
    settings,
) -> list[str]:
    """Core loop — separated so ``run_ocr_extraction`` can wrap it defensively."""
    # 1. Load attachment rows for the conversation.
    rows = (
        supabase.table("workspace_items")
        .select("item_id, title, storage_path, metadata")
        .eq("conversation_id", conversation_id)
        .eq("kind", "attachment")
        .is_("deleted_at", "null")
        .execute()
    )
    # Candidates: never attempted (no ocr_status) AND either fully uploaded
    # (`upload_status == 'ready'`, set by the resumable-upload finalize flow)
    # OR uploaded via the legacy multipart route which doesn't write an
    # upload_status at all. Rows still in `uploading`/`failed`/`cancelled`
    # state are skipped — their bytes may not exist in storage yet, and
    # spending an OCR quota slot on a guaranteed-failure call is wasteful.
    def _is_uploadable(meta: dict) -> bool:
        if "ocr_status" in meta:
            return False
        us = meta.get("upload_status")
        # None = legacy multipart upload (always treat as ready).
        # 'ready' = resumable flow finalized successfully.
        return us is None or us == "ready"

    candidates = [
        r
        for r in (getattr(rows, "data", None) or [])
        if _is_uploadable(r.get("metadata") or {})
    ]
    stats.candidates = len(candidates)
    if not candidates:
        return []

    bucket = settings.STORAGE_BUCKET_DOCUMENTS

    # 2. Screen out what OCR can never read (marked, never retried).
    readable: list[dict] = []
    for row in candidates:
        item_id = row.get("item_id")
        metadata = row.get("metadata") or {}
        mime_type = metadata.get("mime_type")

        # --- Unsupported mime --------------------------------------------
        if mime_type not in _SUPPORTED_MIME_TYPES:
            stats.skipped_unsupported += 1
            _mark_status(supabase, item_id, metadata, "skipped_unsupported")
            continue

        # --- Size --------------------------------------------------------
        if _file_size(metadata) > OCR_MAX_FILE_BYTES:
            stats.skipped_too_large += 1
            _mark_status(supabase, item_id, metadata, "skipped_too_large")
            continue

        # --- Missing storage path (linked-from-case attachments have only a
        #     document_id; those are out of scope for this step) -----------
        if not row.get("storage_path"):
            stats.skipped_unsupported += 1
            _mark_status(supabase, item_id, metadata, "skipped_unsupported")
            continue

        readable.append(row)
    if not readable:
        return stats.filled_item_ids

    # 3. The page budget — one quota read, every file planned together (D5).
    #    Unreadable quota → read nothing and mark nothing: the files are picked
    #    up on the next turn rather than spending pages that cannot be counted.
    try:
        budget = await _read_budget(supabase, user_id)
    except Exception:  # noqa: BLE001
        logger.warning("OCR: quota read failed — files left for the next turn", exc_info=True)
        stats.budget_unavailable = True
        return stats.filled_item_ids
    plans = plan_files(
        [(row.get("item_id"), _page_count(row.get("metadata") or {})) for row in readable],
        budget,
    )

    for row, plan in zip(readable, plans):
        item_id = row.get("item_id")
        metadata = row.get("metadata") or {}
        storage_path = row.get("storage_path")
        mime_type = metadata.get("mime_type")
        name = _display_name(row)

        # --- No pages left: leave it for a later turn (no ocr_status) -------
        if plan.pages == ():
            stats.no_budget.append(name)
            continue

        # --- Signed URL --------------------------------------------------
        try:
            signed_url = get_signed_url(
                bucket, storage_path, expires_in=_SIGNED_URL_EXPIRY_S, supabase=supabase,
            )
        except Exception as exc:  # noqa: BLE001
            stats.failed += 1
            logger.warning("OCR: signed URL failed for item %s", item_id, exc_info=True)
            _mark_status(
                supabase, item_id, metadata, "failed", {"ocr_error": str(exc)},
            )
            continue

        # --- Mistral OCR call --------------------------------------------
        try:
            ocr_result = await _ocr(signed_url, mime_type, metadata, plan, item_id)
        except Exception as exc:  # noqa: BLE001
            stats.failed += 1
            logger.warning("OCR: Mistral call failed for item %s", item_id, exc_info=True)
            _mark_status(
                supabase, item_id, metadata, "failed", {"ocr_error": str(exc)},
            )
            continue

        if not (ocr_result.text or "").strip():
            stats.empty += 1
            _mark_status(supabase, item_id, metadata, "empty")
            continue

        # --- Partial read: mark the skipped pages in the text -------------
        partial_total = _partial_total(plan, ocr_result)
        read_pages = [p.page_no for p in ocr_result.pages]
        if partial_total is not False:
            text, anchored = assemble_partial(ocr_result.pages, partial_total)
            ocr_result = OcrDocumentResult(
                text=text, page_count=ocr_result.page_count,
                model=ocr_result.model, pages=anchored,
            )
            stats.partial.append(PartialRead(
                item_id=item_id, name=name, pages=tuple(read_pages),
                total_pages=partial_total,
            ))
        text = (ocr_result.text or "").strip()

        # --- Persist content + status -----------------------------------
        merged = dict(metadata)
        merged["ocr_status"] = "done"
        merged["ocr_pages"] = ocr_result.page_count
        merged["ocr_chars"] = len(text)
        if partial_total is not False:
            merged["ocr_partial"] = True
            merged["ocr_page_total"] = partial_total
            merged["ocr_page_ranges"] = [list(r) for r in page_ranges(read_pages)]
        try:
            (
                supabase.table("workspace_items")
                .update({"content_md": text, "metadata": merged})
                .eq("item_id", item_id)
                .execute()
            )
        except Exception:  # noqa: BLE001
            stats.failed += 1
            logger.warning(
                "OCR: failed to persist content_md for item %s", item_id, exc_info=True,
            )
            _mark_status(
                supabase, item_id, metadata, "failed",
                {"ocr_error": "persist content_md failed"},
            )
            continue

        # --- Page rows for the segmenter (flag on only; best-effort) -------
        if text == ocr_result.text and segmenter_enabled(user_id):
            stats.pages_stored += _store_pages(supabase, item_id, user_id, ocr_result.pages)

        # --- Cost/pages ledger (source of truth + OCR quota settle/count) -
        # OCR is a direct Mistral call (not a pydantic-ai agent), so the
        # tracking.py sink hook can't see it — feed the ledger explicitly.
        # Runs inside the handle_message capture scope; flushed + settled there.
        _ocr_pages = int(ocr_result.page_count or 0)
        record_call(
            agent="memory.ocr_extraction",
            model=ocr_result.model,
            agent_family="memory",
            subtype="ocr_extraction",
            pages_used=_ocr_pages,
            cost_usd=_ocr_pages / 1000.0,
        )

        stats.extracted += 1
        stats.filled_item_ids.append(item_id)

    return stats.filled_item_ids
