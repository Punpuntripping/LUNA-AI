"""
Workspace API routes -- /api/v1/

Replaces the old artifacts router. Targets the post-026 schema:
``workspace_items`` table, ``item_id`` PK, ``kind``-driven permissions.

Endpoints (existing, renamed paths):
    GET    /conversations/{conversation_id}/workspace
    GET    /cases/{case_id}/workspace
    GET    /workspace/{item_id}
    PATCH  /workspace/{item_id}
    DELETE /workspace/{item_id}

Endpoints (new):
    POST   /conversations/{conversation_id}/workspace/notes
    POST   /conversations/{conversation_id}/workspace/attachments/upload
    POST   /conversations/{conversation_id}/workspace/attachments/from-document
    POST   /conversations/{conversation_id}/workspace/references
    PATCH  /workspace/{item_id}/visibility
    GET    /workspace/{item_id}/file
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Optional

from fastapi import APIRouter, Depends, File, Query, UploadFile
from pydantic import BaseModel, Field
from supabase import Client as SupabaseClient

from backend.app.deps import get_current_user, get_supabase, validate_uuid
from backend.app.errors import ErrorCode, LunaHTTPException
from shared.observability import get_logfire
from backend.app.models.responses import (
    DownloadResponse,
    SuccessResponse,
    UploadInitResponse,
    WorkspaceItemListResponse,
    WorkspaceItemResponse,
)
from backend.app.models.requests import UpdateWorkspaceItemRequest, UploadInitRequest
from backend.app.services import workspace_service
from backend.app.services.case_service import get_user_id
from backend.app.services.references_service import fetch_item_references
from shared.auth.jwt import AuthUser
from shared.config import get_settings
from shared.db.run import run_db
from shared.storage.client import get_signed_url

logger = logging.getLogger(__name__)
_logfire = get_logfire()

router = APIRouter()


# ============================================
# REQUEST BODIES (workspace-only, not shared)
# ============================================


class CreateNoteRequest(BaseModel):
    """POST /conversations/{conversation_id}/workspace/notes"""
    title: str = Field(..., min_length=1, max_length=500)
    content_md: str = Field(default="", max_length=200_000)


class CreateReferenceRequest(BaseModel):
    """POST /conversations/{conversation_id}/workspace/references"""
    title: str = Field(..., min_length=1, max_length=500)
    content_md: Optional[str] = Field(default=None, max_length=200_000)


class FromDocumentRequest(BaseModel):
    """POST /conversations/{conversation_id}/workspace/attachments/from-document"""
    document_id: str = Field(..., min_length=1)


class UpdateVisibilityRequest(BaseModel):
    """PATCH /workspace/{item_id}/visibility"""
    is_visible: bool


class UpdateFeedbackRequest(BaseModel):
    """PATCH /workspace/{item_id}/feedback

    ``feedback`` is the user's 👍/👎 rating: ``'up'`` / ``'down'`` / ``None``
    (None clears it). Validation of the literal values happens in the service.
    """
    feedback: Optional[str] = None


# ============================================
# MAPPERS
# ============================================


def _to_response(data: dict) -> WorkspaceItemResponse:
    """Translate a workspace_items row into the response model."""
    item_id = data.get("item_id") or data.get("artifact_id") or ""
    return WorkspaceItemResponse(
        item_id=item_id,
        user_id=data["user_id"],
        conversation_id=data.get("conversation_id"),
        case_id=data.get("case_id"),
        message_id=data.get("message_id"),
        agent_family=data.get("agent_family"),
        kind=data.get("kind", "agent_search"),
        created_by=data.get("created_by", "agent"),
        title=data.get("title", ""),
        content_md=data.get("content_md"),
        storage_path=data.get("storage_path"),
        document_id=data.get("document_id"),
        is_visible=bool(data.get("is_visible", True)),
        feedback=data.get("feedback"),
        metadata=data.get("metadata") or {},
        created_at=data["created_at"],
        updated_at=data["updated_at"],
    )


# ============================================
# LIST endpoints (renamed paths)
# ============================================


@router.get(
    "/conversations/{conversation_id}/workspace",
    response_model=WorkspaceItemListResponse,
)
async def list_conversation_workspace(
    conversation_id: str,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """List workspace items for a conversation."""
    validate_uuid(conversation_id, "معرف المحادثة")
    items, total = await run_db(
        workspace_service.list_workspace_items_by_conversation,
        supabase, current_user.auth_id, conversation_id,
        limit=limit, offset=offset,
    )
    return WorkspaceItemListResponse(
        items=[_to_response(i) for i in items],
        total=total,
    )


@router.get(
    "/cases/{case_id}/workspace",
    response_model=WorkspaceItemListResponse,
)
async def list_case_workspace(
    case_id: str,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """List workspace items for a case."""
    validate_uuid(case_id, "معرف القضية")
    items, total = await run_db(
        workspace_service.list_workspace_items_by_case,
        supabase, current_user.auth_id, case_id,
        limit=limit, offset=offset,
    )
    return WorkspaceItemListResponse(
        items=[_to_response(i) for i in items],
        total=total,
    )


# ============================================
# SINGLE-ITEM endpoints
# ============================================


@router.get(
    "/workspace/{item_id}",
    response_model=WorkspaceItemResponse,
)
async def get_workspace_item(
    item_id: str,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Get a single workspace item."""
    validate_uuid(item_id, "معرف العنصر")
    data = await run_db(
        workspace_service.get_workspace_item,
        supabase, current_user.auth_id, item_id,
    )
    return _to_response(data)


