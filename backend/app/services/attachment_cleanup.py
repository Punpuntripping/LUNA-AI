"""Daily retention sweep for chat-attachment PDFs.

Purges the **file**, never the item. Once an attachment passes its retention
window the object is removed from Supabase Storage, ``storage_path`` goes NULL
and ``metadata.original_purged_at`` is stamped — but the ``workspace_items`` row
survives with its ``content_md`` intact. That column holds the OCR text written
by ``agents.memory.ocr_extractor``, and it is the *only* thing any agent ever
reads: the PDF itself is never sent to a model. Deleting the row therefore threw
away the useful half and kept nothing.

Why the row is load-bearing
---------------------------
``message_attachments.document_id`` references ``workspace_items(item_id)``
**ON DELETE CASCADE**. Hard-deleting the row also cut the message→upload link,
so one sweep destroyed at once: the extracted text, the ``WI-{seq}`` alias the
agents cite out loud, and the history tag built by
``agents.utils.history.build_user_attachment_tag`` (which is how "حلل العقد
المرفق" three turns later still resolves). A conversation lost every trace that
a document had ever been attached to it.

Measured on prod 2026-08-10, under the old behaviour: 46 successful extractions
ever, 24 still readable — and all 24 were images, which never matched the
PDF-only filter. Every PDF extraction ever made had been swept, unrecoverably.

Scope
-----
``kind='attachment'`` rows that own their file (``storage_path`` NOT NULL) and
whose file is a PDF (``metadata.mime_type == 'application/pdf'`` or
``storage_path`` ends in ``.pdf``). ``document_id``-linked pins are skipped
entirely — their bytes belong to the case library, not to the attachment.

Two windows, because purging a file whose text was never extracted destroys the
only path to that text:

* OCR has settled (``metadata.ocr_status`` present — ``done`` / ``empty`` /
  ``failed`` / ``skipped_*``) → :data:`RETENTION_HOURS`.
* OCR never ran (the upload was abandoned before its message was sent, so
  extraction never fired) → :data:`ABANDONED_RETENTION_HOURS`, a longer grace so
  a user returning to a draft still gets their extraction.

Idempotent by construction: a purged row has ``storage_path IS NULL`` and so
falls out of the query on every later pass.

Triggered once a day by the APScheduler job registered in
``backend.app.main``'s lifespan.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from supabase import Client as SupabaseClient

from shared.config import get_settings

logger = logging.getLogger(__name__)

# Retention window for an attachment whose OCR has settled — the text is
# already safe in content_md, so the original has done its job.
RETENTION_HOURS = 24

# Longer grace for an attachment that was never extracted. Purging its file
# would leave a row that can never gain text, so it keeps the original well past
# the point where a returning user might still send the message it rode on.
ABANDONED_RETENTION_HOURS = 24 * 7


def _is_pdf(row: dict) -> bool:
    """True when the attachment row's own file is a PDF."""
    meta = row.get("metadata") or {}
    if (meta.get("mime_type") or "").lower() == "application/pdf":
        return True
    return (row.get("storage_path") or "").lower().endswith(".pdf")


def _retention_hours(row: dict) -> int:
    """Retention window for one row — see the two-window rule in the module doc."""
    meta = row.get("metadata") or {}
    return RETENTION_HOURS if meta.get("ocr_status") else ABANDONED_RETENTION_HOURS


