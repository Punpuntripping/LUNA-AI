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

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, Field
from supabase import Client as SupabaseClient

from backend.app.deps import get_current_user, get_supabase, validate_uuid
from backend.app.errors import ErrorCode, LunaHTTPException
from backend.app.models.responses import (
    DownloadResponse,
    SuccessResponse,
    WorkspaceItemListResponse,
    WorkspaceItemResponse,
)
from backend.app.models.requests import UpdateWorkspaceItemRequest
from backend.app.services import workspace_service
from backend.app.services.case_service import get_user_id
from shared.auth.jwt import AuthUser
from shared.config import get_settings
from shared.storage.client import (
    build_storage_path,
    get_signed_url,
    upload_file,
)

logger = logging.getLogger(__name__)

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
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """List workspace items for a conversation."""
    validate_uuid(conversation_id, "معرف المحادثة")
    items = workspace_service.list_workspace_items_by_conversation(
        supabase, current_user.auth_id, conversation_id,
    )
    return WorkspaceItemListResponse(
        items=[_to_response(i) for i in items],
        total=len(items),
    )


@router.get(
    "/cases/{case_id}/workspace",
    response_model=WorkspaceItemListResponse,
)
async def list_case_workspace(
    case_id: str,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """List workspace items for a case."""
    validate_uuid(case_id, "معرف القضية")
    items = workspace_service.list_workspace_items_by_case(
        supabase, current_user.auth_id, case_id,
    )
    return WorkspaceItemListResponse(
        items=[_to_response(i) for i in items],
        total=len(items),
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
    data = workspace_service.get_workspace_item(
        supabase, current_user.auth_id, item_id,
    )
    return _to_response(data)


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
    data = workspace_service.update_workspace_item(
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
    workspace_service.delete_workspace_item(
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
    data = workspace_service.update_visibility(
        supabase,
        current_user.auth_id,
        item_id,
        is_visible=body.is_visible,
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
    user_id = get_user_id(supabase, current_user.auth_id)
    row = workspace_service.create_workspace_item(
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
    user_id = get_user_id(supabase, current_user.auth_id)
    row = workspace_service.create_workspace_item(
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


# Reuse the document validation rules so workspace attachments behave
# identically to case_documents uploads.
_ALLOWED_MIME_TYPES = {"application/pdf", "image/png", "image/jpeg"}
_MAGIC_BYTES = {
    "application/pdf": b"%PDF",
    "image/png": b"\x89PNG",
    "image/jpeg": b"\xff\xd8\xff",
}
_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB


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
    """Upload a file attachment into the conversation workspace.

    The file is stored in the same bucket as case_documents but under a
    ``conversations/{conversation_id}/`` prefix so it is conceptually
    scoped to the conversation, not the case.
    """
    validate_uuid(conversation_id, "معرف المحادثة")
    user_id = get_user_id(supabase, current_user.auth_id)

    content_type = file.content_type or "application/octet-stream"
    if content_type not in _ALLOWED_MIME_TYPES:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.DOC_INVALID_TYPE,
            detail="نوع الملف غير مسموح. الأنواع المسموحة: PDF, PNG, JPG",
        )

    # Pre-flight size check if the client supplied Content-Length.
    if hasattr(file, "size") and file.size and file.size > _MAX_FILE_SIZE:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.DOC_TOO_LARGE,
            detail="حجم الملف يتجاوز الحد الأقصى (50 ميغابايت)",
        )

    # Chunked read so we don't load oversized files into memory.
    chunks: list[bytes] = []
    total = 0
    chunk_size = 1024 * 1024
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
    if total == 0:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.DOC_EMPTY,
            detail="الملف فارغ",
        )

    expected_magic = _MAGIC_BYTES.get(content_type)
    if expected_magic and not file_bytes[: len(expected_magic)].startswith(expected_magic):
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.DOC_MAGIC_MISMATCH,
            detail="محتوى الملف لا يتطابق مع نوعه المعلن",
        )

    settings = get_settings()
    bucket = settings.STORAGE_BUCKET_DOCUMENTS
    filename = file.filename or "attachment"
    # build_storage_path produces a per-conversation prefix when only
    # conversation_id is supplied (general/{user_id}/convos/{conversation_id}/...).
    storage_path = build_storage_path(None, user_id, conversation_id, filename)

    try:
        upload_file(bucket, storage_path, file_bytes, content_type, supabase=supabase)
    except Exception as e:
        logger.exception("Workspace attachment upload failed: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.DOC_UPLOAD_FAILED,
            detail="حدث خطأ أثناء رفع الملف",
        )

    metadata = {
        "filename": filename,
        "mime_type": content_type,
        "file_size_bytes": total,
    }
    row = workspace_service.create_workspace_item(
        supabase,
        user_id,
        kind="attachment",
        created_by="user",
        title=filename,
        conversation_id=conversation_id,
        storage_path=storage_path,
        metadata=metadata,
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
    user_id = get_user_id(supabase, current_user.auth_id)

    # Verify the document is owned by this user (joins back to lawyer_cases).
    try:
        doc_row = (
            supabase.table("case_documents")
            .select("document_name, mime_type, file_size_bytes, lawyer_cases!inner(lawyer_user_id)")
            .eq("document_id", body.document_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
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
    row = workspace_service.create_workspace_item(
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
    item = workspace_service.get_workspace_item(
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
        try:
            doc_row = (
                supabase.table("case_documents")
                .select("storage_path")
                .eq("document_id", item["document_id"])
                .is_("deleted_at", "null")
                .maybe_single()
                .execute()
            )
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
        url = get_signed_url(bucket, storage_path, expires_in=3600, supabase=supabase)
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
