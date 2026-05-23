"""
Shared upload-session helpers for the direct-to-Supabase resumable flow.

Both ``case_documents`` uploads and ``workspace_items`` (chat attachments) go
through the same three-phase protocol: ``init`` → browser TUS PATCHes →
``finalize``. This module owns the pure storage + validation pieces; row
lifecycle (insert + state transitions) lives in the calling service
(``document_service`` / ``workspace_service``).

Functions:
    * ``init_upload``         — validate input, reserve a storage path, return
                                ``{bucket, path, upload_url, expires_at}``.
                                Does NOT touch DB tables.
    * ``verify_finalize``     — HEAD the storage object, check declared size
                                matches, magic-byte-check the first 16 bytes.
                                Raises ``LunaHTTPException`` on any mismatch.
    * ``cancel_storage_object`` — best-effort ``delete_file``; never raises.

All error messages are Arabic. Imports use ``from backend.app.xxx`` prefix.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from supabase import Client as SupabaseClient

from backend.app.errors import ErrorCode, LunaHTTPException
from shared.config import get_settings
from shared.storage.client import (
    build_storage_path,
    delete_file,
    download_head_bytes,
    get_resumable_upload_url,
    head_object,
)

logger = logging.getLogger(__name__)


# ============================================
# CONSTANTS — kept in lock-step with migration 045 bucket policy
# ============================================

ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {"application/pdf", "image/png", "image/jpeg"}
)
MAX_FILE_SIZE: int = 50 * 1024 * 1024  # 50 MB

# Magic-byte prefixes for the three accepted MIME types.
MAGIC_BYTES: dict[str, bytes] = {
    "application/pdf": b"%PDF",
    "image/png": b"\x89PNG",
    "image/jpeg": b"\xff\xd8\xff",
}

# Client-side validity window for the returned ``upload_url``. Supabase TUS
# tokens are technically good for ~24 h; we narrow to 1 h so a hung browser
# can't dangle a half-finished upload indefinitely. The frontend re-inits if
# the user resumes after expiry.
UPLOAD_URL_TTL_SECONDS: int = 3600


# ============================================
# INIT — validate + reserve a storage path
# ============================================


def init_upload(
    supabase: SupabaseClient,
    *,
    user_id: str,
    case_id: Optional[str],
    conversation_id: Optional[str],
    filename: str,
    mime_type: str,
    size_bytes: int,
) -> dict:
    """Reserve a storage path for a resumable upload and return the contract
    the client needs.

    The caller is responsible for any DB row lifecycle (insert the placeholder
    row, persist ``storage_path``, etc.) — this function deliberately does NOT
    touch ``case_documents`` or ``workspace_items``.

    Args:
        supabase: Service-role Supabase client (RLS already bypassed).
        user_id: ``public.users.user_id`` of the caller (NOT auth_id).
        case_id: Case to scope the upload to. ``None`` for general/chat-only.
        conversation_id: Conversation to scope to. ``None`` for case-only.
        filename: User-supplied filename. Sanitised inside ``build_storage_path``.
        mime_type: Client-declared MIME type. Validated against
            ``ALLOWED_MIME_TYPES``.
        size_bytes: Client-declared size in bytes. Validated against
            ``MAX_FILE_SIZE``.

    Returns:
        ``{"bucket": str, "path": str, "upload_url": str,
           "expires_at": datetime, "filename": str, "mime_type": str,
           "size_bytes": int}``

    Raises:
        LunaHTTPException(400, DOC_INVALID_TYPE)
        LunaHTTPException(400, DOC_TOO_LARGE)
        LunaHTTPException(400, DOC_EMPTY)
    """
    # Defensive — Pydantic already enforces these, but services are public
    # surface for other callers (cron, internal webhooks) so duplicate cheaply.
    if mime_type not in ALLOWED_MIME_TYPES:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.DOC_INVALID_TYPE,
            detail="نوع الملف غير مسموح. الأنواع المسموحة: PDF, PNG, JPG",
        )
    if size_bytes <= 0:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.DOC_EMPTY,
            detail="الملف فارغ",
        )
    if size_bytes > MAX_FILE_SIZE:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.DOC_TOO_LARGE,
            detail="حجم الملف يتجاوز الحد الأقصى (50 ميغابايت)",
        )

    settings = get_settings()
    bucket = settings.STORAGE_BUCKET_DOCUMENTS

    storage_path = build_storage_path(
        case_id=case_id,
        user_id=user_id,
        conversation_id=conversation_id,
        filename=filename,
    )

    upload_url = get_resumable_upload_url(settings.SUPABASE_URL)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=UPLOAD_URL_TTL_SECONDS)

    logger.info(
        "upload.init bucket=%s path=%s size=%d mime=%s",
        bucket,
        storage_path,
        size_bytes,
        mime_type,
    )

    return {
        "bucket": bucket,
        "path": storage_path,
        "upload_url": upload_url,
        "expires_at": expires_at,
        "filename": filename,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
    }


# ============================================
# FINALIZE — confirm the bytes actually landed
# ============================================


def verify_finalize(
    supabase: SupabaseClient,
    *,
    bucket: str,
    storage_path: str,
    expected_size: int,
    expected_mime: str,
) -> None:
    """Confirm a resumable upload completed and the bytes match what the
    client declared at init time.

    Three checks, in order:
        1. ``head_object`` — the object exists in storage.
        2. Reported size matches the size the client declared at init.
        3. First 16 bytes match the magic prefix for ``expected_mime``.

    Args:
        supabase: Service-role client used for the storage REST call.
        bucket: Bucket name (typically ``documents``).
        storage_path: Object key inside the bucket.
        expected_size: Bytes the client said the file would be at init.
        expected_mime: Declared MIME type — used to pick the magic prefix.

    Raises:
        LunaHTTPException(409, UPLOAD_NOT_COMPLETE):
            Object missing — client should retry the TUS upload.
        LunaHTTPException(409, UPLOAD_SIZE_MISMATCH):
            Object exists but its size differs from the declared size.
        LunaHTTPException(400, DOC_MAGIC_MISMATCH):
            File header doesn't match its declared MIME — likely a tampered
            client or a renamed extension.
    """
    info = head_object(bucket, storage_path, supabase=supabase)
    if info is None:
        logger.info(
            "upload.finalize: object missing — %s/%s", bucket, storage_path
        )
        raise LunaHTTPException(
            status_code=409,
            code=ErrorCode.UPLOAD_NOT_COMPLETE,
            detail="لم يكتمل رفع الملف بعد",
        )

    actual_size = info.get("size")
    if actual_size != expected_size:
        logger.warning(
            "upload.finalize: size mismatch %s/%s — expected=%s actual=%s",
            bucket,
            storage_path,
            expected_size,
            actual_size,
        )
        raise LunaHTTPException(
            status_code=409,
            code=ErrorCode.UPLOAD_SIZE_MISMATCH,
            detail="حجم الملف المرفوع لا يطابق الحجم المُعلن",
        )

    expected_magic = MAGIC_BYTES.get(expected_mime)
    if expected_magic:
        try:
            head = download_head_bytes(
                bucket, storage_path, n=len(expected_magic), supabase=supabase
            )
        except Exception as e:
            # Storage said the object exists but we can't read it — surface as
            # UPLOAD_NOT_COMPLETE so the client retries rather than wedging the
            # row in an indeterminate state.
            logger.warning(
                "upload.finalize: head-byte fetch failed %s/%s: %s",
                bucket,
                storage_path,
                e,
            )
            raise LunaHTTPException(
                status_code=409,
                code=ErrorCode.UPLOAD_NOT_COMPLETE,
                detail="لم يكتمل رفع الملف بعد",
            )

        if not head.startswith(expected_magic):
            logger.warning(
                "upload.finalize: magic mismatch %s/%s — expected=%r got=%r",
                bucket,
                storage_path,
                expected_magic,
                head,
            )
            raise LunaHTTPException(
                status_code=400,
                code=ErrorCode.DOC_MAGIC_MISMATCH,
                detail="محتوى الملف لا يتطابق مع نوعه المعلن",
            )

    logger.info(
        "upload.finalize.ok bucket=%s path=%s size=%d", bucket, storage_path, actual_size
    )


# ============================================
# CANCEL — best-effort cleanup
# ============================================


def cancel_storage_object(
    supabase: SupabaseClient,
    *,
    bucket: str,
    storage_path: Optional[str],
) -> bool:
    """Best-effort delete of a storage object during cancel.

    Never raises. Returns ``True`` on success or when there is nothing to
    delete. Callers should always proceed with their DB-side soft delete
    regardless of the return value — the daily reconciler is the safety net
    for any object the cancel call missed.
    """
    if not storage_path:
        return True
    try:
        return delete_file(bucket, storage_path, supabase=supabase)
    except Exception as e:  # noqa: BLE001 — best effort
        logger.warning(
            "upload.cancel: storage delete failed %s/%s: %s",
            bucket,
            storage_path,
            e,
        )
        return False


__all__ = [
    "ALLOWED_MIME_TYPES",
    "MAX_FILE_SIZE",
    "MAGIC_BYTES",
    "UPLOAD_URL_TTL_SECONDS",
    "init_upload",
    "verify_finalize",
    "cancel_storage_object",
]
