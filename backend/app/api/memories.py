"""
Memory endpoints.
CRUD for case memories.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from supabase import Client as SupabaseClient

from backend.app.deps import get_current_user, get_supabase, validate_uuid
from backend.app.models.requests import CreateMemoryRequest, UpdateMemoryRequest
from backend.app.models.responses import (
    MemoryListResponse,
    MemoryResponse,
    SuccessResponse,
)
from shared.auth.jwt import AuthUser
from backend.app.services import memory_service

router = APIRouter()


@router.get(
    "/cases/{case_id}/memories",
    response_model=MemoryListResponse,
)
async def list_memories(
    case_id: str,
    type: Optional[str] = Query(None, alias="type"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """List memories for a case, optionally filtered by type."""
    validate_uuid(case_id, "معرف القضية")
    return memory_service.list_memories(
        supabase, user.auth_id, case_id,
        memory_type=type, page=page, limit=limit,
    )


@router.post(
    "/cases/{case_id}/memories",
    response_model=MemoryResponse,
    status_code=201,
)
async def create_memory(
    case_id: str,
    body: CreateMemoryRequest,
    user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Create a new memory for a case."""
    validate_uuid(case_id, "معرف القضية")
    return memory_service.create_memory(
        supabase, user.auth_id, case_id,
        memory_type=body.memory_type,
        content_ar=body.content_ar,
    )


@router.patch(
    "/memories/{memory_id}",
    response_model=MemoryResponse,
)
async def update_memory(
    memory_id: str,
    body: UpdateMemoryRequest,
    user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Update a memory's content or type."""
    validate_uuid(memory_id, "معرف الذاكرة")
    return memory_service.update_memory(
        supabase, user.auth_id, memory_id,
        content_ar=body.content_ar,
        memory_type=body.memory_type,
    )


@router.delete(
    "/memories/{memory_id}",
    response_model=SuccessResponse,
)
async def delete_memory(
    memory_id: str,
    user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Soft-delete a memory."""
    validate_uuid(memory_id, "معرف الذاكرة")
    memory_service.delete_memory(supabase, user.auth_id, memory_id)
    return {"success": True}
