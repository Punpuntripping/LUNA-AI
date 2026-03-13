"""
Cases API routes — /api/v1/cases/
6 endpoints: list, create, detail, update, archive, delete
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from supabase import Client as SupabaseClient

from backend.app.deps import get_current_user, get_supabase, validate_uuid
from backend.app.models.requests import (
    CreateCaseRequest,
    UpdateCaseRequest,
    UpdateCaseStatusRequest,
)
from backend.app.models.responses import (
    CaseDetail,
    CaseDetailResponse,
    CaseListResponse,
    CaseResponse,
    CaseStats,
    CaseSummary,
    ConversationSummary,
    CreateCaseResponse,
    SuccessResponse,
)
from backend.app.services import case_service
from shared.auth.jwt import AuthUser

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================
# GET / — List cases
# ============================================

@router.get("", response_model=CaseListResponse)
async def list_cases(
    status: Optional[str] = Query(None, description="Filter by status: active, closed, archived"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """List all cases for the authenticated user with pagination."""
    data = case_service.list_cases(
        supabase,
        current_user.auth_id,
        status=status,
        page=page,
        per_page=per_page,
    )

    return CaseListResponse(
        cases=[_to_case_summary(c) for c in data["cases"]],
        total=data["total"],
        page=data["page"],
        per_page=data["per_page"],
    )


# ============================================
# POST / — Create case
# ============================================

@router.post("", response_model=CreateCaseResponse, status_code=201)
async def create_case(
    body: CreateCaseRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Create a new case with an initial conversation."""
    data = case_service.create_case(
        supabase,
        current_user.auth_id,
        case_name=body.case_name,
        case_type=body.case_type,
        description=body.description,
        case_number=body.case_number,
        court_name=body.court_name,
        priority=body.priority,
    )

    return CreateCaseResponse(
        case=_to_case_detail(data["case"]),
        first_conversation_id=data["first_conversation_id"],
    )


# ============================================
# GET /{case_id} — Case detail
# ============================================

@router.get("/{case_id}", response_model=CaseDetailResponse)
async def get_case_detail(
    case_id: str,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Get full case detail with conversations and stats."""
    validate_uuid(case_id, "معرف القضية")
    data = case_service.get_case_detail(
        supabase,
        current_user.auth_id,
        case_id,
    )

    return CaseDetailResponse(
        case=_to_case_detail(data["case"]),
        conversations=[_to_conversation_summary(c) for c in data["conversations"]],
        stats=CaseStats(**data["stats"]),
    )


# ============================================
# PATCH /{case_id} — Update case
# ============================================

@router.patch("/{case_id}", response_model=CaseResponse)
async def update_case(
    case_id: str,
    body: UpdateCaseRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Update case fields (only provided fields are changed)."""
    validate_uuid(case_id, "معرف القضية")
    data = case_service.update_case(
        supabase,
        current_user.auth_id,
        case_id,
        case_name=body.case_name,
        case_type=body.case_type,
        description=body.description,
        case_number=body.case_number,
        court_name=body.court_name,
        priority=body.priority,
    )

    return CaseResponse(case=_to_case_detail(data))


# ============================================
# PATCH /{case_id}/status — Update case status
# ============================================

@router.patch("/{case_id}/status", response_model=CaseResponse)
async def update_case_status(
    case_id: str,
    body: UpdateCaseStatusRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Update case status (active, closed, archived)."""
    validate_uuid(case_id, "معرف القضية")
    data = case_service.update_case_status(
        supabase,
        current_user.auth_id,
        case_id,
        status=body.status,
    )

    return CaseResponse(case=_to_case_detail(data))


# ============================================
# DELETE /{case_id} — Soft-delete case
# ============================================

@router.delete("/{case_id}", response_model=SuccessResponse)
async def delete_case(
    case_id: str,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Soft-delete a case and all its conversations."""
    validate_uuid(case_id, "معرف القضية")
    case_service.delete_case(
        supabase,
        current_user.auth_id,
        case_id,
    )

    return SuccessResponse(success=True)


# ============================================
# MAPPERS — raw dict → Pydantic model
# ============================================

def _to_case_summary(data: dict) -> CaseSummary:
    """Map a raw case dict to CaseSummary response model."""
    return CaseSummary(
        case_id=data["case_id"],
        case_name=data["case_name"],
        case_type=data.get("case_type", "عام"),
        status=data.get("status", "active"),
        priority=data.get("priority", "medium"),
        description=data.get("description"),
        case_number=data.get("case_number"),
        court_name=data.get("court_name"),
        conversation_count=data.get("conversation_count", 0),
        document_count=data.get("document_count", 0),
        created_at=data["created_at"],
        updated_at=data["updated_at"],
    )


def _to_case_detail(data: dict) -> CaseDetail:
    """Map a raw case dict to CaseDetail response model."""
    return CaseDetail(
        case_id=data["case_id"],
        case_name=data["case_name"],
        case_type=data.get("case_type", "عام"),
        status=data.get("status", "active"),
        priority=data.get("priority", "medium"),
        description=data.get("description"),
        case_number=data.get("case_number"),
        court_name=data.get("court_name"),
        parties=data.get("parties"),
        conversation_count=data.get("conversation_count", 0),
        document_count=data.get("document_count", 0),
        created_at=data["created_at"],
        updated_at=data["updated_at"],
    )


def _to_conversation_summary(data: dict) -> ConversationSummary:
    """Map a raw conversation dict to ConversationSummary response model."""
    return ConversationSummary(
        conversation_id=data["conversation_id"],
        case_id=data.get("case_id"),
        title_ar=data.get("title_ar"),
        message_count=data.get("message_count", 0),
        is_active=data.get("is_active", True),
        created_at=data["created_at"],
        updated_at=data["updated_at"],
    )
