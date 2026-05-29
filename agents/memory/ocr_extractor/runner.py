"""Entry point for the OCR extraction step.

``run_ocr_extraction`` runs once per turn (before the router): it finds
un-extracted PDF/image attachments on the conversation, OCRs each via Mistral,
and writes the text back into the attachment's own ``workspace_items.content_md``.

Best-effort by contract — it must **never raise**. Every per-candidate failure
is caught, logged, and recorded on the row as an ``ocr_status`` marker so the
file is never re-attempted. The function returns whatever succeeded.
"""
from __future__ import annotations

import logging

from supabase import Client as SupabaseClient

from agents.runs import AgentRunRecord, record_agent_run
from agents.utils.tracking import track_stage
from shared.config import get_settings
from shared.observability import get_logfire
from shared.storage.client import get_signed_url

from .mistral_ocr import OCR_MAX_PAGES, ocr_document
from .models import OcrExtractionStats

logger = logging.getLogger(__name__)
_logfire = get_logfire()

# ---------------------------------------------------------------------------
# Module constants (see .claude/plans/ocr_extraction_agent.md)
# ---------------------------------------------------------------------------
OCR_MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB — over this → skipped_too_large
OCR_USER_QUOTA = 100  # lifetime successful OCR runs per user
# OCR_MAX_PAGES is owned by mistral_ocr; re-exported for callers/tests.
__all__ = ["run_ocr_extraction", "OCR_MAX_FILE_BYTES", "OCR_MAX_PAGES", "OCR_USER_QUOTA"]

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


def _count_prior_ocr_runs(supabase: SupabaseClient, user_id: str) -> int:
    """Lifetime count of this user's successful OCR ``agent_runs`` rows.

    On query failure, returns 0 (fail-open: a transient telemetry-table error
    must not block document extraction).
    """
    try:
        result = (
            supabase.table("agent_runs")
            .select("run_id", count="exact")
            .eq("user_id", user_id)
            .eq("agent_family", "memory")
            .eq("subtype", "ocr_extraction")
            .eq("status", "ok")
            .execute()
        )
        count = getattr(result, "count", None)
        if count is not None:
            return int(count)
        return len(getattr(result, "data", None) or [])
    except Exception:  # noqa: BLE001
        logger.warning("OCR: quota count query failed — treating as 0", exc_info=True)
        return 0


async def run_ocr_extraction(
    supabase: SupabaseClient,
    conversation_id: str,
    user_id: str,
) -> list[str]:
    """Extract text from new PDF/image attachments on a conversation.

    Loads ``kind='attachment'`` workspace items for the conversation, OCRs each
    one that has never been attempted (no ``metadata.ocr_status``), writes the
    text into the row's ``content_md``, and records an ``agent_runs`` row per
    successful extraction.

    Args:
        supabase: A (sync) Supabase client — the established pattern is to use
            it inside async functions.
        conversation_id: The conversation whose attachments to scan.
        user_id: The ``users.user_id`` of the conversation owner (drives quota).

    Returns:
        The list of attachment ``item_id``s whose ``content_md`` was filled this
        run. Empty list on any global failure (dev-safe no-op).

    Never raises — every failure path is caught and logged.
    """
    stats = OcrExtractionStats()
    settings = get_settings()

    # Dev-safe no-op: with no API key there is nothing to call.
    if not (settings.MISTRAL_API_KEY or "").strip():
        logger.warning("OCR: MISTRAL_API_KEY unset — skipping OCR extraction")
        return []

    with track_stage(
        "ocr_extraction.run",
        conversation_id=str(conversation_id),
        agent_family="memory",
    ) as _span:
        try:
            filled = await _run_inner(supabase, conversation_id, user_id, stats, settings)
        except Exception:  # noqa: BLE001
            # Belt-and-braces: the whole step is best-effort.
            logger.warning("OCR: extraction step failed", exc_info=True)
            filled = stats.filled_item_ids

        try:
            _span.set(**{
                "candidates": stats.candidates,
                "extracted": stats.extracted,
                "skipped_unsupported": stats.skipped_unsupported,
                "skipped_quota": stats.skipped_quota,
                "skipped_too_large": stats.skipped_too_large,
                "failed": stats.failed,
                "empty": stats.empty,
            })
        except Exception:  # noqa: BLE001
            pass

        if stats.candidates:
            logger.info(
                "OCR: conversation %s — %d candidate(s): %d extracted, "
                "%d unsupported, %d quota, %d too_large, %d failed, %d empty",
                conversation_id,
                stats.candidates,
                stats.extracted,
                stats.skipped_unsupported,
                stats.skipped_quota,
                stats.skipped_too_large,
                stats.failed,
                stats.empty,
            )
        return filled


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
        .select("item_id, storage_path, metadata")
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

    for row in candidates:
        item_id = row.get("item_id")
        metadata = row.get("metadata") or {}
        storage_path = row.get("storage_path")
        mime_type = metadata.get("mime_type")

        # --- Unsupported mime --------------------------------------------
        if mime_type not in _SUPPORTED_MIME_TYPES:
            stats.skipped_unsupported += 1
            _mark_status(supabase, item_id, metadata, "skipped_unsupported")
            continue

        # --- Lifetime quota ----------------------------------------------
        if _count_prior_ocr_runs(supabase, user_id) >= OCR_USER_QUOTA:
            stats.skipped_quota += 1
            _mark_status(supabase, item_id, metadata, "skipped_quota")
            continue

        # --- Size --------------------------------------------------------
        file_size = metadata.get("file_size_bytes") or 0
        try:
            file_size = int(file_size)
        except (TypeError, ValueError):
            file_size = 0
        if file_size > OCR_MAX_FILE_BYTES:
            stats.skipped_too_large += 1
            _mark_status(supabase, item_id, metadata, "skipped_too_large")
            continue

        # --- Missing storage path (linked-from-case attachments have only a
        #     document_id; those are out of scope for this step) -----------
        if not storage_path:
            stats.skipped_unsupported += 1
            _mark_status(supabase, item_id, metadata, "skipped_unsupported")
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
            ocr_result = await ocr_document(signed_url, mime_type)
        except Exception as exc:  # noqa: BLE001
            stats.failed += 1
            logger.warning("OCR: Mistral call failed for item %s", item_id, exc_info=True)
            _mark_status(
                supabase, item_id, metadata, "failed", {"ocr_error": str(exc)},
            )
            continue

        text = (ocr_result.text or "").strip()
        if not text:
            stats.empty += 1
            _mark_status(supabase, item_id, metadata, "empty")
            continue

        # --- Persist content + status -----------------------------------
        merged = dict(metadata)
        merged["ocr_status"] = "done"
        merged["ocr_pages"] = ocr_result.page_count
        merged["ocr_chars"] = len(text)
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

        # --- Record agent_runs row (telemetry — never blocking) ----------
        try:
            record_agent_run(
                supabase,
                AgentRunRecord(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    agent_family="memory",
                    subtype="ocr_extraction",
                    output_item_id=item_id,
                    model_used=ocr_result.model,
                    per_phase_stats={"pages": ocr_result.page_count},
                    cost_usd=ocr_result.page_count / 1000.0,
                    status="ok",
                ),
            )
        except Exception:  # noqa: BLE001
            logger.warning("OCR: agent_runs record failed for item %s", item_id, exc_info=True)

        stats.extracted += 1
        stats.filled_item_ids.append(item_id)

    return stats.filled_item_ids
