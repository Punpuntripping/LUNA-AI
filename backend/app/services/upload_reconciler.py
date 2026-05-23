"""Daily reconciliation sweep for stuck resumable uploads.

The init/finalize/cancel protocol leaves a window where a browser can crash
or be closed between phases 1 and 3 — the placeholder row sits in the DB
with ``upload_status='uploading'`` but no client will ever call ``/finalize``
or ``/cancel`` for it. This reconciler is the safety net.

Scope: both ``case_documents`` (status in ``extracted_data->>'upload_status'``)
and ``workspace_items`` with ``kind='attachment'`` (status in
``metadata->>'upload_status'``).

For each row stuck > ``STUCK_HOURS``:

  1. HEAD the storage object via ``upload_session_service.verify_finalize``.
  2. If the object is present and matches the declared size + magic bytes,
     promote the row to ``ready`` (auto-recovery — the user did finish the
     upload, only the finalize round-trip was lost).
  3. Otherwise soft-delete the row via the appropriate ``cancel_…`` helper,
     which also best-effort wipes the partial storage object.

Triggered once a day by the APScheduler job registered in
``backend.app.main``'s lifespan. Never raises; per-row isolation means a
single failure cannot abort the rest of the sweep.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from supabase import Client as SupabaseClient

from backend.app.errors import LunaHTTPException
from backend.app.services import (
    document_service,
    upload_session_service,
    workspace_service,
)
from shared.config import get_settings
from shared.observability import get_logfire

logger = logging.getLogger(__name__)
_logfire = get_logfire()

# Rows older than this with ``upload_status='uploading'`` are considered
# stuck. 24 h is generous — even slow-3G uploads of a 50 MB cap file finish
# well inside an hour.
STUCK_HOURS = 24


# ---------------------------------------------------------------------------
# Helpers — promote a stuck row to ready, or cancel it
# ---------------------------------------------------------------------------


def _auth_id_for_user(supabase: SupabaseClient, user_id: str) -> Optional[str]:
    """Translate ``public.users.user_id`` → ``auth_id`` for the cancel/finalize
    service calls (which take ``auth_id`` and re-derive ``user_id`` internally).

    Returns ``None`` if the user is missing — in which case the row is
    orphaned and the caller should fall back to a direct soft-delete.
    """
    try:
        res = (
            supabase.table("users")
            .select("auth_id")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("reconciler: auth_id lookup failed for %s: %s", user_id, exc)
        return None
    if res is None or res.data is None:
        return None
    return res.data.get("auth_id")


def _direct_soft_delete_document(
    supabase: SupabaseClient, document_id: str, storage_path: Optional[str]
) -> None:
    """Soft-delete a case_documents row without going through the auth path.

    Used when the owning user lookup fails or the service finalize/cancel
    raises; the reconciler still wants the row out of the ``uploading`` set
    so the next sweep doesn't re-pick it.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        row = (
            supabase.table("case_documents")
            .select("extracted_data")
            .eq("document_id", document_id)
            .maybe_single()
            .execute()
        )
        current = (row.data or {}) if row is not None else {}
    except Exception:
        current = {}
    extracted = dict(current.get("extracted_data") or {})
    extracted["upload_status"] = "cancelled"
    extracted["upload_cancelled_at"] = now_iso
    extracted["cancelled_by"] = "reconciler"
    try:
        (
            supabase.table("case_documents")
            .update(
                {
                    "deleted_at": now_iso,
                    "updated_at": now_iso,
                    "extracted_data": extracted,
                }
            )
            .eq("document_id", document_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "reconciler: direct soft-delete failed for document %s: %s",
            document_id,
            exc,
        )
        return

    if storage_path:
        settings = get_settings()
        upload_session_service.cancel_storage_object(
            supabase,
            bucket=settings.STORAGE_BUCKET_DOCUMENTS,
            storage_path=storage_path,
        )


def _direct_soft_delete_attachment(
    supabase: SupabaseClient, item_id: str, storage_path: Optional[str]
) -> None:
    """Mirror of ``_direct_soft_delete_document`` for workspace_items."""
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        row = (
            supabase.table("workspace_items")
            .select("metadata")
            .eq("item_id", item_id)
            .maybe_single()
            .execute()
        )
        current = (row.data or {}) if row is not None else {}
    except Exception:
        current = {}
    meta = dict(current.get("metadata") or {})
    meta["upload_status"] = "cancelled"
    meta["upload_cancelled_at"] = now_iso
    meta["cancelled_by"] = "reconciler"
    try:
        (
            supabase.table("workspace_items")
            .update(
                {
                    "deleted_at": now_iso,
                    "updated_at": now_iso,
                    "metadata": meta,
                }
            )
            .eq("item_id", item_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "reconciler: direct soft-delete failed for item %s: %s", item_id, exc
        )
        return

    if storage_path:
        settings = get_settings()
        upload_session_service.cancel_storage_object(
            supabase,
            bucket=settings.STORAGE_BUCKET_DOCUMENTS,
            storage_path=storage_path,
        )


# ---------------------------------------------------------------------------
# Per-row reconciliation
# ---------------------------------------------------------------------------


def _reconcile_document(
    supabase: SupabaseClient, row: dict, stats: dict
) -> None:
    document_id = row.get("document_id")
    storage_path = row.get("storage_path")
    extracted = row.get("extracted_data") or {}
    expected_size = extracted.get("declared_size_bytes") or row.get(
        "file_size_bytes"
    )
    expected_mime = extracted.get("declared_mime_type") or row.get("mime_type")
    settings = get_settings()
    bucket = settings.STORAGE_BUCKET_DOCUMENTS

    # 1. Try to verify the object — if good, promote via the service.
    try:
        upload_session_service.verify_finalize(
            supabase,
            bucket=bucket,
            storage_path=storage_path,
            expected_size=int(expected_size or 0),
            expected_mime=expected_mime or "application/octet-stream",
        )
    except LunaHTTPException as exc:
        logger.info(
            "reconciler: document %s verify failed (%s) — cancelling",
            document_id,
            getattr(exc, "code", "unknown"),
        )
        case_row = row.get("lawyer_cases") or {}
        user_id = case_row.get("lawyer_user_id")
        auth_id = _auth_id_for_user(supabase, user_id) if user_id else None
        if auth_id:
            try:
                document_service.cancel_document_upload(
                    supabase, auth_id, document_id
                )
                stats["deleted"] += 1
                return
            except Exception as exc2:  # noqa: BLE001
                logger.warning(
                    "reconciler: document_service.cancel failed for %s: %s",
                    document_id,
                    exc2,
                )
        _direct_soft_delete_document(supabase, document_id, storage_path)
        stats["deleted"] += 1
        return
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "reconciler: unexpected verify error for document %s: %s",
            document_id,
            exc,
        )
        _direct_soft_delete_document(supabase, document_id, storage_path)
        stats["deleted"] += 1
        return

    # 2. Verify passed — promote via service.
    case_row = row.get("lawyer_cases") or {}
    user_id = case_row.get("lawyer_user_id")
    auth_id = _auth_id_for_user(supabase, user_id) if user_id else None
    if not auth_id:
        # Object is good but we can't run the service path — flip the flag
        # in-place rather than dropping the user's file.
        now_iso = datetime.now(timezone.utc).isoformat()
        extracted = dict(row.get("extracted_data") or {})
        extracted["upload_status"] = "ready"
        extracted["upload_finalized_at"] = now_iso
        extracted["finalized_by"] = "reconciler"
        try:
            (
                supabase.table("case_documents")
                .update({"extracted_data": extracted, "updated_at": now_iso})
                .eq("document_id", document_id)
                .execute()
            )
            stats["recovered"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "reconciler: in-place promote failed for %s: %s",
                document_id,
                exc,
            )
        return

    try:
        document_service.finalize_document_upload(
            supabase, auth_id, document_id
        )
        stats["recovered"] += 1
        logger.info("reconciler: recovered document %s", document_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "reconciler: finalize service failed for %s: %s — cancelling",
            document_id,
            exc,
        )
        _direct_soft_delete_document(supabase, document_id, storage_path)
        stats["deleted"] += 1


def _reconcile_attachment(
    supabase: SupabaseClient, row: dict, stats: dict
) -> None:
    item_id = row.get("item_id")
    storage_path = row.get("storage_path")
    metadata = row.get("metadata") or {}
    expected_size = metadata.get("declared_size_bytes") or metadata.get(
        "file_size_bytes"
    )
    expected_mime = metadata.get("declared_mime_type") or metadata.get(
        "mime_type"
    )
    settings = get_settings()
    bucket = settings.STORAGE_BUCKET_DOCUMENTS
    user_id = row.get("user_id")

    try:
        upload_session_service.verify_finalize(
            supabase,
            bucket=bucket,
            storage_path=storage_path,
            expected_size=int(expected_size or 0),
            expected_mime=expected_mime or "application/octet-stream",
        )
    except LunaHTTPException as exc:
        logger.info(
            "reconciler: attachment %s verify failed (%s) — cancelling",
            item_id,
            getattr(exc, "code", "unknown"),
        )
        auth_id = _auth_id_for_user(supabase, user_id) if user_id else None
        if auth_id:
            try:
                workspace_service.cancel_attachment_upload(
                    supabase, auth_id, item_id
                )
                stats["deleted"] += 1
                return
            except Exception as exc2:  # noqa: BLE001
                logger.warning(
                    "reconciler: workspace_service.cancel failed for %s: %s",
                    item_id,
                    exc2,
                )
        _direct_soft_delete_attachment(supabase, item_id, storage_path)
        stats["deleted"] += 1
        return
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "reconciler: unexpected verify error for attachment %s: %s",
            item_id,
            exc,
        )
        _direct_soft_delete_attachment(supabase, item_id, storage_path)
        stats["deleted"] += 1
        return

    auth_id = _auth_id_for_user(supabase, user_id) if user_id else None
    if not auth_id:
        now_iso = datetime.now(timezone.utc).isoformat()
        meta = dict(row.get("metadata") or {})
        meta["upload_status"] = "ready"
        meta["upload_finalized_at"] = now_iso
        meta["finalized_by"] = "reconciler"
        try:
            (
                supabase.table("workspace_items")
                .update({"metadata": meta, "updated_at": now_iso})
                .eq("item_id", item_id)
                .execute()
            )
            stats["recovered"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "reconciler: in-place promote failed for %s: %s",
                item_id,
                exc,
            )
        return

    try:
        workspace_service.finalize_attachment_upload(
            supabase, auth_id, item_id
        )
        stats["recovered"] += 1
        logger.info("reconciler: recovered attachment %s", item_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "reconciler: finalize service failed for %s: %s — cancelling",
            item_id,
            exc,
        )
        _direct_soft_delete_attachment(supabase, item_id, storage_path)
        stats["deleted"] += 1


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def reconcile_stuck_uploads(supabase: SupabaseClient) -> dict[str, int]:
    """Sweep rows stuck in ``upload_status='uploading'`` > ``STUCK_HOURS``.

    Returns ``{"scanned": int, "recovered": int, "deleted": int}``. Never
    raises — per-row isolation plus a top-level swallow at each query keeps
    the scheduler tick alive even if a single row blows up.

    The status filter is applied in Python because the JSON-path filter
    (``->>'upload_status'``) is awkward through ``supabase-py``'s fluent
    query builder and the older-than-cutoff filter already narrows the set
    drastically.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=STUCK_HOURS)
    ).isoformat()
    stats = {"scanned": 0, "recovered": 0, "deleted": 0}

    # --- case_documents ---------------------------------------------------
    doc_rows: list[dict] = []
    try:
        result = (
            supabase.table("case_documents")
            .select(
                "document_id, storage_path, mime_type, file_size_bytes, "
                "extracted_data, created_at, "
                "lawyer_cases!inner(lawyer_user_id)"
            )
            .lt("created_at", cutoff)
            .is_("deleted_at", "null")
            .execute()
        )
        doc_rows = result.data or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("reconciler: case_documents query failed: %s", exc)

    for row in doc_rows:
        extracted = row.get("extracted_data") or {}
        if extracted.get("upload_status") != "uploading":
            continue
        stats["scanned"] += 1
        try:
            _reconcile_document(supabase, row, stats)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "reconciler: row failed for document %s: %s",
                row.get("document_id"),
                exc,
            )

    # --- workspace_items (attachments) ------------------------------------
    att_rows: list[dict] = []
    try:
        result = (
            supabase.table("workspace_items")
            .select(
                "item_id, user_id, conversation_id, storage_path, "
                "metadata, created_at"
            )
            .eq("kind", "attachment")
            .lt("created_at", cutoff)
            .is_("deleted_at", "null")
            .execute()
        )
        att_rows = result.data or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("reconciler: workspace_items query failed: %s", exc)

    for row in att_rows:
        metadata = row.get("metadata") or {}
        if metadata.get("upload_status") != "uploading":
            continue
        stats["scanned"] += 1
        try:
            _reconcile_attachment(supabase, row, stats)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "reconciler: row failed for attachment %s: %s",
                row.get("item_id"),
                exc,
            )

    logger.info(
        "reconciler: scanned=%d recovered=%d deleted=%d",
        stats["scanned"],
        stats["recovered"],
        stats["deleted"],
    )

    # Top-level span captured at the end so the attributes carry final counts
    # rather than zeros taken at entry. Logfire is happy with zero-duration
    # spans; this is purely a telemetry breadcrumb.
    with _logfire.span(
        "upload.reconcile",
        scanned=stats["scanned"],
        recovered=stats["recovered"],
        deleted=stats["deleted"],
    ):
        pass

    return stats


__all__ = ["reconcile_stuck_uploads", "STUCK_HOURS"]


if __name__ == "__main__":
    # Manual run for testing:
    #   python -m backend.app.services.upload_reconciler
    logging.basicConfig(level=logging.INFO)
    from shared.db.client import get_supabase_client

    print(reconcile_stuck_uploads(get_supabase_client()))
