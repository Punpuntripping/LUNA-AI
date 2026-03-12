"""
Document endpoints.
CRUD for case documents with file upload/download.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, UploadFile
from supabase import Client as SupabaseClient

from backend.app.deps import get_current_user, get_supabase
from backend.app.models.responses import (
    DocumentListResponse,
    DocumentResponse,
    DownloadResponse,
    SuccessResponse,
)
from shared.auth.jwt import AuthUser
from backend.app.services import document_service

router = APIRouter()


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
    return document_service.list_documents(
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
    """Upload a document to a case."""
    doc = document_service.upload_document(
        supabase, user.auth_id, case_id, file=file
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
    return document_service.get_document(supabase, user.auth_id, document_id)


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
    return document_service.get_download_url(supabase, user.auth_id, document_id)


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
    document_service.delete_document(supabase, user.auth_id, document_id)
    return {"success": True}