def _parse_ts(value) -> datetime | None:
    """Parse a PostgREST timestamptz. Returns None when unparseable.

    An unparseable ``created_at`` must never be read as "infinitely old" — the
    caller skips the row instead of purging on a guess.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def cleanup_old_pdf_attachments(supabase: SupabaseClient) -> dict[str, int]:
    """Purge the original file of PDF attachments past their retention window.

    Returns count stats ``{scanned, rows_purged, files_deleted, skipped_young}``.
    Never raises — failures are logged and partial counts returned, so a
    scheduler tick can never crash the app.

    Per-row ordering is **row UPDATE first, then storage DELETE**, and that is
    deliberate:

    * If the UPDATE fails, the delete is skipped and nothing is lost — the row
      keeps pointing at a file that still exists, and tomorrow's pass retries.
      This is what makes a deploy that lands ahead of migration 125 a harmless
      no-op instead of a data-loss event: the old ``workspace_content_shape``
      CHECK rejects a NULL ``storage_path``, so every UPDATE simply fails.
    * If the storage delete fails afterwards, one object is orphaned. That is
      logged with its path (recoverable by hand) and costs no user-visible data.

    The reverse order would delete the bytes first and only then discover it
    cannot record the fact — the one outcome with no way back.
    """
    now = datetime.now(timezone.utc)
    # Query on the SHORTEST window; the per-row window is applied below.
    cutoff = (now - timedelta(hours=RETENTION_HOURS)).isoformat()
    stats = {
        "scanned": 0,
        "rows_purged": 0,
        "files_deleted": 0,
        "skipped_young": 0,
    }

    try:
        result = (
            supabase.table("workspace_items")
            .select("item_id, storage_path, metadata, created_at")
            .eq("kind", "attachment")
            .lt("created_at", cutoff)
            .is_("deleted_at", "null")
            .not_.is_("storage_path", "null")  # skips pins AND already-purged rows
            .execute()
        )
        rows = result.data or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("attachment retention: query failed: %s", exc)
        return stats

    pdf_rows = [r for r in rows if _is_pdf(r)]
    stats["scanned"] = len(pdf_rows)
    if not pdf_rows:
        logger.info(
            "attachment retention: nothing older than %dh", RETENTION_HOURS
        )
        return stats

    bucket = get_settings().STORAGE_BUCKET_DOCUMENTS
    now_iso = now.isoformat()

    for row in pdf_rows:
        item_id = row["item_id"]
        path = row.get("storage_path")

        created_at = _parse_ts(row.get("created_at"))
        if created_at is None:
            stats["skipped_young"] += 1
            logger.warning(
                "attachment retention: unparseable created_at on %s — skipped",
                item_id,
            )
            continue
        age_hours = (now - created_at).total_seconds() / 3600.0
        if age_hours < _retention_hours(row):
            # Awaiting extraction — still inside the longer abandoned-upload grace.
            stats["skipped_young"] += 1
            continue

        # 1. Record the purge. content_md is untouched: the extracted text is
        #    what the item is FOR once its original is gone.
        merged = dict(row.get("metadata") or {})
        merged["original_purged_at"] = now_iso
        merged["original_purge_reason"] = "retention_sweep"
        try:
            (
                supabase.table("workspace_items")
                .update({"storage_path": None, "metadata": merged})
                .eq("item_id", item_id)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            # Nothing deleted yet — the file survives and tomorrow retries.
            logger.warning(
                "attachment retention: purge marker failed for %s (file kept): %s",
                item_id,
                exc,
            )
            continue
        stats["rows_purged"] += 1

        # 2. Now the bytes. An orphan here is logged, never silent.
        try:
            supabase.storage.from_(bucket).remove([path])
            stats["files_deleted"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "attachment retention: storage remove failed for %s "
                "(orphaned object at %s): %s",
                item_id,
                path,
                exc,
            )

    logger.info(
        "attachment retention: scanned=%d rows_purged=%d files_deleted=%d "
        "skipped_young=%d",
        stats["scanned"],
        stats["rows_purged"],
        stats["files_deleted"],
        stats["skipped_young"],
    )
    return stats


if __name__ == "__main__":
    # Manual run for testing:
    #   python -m backend.app.services.attachment_cleanup
    logging.basicConfig(level=logging.INFO)
    from shared.db.client import get_supabase_client

    print(cleanup_old_pdf_attachments(get_supabase_client()))


__all__ = [
    "cleanup_old_pdf_attachments",
    "RETENTION_HOURS",
    "ABANDONED_RETENTION_HOURS",
]