@router.get(
    "/workspace/{item_id}/references",
)
async def list_workspace_item_references(
    item_id: str,
    used: Optional[bool] = Query(
        default=None,
        description="When true, only return references the synthesis cited inline.",
    ),
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """List the references attached to an ``agent_search`` workspace item.

    Replaces the pre-migration-049 ``metadata.references`` JSONB read path.
    The response shape is the same ``Reference`` list the frontend
    ``ReferencePanel`` already consumes — the only change is where the data
    lives. ``get_workspace_item`` is called first to enforce ownership; a
    cross-user item_id surfaces as 404 before any references are exposed.
    """
    validate_uuid(item_id, "معرف العنصر")
    # Ownership check via the existing service. Raises 404 if the item is
    # not visible to this user — same envelope as get_workspace_item.
    await run_db(workspace_service.get_workspace_item, supabase, current_user.auth_id, item_id)
    references = await fetch_item_references(
        supabase, item_id, used_only=bool(used) if used is not None else False,
    )
    return {"references": [r.model_dump(mode="json") for r in references]}


@router.patch(
    "/workspace/{item_id}",
    response_model=WorkspaceItemResponse,
)
async def update_workspace_item(
    item_id: str,
    body: UpdateWorkspaceItemRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Update workspace item title/content. Permission keyed on ``kind``."""
    validate_uuid(item_id, "معرف العنصر")
    data = await run_db(
        workspace_service.update_workspace_item,
        supabase,
        current_user.auth_id,
        item_id,
        content_md=body.content_md,
        title=body.title,
    )
    return _to_response(data)


@router.delete(
    "/workspace/{item_id}",
    response_model=SuccessResponse,
)
async def delete_workspace_item(
    item_id: str,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Soft-delete a workspace item."""
    validate_uuid(item_id, "معرف العنصر")
    await run_db(
        workspace_service.delete_workspace_item,
        supabase, current_user.auth_id, item_id,
    )
    return SuccessResponse(success=True)


@router.patch(
    "/workspace/{item_id}/visibility",
    response_model=WorkspaceItemResponse,
)
async def update_workspace_visibility(
    item_id: str,
    body: UpdateVisibilityRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Toggle ``is_visible`` (works on any kind, including non-editable ones)."""
    validate_uuid(item_id, "معرف العنصر")
    data = await run_db(
        workspace_service.update_visibility,
        supabase,
        current_user.auth_id,
        item_id,
        is_visible=body.is_visible,
    )
    return _to_response(data)


@router.patch(
    "/workspace/{item_id}/feedback",
    response_model=WorkspaceItemResponse,
)
async def update_workspace_feedback(
    item_id: str,
    body: UpdateFeedbackRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Set the user's 👍/👎 rating ('up' / 'down' / null). Works on any kind
    (including read-only ones like ``agent_search``) — feedback is a UX flag,
    not content mutation, so it bypasses the kind-edit permission check."""
    validate_uuid(item_id, "معرف العنصر")
    data = await run_db(
        workspace_service.update_feedback,
        supabase,
        current_user.auth_id,
        item_id,
        feedback=body.feedback,
    )
    return _to_response(data)


# ============================================
# CREATE: notes / references
# ============================================


@router.post(
    "/conversations/{conversation_id}/workspace/notes",
    response_model=WorkspaceItemResponse,
    status_code=201,
)
async def create_note(
    conversation_id: str,
    body: CreateNoteRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Create a user-authored note inside the conversation workspace."""
    validate_uuid(conversation_id, "معرف المحادثة")
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)
    row = await run_db(
        workspace_service.create_workspace_item,
        supabase,
        user_id,
        kind="note",
        created_by="user",
        title=body.title,
        conversation_id=conversation_id,
        content_md=body.content_md,
    )
    return _to_response(row)


@router.post(
    "/conversations/{conversation_id}/workspace/references",
    response_model=WorkspaceItemResponse,
    status_code=201,
)
async def create_reference(
    conversation_id: str,
    body: CreateReferenceRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Create a placeholder ``references`` workspace item."""
    validate_uuid(conversation_id, "معرف المحادثة")
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)
    row = await run_db(
        workspace_service.create_workspace_item,
        supabase,
        user_id,
        kind="references",
        created_by="user",
        title=body.title,
        conversation_id=conversation_id,
        content_md=body.content_md or "",
    )
    return _to_response(row)


# ============================================
# CREATE: attachments
# ============================================
#
# Content validation rules (MIME / size / magic bytes) live in
# ``workspace_service.upload_attachment_bytes`` now — the legacy upload route
# only does the async chunked read and delegates the rest off the event loop.


@router.post(
    "/conversations/{conversation_id}/workspace/attachments/upload",
    response_model=WorkspaceItemResponse,
    status_code=201,
)
async def upload_workspace_attachment(
    conversation_id: str,
    file: UploadFile = File(...),
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Upload a file attachment into the conversation workspace (legacy
    single-shot multipart).

    The file is stored in the same bucket as case_documents but under a
    ``conversations/{conversation_id}/`` prefix so it is conceptually
    scoped to the conversation, not the case.
    """
    validate_uuid(conversation_id, "معرف المحادثة")

    # Async chunked read — never blocks the loop, enforces the 50 MB cap.
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > workspace_service._MAX_FILE_SIZE:
            raise LunaHTTPException(
                status_code=400,
                code=ErrorCode.DOC_TOO_LARGE,
                detail="حجم الملف يتجاوز الحد الأقصى (50 ميغابايت)",
            )
        chunks.append(chunk)
    file_bytes = b"".join(chunks)

    # All sync Supabase/storage round-trips (validation, insert-first, storage
    # write, promote) run off the event loop.
    row = await asyncio.to_thread(
        workspace_service.upload_attachment_bytes,
        supabase,
        current_user.auth_id,
        conversation_id,
        file_bytes=file_bytes,
        filename=file.filename or "attachment",
        content_type=file.content_type or "application/octet-stream",
    )
    return _to_response(row)


@router.post(
    "/conversations/{conversation_id}/workspace/attachments/from-document",
    response_model=WorkspaceItemResponse,
    status_code=201,
)
async def attach_from_case_document(
    conversation_id: str,
    body: FromDocumentRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Pin an existing case_documents row into this conversation's workspace.

    No file copy -- the workspace item carries a ``document_id`` FK that
    resolves to ``case_documents.storage_path`` at signing time.
    """
    validate_uuid(conversation_id, "معرف المحادثة")
    validate_uuid(body.document_id, "معرف المستند")
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)

    # Verify the document is owned by this user (joins back to lawyer_cases).
    def _fetch_doc_row():
        return (
            supabase.table("case_documents")
            .select("document_name, mime_type, file_size_bytes, lawyer_cases!inner(lawyer_user_id)")
            .eq("document_id", body.document_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )

    try:
        doc_row = await run_db(_fetch_doc_row)
    except Exception as e:
        logger.exception("Error verifying document ownership: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ داخلي",
        )

    if doc_row is None or doc_row.data is None:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.DOC_NOT_FOUND,
            detail="المستند غير موجود",
        )
    if doc_row.data.get("lawyer_cases", {}).get("lawyer_user_id") != user_id:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.DOC_NOT_FOUND,
            detail="المستند غير موجود",
        )

    metadata = {
        "filename": doc_row.data.get("document_name"),
        "mime_type": doc_row.data.get("mime_type"),
        "file_size_bytes": doc_row.data.get("file_size_bytes"),
        "linked_from_case_documents": True,
    }
    row = await run_db(
        workspace_service.create_workspace_item,
        supabase,
        user_id,
        kind="attachment",
        created_by="user",
        title=doc_row.data.get("document_name") or "مرفق",
        conversation_id=conversation_id,
        document_id=body.document_id,
        metadata=metadata,
    )
    return _to_response(row)


# ============================================
# Resumable attachment upload (TUS) — init / finalize / cancel
# ============================================
# Browser uploads bytes directly to Supabase Storage and the backend only
# brokers the session. The legacy multipart upload route above stays for the
# 7-day deprecation soak. Frontend cuts over to these endpoints in Phase 2.


@router.post(
    "/conversations/{conversation_id}/workspace/attachments/init",
    response_model=UploadInitResponse,
    status_code=201,
)
async def init_workspace_attachment_upload(
    conversation_id: str,
    body: UploadInitRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Open a resumable-upload session for a chat-conversation attachment.

    Creates a ``workspace_items`` row with ``kind='attachment'`` and
    ``metadata.upload_status='uploading'``. The client uploads bytes to
    ``upload_url`` (Supabase TUS) using its existing access token, then
    calls ``/finalize``.
    """
    validate_uuid(conversation_id, "معرف المحادثة")
    with _logfire.span(
        "upload.init",
        flow="attachment",
        conversation_id=conversation_id,
        mime_type=body.mime_type,
        size_bytes=body.size_bytes,
    ) as _span:
        session = await run_db(
            workspace_service.init_attachment_upload,
            supabase,
            current_user.auth_id,
            conversation_id,
            filename=body.filename,
            mime_type=body.mime_type,
            size_bytes=body.size_bytes,
            page_count=body.page_count,
        )
        try:
            _span.set_attribute("item_id", session["item_id"])
        except Exception:
            pass
        return UploadInitResponse(
            item_id=session["item_id"],
            storage_path=session["storage_path"],
            bucket=session["bucket"],
            upload_url=session["upload_url"],
            expires_at=session["expires_at"],
        )


@router.post(
    "/workspace/attachments/{item_id}/finalize",
    response_model=WorkspaceItemResponse,
)
async def finalize_workspace_attachment_upload(
    item_id: str,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Confirm a resumable chat-attachment upload landed in storage.

    Same semantics as ``/documents/{id}/finalize``: HEAD + size match +
    magic byte check. Flips ``metadata.upload_status='ready'``. Returns
    409 ``UPLOAD_NOT_COMPLETE`` if the object isn't in storage yet.
    """
    validate_uuid(item_id, "معرف العنصر")
    t0 = perf_counter()
    with _logfire.span(
        "upload.finalize",
        flow="attachment",
        item_id=item_id,
    ) as _span:
        result_code = "success"
        try:
            row = await run_db(
                workspace_service.finalize_attachment_upload,
                supabase, current_user.auth_id, item_id
            )
            return _to_response(row)
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
    "/workspace/attachments/{item_id}/cancel",
    response_model=SuccessResponse,
)
async def cancel_workspace_attachment_upload(
    item_id: str,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Soft-delete a resumable chat-attachment row and best-effort wipe the
    partial storage object. Idempotent."""
    validate_uuid(item_id, "معرف العنصر")
    with _logfire.span(
        "upload.cancel",
        flow="attachment",
        item_id=item_id,
    ):
        await run_db(
            workspace_service.cancel_attachment_upload,
            supabase, current_user.auth_id, item_id
        )
        return SuccessResponse(success=True)


# ============================================
# Signed URL for attachment files
# ============================================


@router.get(
    "/workspace/{item_id}/file",
    response_model=DownloadResponse,
)
async def get_workspace_file_url(
    item_id: str,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Return a signed URL for the file backing an ``attachment`` item.

    Rules:
        * 404 if the item is not an attachment.
        * If ``storage_path`` is set on the item, sign that path.
        * Otherwise resolve ``document_id`` -> ``case_documents.storage_path``
          and sign that. (No file copy -- linked attachments share the
          underlying object with the case library.)
    """
    validate_uuid(item_id, "معرف العنصر")
    item = await run_db(
        workspace_service.get_workspace_item,
        supabase, current_user.auth_id, item_id,
    )

    if item.get("kind") != "attachment":
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.ARTIFACT_NOT_FOUND,
            detail="هذا العنصر لا يحتوي ملفاً",
        )

    settings = get_settings()
    bucket = settings.STORAGE_BUCKET_DOCUMENTS

    storage_path = item.get("storage_path")
    if not storage_path and item.get("document_id"):
        # Resolve the linked case_documents path.
        def _fetch_storage_path():
            return (
                supabase.table("case_documents")
                .select("storage_path")
                .eq("document_id", item["document_id"])
                .is_("deleted_at", "null")
                .maybe_single()
                .execute()
            )

        try:
            doc_row = await run_db(_fetch_storage_path)
        except Exception as e:
            logger.exception("Error resolving linked case_document: %s", e)
            raise LunaHTTPException(
                status_code=500,
                code=ErrorCode.INTERNAL_ERROR,
                detail="حدث خطأ داخلي",
            )
        if doc_row is None or doc_row.data is None:
            raise LunaHTTPException(
                status_code=404,
                code=ErrorCode.DOC_NOT_FOUND,
                detail="ملف المستند غير موجود",
            )
        storage_path = doc_row.data.get("storage_path")

    if not storage_path:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.DOC_NOT_FOUND,
            detail="ملف المستند غير موجود",
        )

    try:
        url = await run_db(
            get_signed_url, bucket, storage_path, expires_in=3600, supabase=supabase
        )
    except Exception as e:
        logger.exception("Error generating signed URL for workspace file: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء إنشاء رابط الملف",
        )

    expires_at = (
        datetime.now(timezone.utc) + timedelta(hours=1)
    ).isoformat()
    return DownloadResponse(url=url, expires_at=expires_at)
