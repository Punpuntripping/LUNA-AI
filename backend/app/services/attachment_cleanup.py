"""Daily cleanup sweep for old PDF attachments.

Hard-deletes conversation-workspace PDF attachments older than the retention
window — both the file in Supabase Storage AND the ``workspace_items`` row.
Triggered once a day by the APScheduler job registered in
``backend.app.main``'s lifespan.

Scope: ``workspace_items`` rows with ``kind='attachment'`` whose own file is a
PDF (``metadata.mime_type == 'application/pdf'`` or ``storage_path`` ends in
``.pdf``). ``document_id``-linked pins (which share a case_documents file) get
their row removed but the underlying case file is left alone — it belongs to
the case library, not the attachment.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from supabase import Client as SupabaseClient

from shared.config import get_settings

logger = logging.getLogger(__name__)

# Retention window — attachments older than this are swept.
RETENTION_HOURS = 24


def _is_pdf(row: dict) -> bool:
    """True when the attachment row's own file is a PDF."""
    meta = row.get("metadata") or {}
    if (meta.get("mime_type") or "").lower() == "application/pdf":
        return True
    return (row.get("storage_path") or "").lower().endswith(".pdf")


def cleanup_old_pdf_attachments(supabase: SupabaseClient) -> dict[str, int]:
    """Hard-delete PDF attachments older than ``RETENTION_HOURS``.

    Returns count stats ``{scanned, rows_deleted, files_deleted}``. Never
    raises — failures are logged and partial counts returned, so a scheduler
    tick can never crash the app.

    Each row is deleted individually: the row first, then its storage file.
    Row-first ordering means a failure can never leave a ``workspace_items``
    row pointing at a missing file, and per-row isolation means one FK
    conflict or missing object does not abort the rest of the sweep.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=RETENTION_HOURS)
    ).isoformat()
    stats = {"scanned": 0, "rows_deleted": 0, "files_deleted": 0}

    try:
        result = (
            supabase.table("workspace_items")
            .select("item_id, storage_path, document_id, metadata, created_at")
            .eq("kind", "attachment")
            .lt("created_at", cutoff)
            .is_("deleted_at", "null")
            .execute()
        )
        rows = result.data or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("pdf cleanup: query failed: %s", exc)
        return stats

    pdf_rows = [r for r in rows if _is_pdf(r)]
    stats["scanned"] = len(pdf_rows)
    if not pdf_rows:
        logger.info("pdf cleanup: nothing older than %dh", RETENTION_HOURS)
        return stats

    bucket = get_settings().STORAGE_BUCKET_DOCUMENTS

    for row in pdf_rows:
        item_id = row["item_id"]
        try:
            supabase.table("workspace_items").delete().eq(
                "item_id", item_id
            ).execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "pdf cleanup: row delete failed for %s: %s", item_id, exc
            )
            continue
        stats["rows_deleted"] += 1

        path = row.get("storage_path")
        if not path:
            continue  # document_id-linked pin — no own file to remove.
        try:
            supabase.storage.from_(bucket).remove([path])
            stats["files_deleted"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "pdf cleanup: storage remove failed for %s: %s", path, exc
            )

    logger.info(
        "pdf cleanup: scanned=%d rows_deleted=%d files_deleted=%d",
        stats["scanned"],
        stats["rows_deleted"],
        stats["files_deleted"],
    )
    return stats


if __name__ == "__main__":
    # Manual run for testing:
    #   python -m backend.app.services.attachment_cleanup
    logging.basicConfig(level=logging.INFO)
    from shared.db.client import get_supabase_client

    print(cleanup_old_pdf_attachments(get_supabase_client()))


__all__ = ["cleanup_old_pdf_attachments", "RETENTION_HOURS"]
