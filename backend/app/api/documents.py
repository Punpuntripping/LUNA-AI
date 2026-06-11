"""
Document endpoints.
CRUD for case documents with file upload/download.
"""
from __future__ import annotations

import asyncio
from time import perf_counter

from fastapi import APIRouter, Depends, File, Query, UploadFile
from supabase import Client as SupabaseClient

from backend.app.deps import get_current_user, get_supabase, validate_uuid
from backend.app.errors import ErrorCode, LunaHTTPException
from backend.app.models.requests import UploadInitRequest
from backend.app.models.responses import (
    DocumentListResponse,
    DocumentResponse,
    DownloadResponse,
    SuccessResponse,
    UploadInitResponse,
)
from shared.auth.jwt import AuthUser
from shared.db.run import run_db
from shared.observability import get_logfire
from backend.app.services import document_service

router = APIRouter()
_logfire = get_logfire()


@router.get(
    "/cases/{case_id}/documents",
    response_model=DocumentListResponse,
)
async def list_documents(
    case_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """List documents for a case."""
    validate_uuid(case_id, "معرف القضية")
    return await run_db(
        document_service.list_documents,
        supabase, user.auth_id, case_id, page=page, limit=limit
    )


@router.post(
    "/cases/{case_id}/documents",
    response_model=DocumentResponse,
)
async def upload_document(
    case_id: str,
    file: UploadFile = File(...),
    user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Upload a document to a case (legacy single-shot multipart)."""
    validate_uuid(case_id, "معرف القضية")

    # Async chunked read — never blocks the loop, enforces the 50 MB cap.
    # ``_MAX_FILE_SIZE`` lives in document_service so the cap has one home.
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > document_service._MAX_FILE_SIZE:
            raise LunaHTTPException(
                status_code=400,
                code=ErrorCode.DOC_TOO_LARGE,
                detail="حجم الملف يتجاوز الحد الأقصى (50 ميغابايت)",
            )
        chunks.append(chunk)
    file_bytes = b"".join(chunks)

    # All sync Supabase/storage round-trips run off the event loop.
    doc = await asyncio.to_thread(
        document_service.upload_document_bytes,
        supabase,
        user.auth_id,
        case_id,
        file_bytes=file_bytes,
        filename=file.filename or "document",
        content_type=file.content_type or "application/octet-stream",
    )
    return doc


@router.get(
    "/documents/{document_id}",
    response_model=DocumentResponse,
)
async def get_document(
    document_id: str,
    user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Get document details."""
    validate_uuid(document_id, "معرف المستند")
    return await run_db(document_service.get_document, supabase, user.auth_id, document_id)


@router.get(
    "/documents/{document_id}/download",
    response_model=DownloadResponse,
)
async def download_document(
    document_id: str,
    user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Get a signed download URL for a document."""
    validate_uuid(document_id, "معرف المستند")
    return await run_db(document_service.get_download_url, supabase, user.auth_id, document_id)


@router.delete(
    "/documents/{document_id}",
    response_model=SuccessResponse,
)
async def delete_document(
    document_id: str,
    user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Soft-delete a document."""
    validate_uuid(document_id, "معرف المستند")
    await run_db(document_service.delete_document, supabase, user.auth_id, document_id)
    return {"success": True}


# ============================================
# Resumable upload (TUS) — init / finalize / cancel
# ============================================
# The legacy POST /cases/{id}/documents (multipart) route above stays for the
# 7-day deprecation soak. Frontend cuts over to these endpoints in Phase 2.


@router.post(
    "/cases/{case_id}/documents/init",
    response_model=UploadInitResponse,
    status_code=201,
)
async def init_document_upload(
    case_id: str,
    body: UploadInitRequest,
    user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Open a resumable-upload session for a case document.

    The client then PATCHes bytes directly to ``upload_url`` (Supabase TUS)
    using its existing Supabase access token, and calls ``/finalize`` when
    done. Storage RLS (migration 045) restricts writes to the returned
    ``storage_path`` prefix.
    """
    validate_uuid(case_id, "معرف القضية")
    with _logfire.span(
        "upload.init",
        flow="document",
        case_id=case_id,
        mime_type=body.mime_type,
        size_bytes=body.size_bytes,
    ) as _span:
        session = await run_db(
            document_service.init_document_upload,
            supabase,
            user.auth_id,
            case_id,
            filename=body.filename,
            mime_type=body.mime_type,
            size_bytes=body.size_bytes,
        )
        try:
            _span.set_attribute("document_id", session["document_id"])
        except Exception:
            pass
        return UploadInitResponse(
            document_id=session["document_id"],
            storage_path=session["storage_path"],
            bucket=session["bucket"],
            upload_url=session["upload_url"],
            expires_at=session["expires_at"],
        )


@router.post(
    "/documents/{document_id}/finalize",
    response_model=DocumentResponse,
)
async def finalize_document_upload(
    document_id: str,
    user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Confirm the bytes for a resumable upload landed in storage.

    HEADs the object, compares size against the declared size, magic-byte-
    checks the first 16 bytes. On success the upload marker flips to
    ``ready`` and the document is treated as OCR-pending from here on.
    Returns 409 ``UPLOAD_NOT_COMPLETE`` if storage doesn't have the object
    yet — the client should retry the TUS upload.
    """
    validate_uuid(document_id, "معرف المستند")
    t0 = perf_counter()
    with _logfire.span(
        "upload.finalize",
        flow="document",
        document_id=document_id,
    ) as _span:
        result_code = "success"
        try:
            row = await run_db(
                document_service.finalize_document_upload,
                supabase, user.auth_id, document_id
            )
            return row
        except LunaHTTPException as exc:
            if exc.code == ErrorCode.UPLOAD_NOT_COMPLETE:
                result_code = "not_complete"
            elif exc.code == ErrorCode.UPLOAD_SIZE_MISMATCH:
                result_code = "size_mismatch"
            elif exc.code == ErrorCode.DOC_MAGIC_MISMATCH:
                result_code = "magic_mismatch"
            else:
                result_code = "error"
            raise
        finally:
            try:
                _span.set_attributes({
                    "duration_ms": int((perf_counter() - t0) * 1000),
                    "result": result_code,
                })
            except Exception:
                pass


@router.post(
    "/documents/{document_id}/cancel",
    response_model=SuccessResponse,
)
async def cancel_document_upload(
    document_id: str,
    user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Soft-delete a resumable-upload row and best-effort wipe its storage
    object. Idempotent — safe to call after the row is already cancelled or
    deleted."""
    validate_uuid(document_id, "معرف المستند")
    with _logfire.span(
        "upload.cancel",
        flow="document",
        document_id=document_id,
    ):
        await run_db(document_service.cancel_document_upload, supabase, user.auth_id, document_id)
        return {"success": True}
