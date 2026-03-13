"""
Document business logic.
Upload to Supabase Storage, DB record management, download URLs.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, UploadFile
from supabase import Client as SupabaseClient

from backend.app.errors import LunaHTTPException, ErrorCode
from backend.app.services.audit_service import write_audit_log
from backend.app.services.case_service import get_user_id
from shared.config import get_settings
from shared.storage.client import (
    upload_file,
    get_signed_url,
    delete_file,
    build_storage_path,
)

logger = logging.getLogger(__name__)

_ALLOWED_MIME_TYPES = {"application/pdf", "image/png", "image/jpeg"}
_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

# Magic bytes for server-side file type validation
_MAGIC_BYTES = {
    "application/pdf": b"%PDF",
    "image/png": b"\x89PNG",
    "image/jpeg": b"\xff\xd8\xff",
}


def _verify_case_ownership(supabase: SupabaseClient, case_id: str, user_id: str) -> None:
    """Verify case exists and belongs to user."""
    try:
        result = (
            supabase.table("lawyer_cases")
            .select("case_id")
            .eq("case_id", case_id)
            .eq("lawyer_user_id", user_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logger.exception("Error verifying case ownership: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ داخلي")

    if result is None or result.data is None:
        raise LunaHTTPException(status_code=404, code=ErrorCode.CASE_NOT_FOUND, detail="القضية غير موجودة")


def _verify_document_ownership(supabase: SupabaseClient, document_id: str, user_id: str) -> dict:
    """Verify document exists and belongs to user's case. Returns document row."""
    try:
        result = (
            supabase.table("case_documents")
            .select("*, lawyer_cases!inner(lawyer_user_id)")
            .eq("document_id", document_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logger.exception("Error verifying document ownership: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ داخلي")

    if result is None or result.data is None:
        raise LunaHTTPException(status_code=404, code=ErrorCode.DOC_NOT_FOUND, detail="المستند غير موجود")

    if result.data.get("lawyer_cases", {}).get("lawyer_user_id") != user_id:
        raise LunaHTTPException(status_code=404, code=ErrorCode.DOC_NOT_FOUND, detail="المستند غير موجود")

    return result.data


def list_documents(
    supabase: SupabaseClient,
    auth_id: str,
    case_id: str,
    *,
    page: int = 1,
    limit: int = 20,
) -> dict:
    """List documents for a case with pagination."""
    user_id = get_user_id(supabase, auth_id)
    _verify_case_ownership(supabase, case_id, user_id)

    page = max(1, page)
    limit = max(1, min(limit, 100))
    offset = (page - 1) * limit

    try:
        result = (
            supabase.table("case_documents")
            .select("*", count="exact")
            .eq("case_id", case_id)
            .is_("deleted_at", "null")
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )
    except Exception as e:
        logger.exception("Error listing documents: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء جلب المستندات")

    return {
        "documents": result.data or [],
        "total": result.count or 0,
    }


def upload_document(
    supabase: SupabaseClient,
    auth_id: str,
    case_id: str,
    *,
    file: UploadFile,
    conversation_id: Optional[str] = None,
) -> dict:
    """Upload a document to storage and create a DB record."""
    user_id = get_user_id(supabase, auth_id)
    _verify_case_ownership(supabase, case_id, user_id)

    # Validate MIME type from header
    content_type = file.content_type or "application/octet-stream"
    if content_type not in _ALLOWED_MIME_TYPES:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.DOC_INVALID_TYPE,
            detail="نوع الملف غير مسموح. الأنواع المسموحة: PDF, PNG, JPG",
        )

    # Check file size before reading full content (if available)
    if hasattr(file, "size") and file.size and file.size > _MAX_FILE_SIZE:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.DOC_TOO_LARGE,
            detail="حجم الملف يتجاوز الحد الأقصى (50 ميغابايت)",
        )

    # Read file bytes with chunked size guard
    chunks = []
    total = 0
    chunk_size = 1024 * 1024  # 1MB chunks
    while True:
        chunk = file.file.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_FILE_SIZE:
            raise LunaHTTPException(
                status_code=400,
                code=ErrorCode.DOC_TOO_LARGE,
                detail="حجم الملف يتجاوز الحد الأقصى (50 ميغابايت)",
            )
        chunks.append(chunk)
    file_bytes = b"".join(chunks)
    file_size = total

    if file_size == 0:
        raise LunaHTTPException(status_code=400, code=ErrorCode.DOC_EMPTY, detail="الملف فارغ")

    # Server-side magic-byte validation
    expected_magic = _MAGIC_BYTES.get(content_type)
    if expected_magic and not file_bytes[:len(expected_magic)].startswith(expected_magic):
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.DOC_MAGIC_MISMATCH,
            detail="محتوى الملف لا يتطابق مع نوعه المعلن",
        )

    # Build storage path and upload
    settings = get_settings()
    bucket = settings.STORAGE_BUCKET_DOCUMENTS
    filename = file.filename or "document"
    storage_path = build_storage_path(case_id, user_id, conversation_id, filename)

    try:
        upload_file(bucket, storage_path, file_bytes, content_type, supabase=supabase)
    except (HTTPException, LunaHTTPException):
        raise
    except Exception as e:
        logger.exception("Storage upload failed: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.DOC_UPLOAD_FAILED, detail="حدث خطأ أثناء رفع الملف")

    # Create DB record
    doc_data = {
        "case_id": case_id,
        "document_name": filename,
        "mime_type": content_type,
        "file_size_bytes": file_size,
        "storage_path": storage_path,
        "extraction_status": "pending",
    }
    if conversation_id:
        doc_data["conversation_id"] = conversation_id

    try:
        result = (
            supabase.table("case_documents")
            .insert(doc_data)
            .execute()
        )
    except (HTTPException, LunaHTTPException):
        raise
    except Exception as e:
        logger.exception("Error creating document record: %s", e)
        # Try to clean up the uploaded file
        delete_file(bucket, storage_path, supabase=supabase)
        raise LunaHTTPException(status_code=500, code=ErrorCode.DOC_UPLOAD_FAILED, detail="حدث خطأ أثناء حفظ بيانات المستند")

    if not result.data:
        raise LunaHTTPException(status_code=500, code=ErrorCode.DOC_UPLOAD_FAILED, detail="حدث خطأ أثناء حفظ بيانات المستند")

    write_audit_log(
        supabase,
        user_id=user_id,
        action="upload",
        resource_type="document",
        resource_id=result.data[0]["document_id"],
    )

    return result.data[0]


def get_document(
    supabase: SupabaseClient,
    auth_id: str,
    document_id: str,
) -> dict:
    """Get a single document by ID with ownership check."""
    user_id = get_user_id(supabase, auth_id)
    doc = _verify_document_ownership(supabase, document_id, user_id)
    # Strip the joined table data
    doc.pop("lawyer_cases", None)
    return doc


def get_download_url(
    supabase: SupabaseClient,
    auth_id: str,
    document_id: str,
) -> dict:
    """Get a signed download URL for a document."""
    user_id = get_user_id(supabase, auth_id)
    doc = _verify_document_ownership(supabase, document_id, user_id)

    storage_path = doc.get("storage_path")
    if not storage_path:
        raise LunaHTTPException(status_code=404, code=ErrorCode.DOC_NOT_FOUND, detail="ملف المستند غير موجود")

    settings = get_settings()
    bucket = settings.STORAGE_BUCKET_DOCUMENTS

    try:
        url = get_signed_url(bucket, storage_path, expires_in=3600, supabase=supabase)
    except Exception as e:
        logger.exception("Error generating download URL: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء إنشاء رابط التحميل")

    expires_at = datetime.now(timezone.utc).replace(
        second=0, microsecond=0
    )
    from datetime import timedelta
    expires_at = (expires_at + timedelta(hours=1)).isoformat()

    return {
        "url": url,
        "expires_at": expires_at,
    }


def delete_document(
    supabase: SupabaseClient,
    auth_id: str,
    document_id: str,
) -> None:
    """Soft-delete a document and remove from storage."""
    user_id = get_user_id(supabase, auth_id)
    doc = _verify_document_ownership(supabase, document_id, user_id)

    now = datetime.now(timezone.utc).isoformat()

    # Soft delete in DB
    try:
        supabase.table("case_documents").update({
            "deleted_at": now,
            "updated_at": now,
        }).eq("document_id", document_id).execute()
    except Exception as e:
        logger.exception("Error deleting document: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء حذف المستند")

    write_audit_log(
        supabase,
        user_id=user_id,
        action="delete",
        resource_type="document",
        resource_id=document_id,
    )

    # Delete from storage (best effort)
    storage_path = doc.get("storage_path")
    if storage_path:
        settings = get_settings()
        delete_file(settings.STORAGE_BUCKET_DOCUMENTS, storage_path, supabase=supabase)
