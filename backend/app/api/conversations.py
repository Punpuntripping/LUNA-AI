"""
Conversations API routes — /api/v1/conversations/
6 endpoints: list, create, detail, update, delete, end-session
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from supabase import Client as SupabaseClient

from backend.app.deps import get_current_user, get_supabase
from backend.app.models.requests import (
    CreateConversationRequest,
    UpdateConversationRequest,
)
from backend.app.models.responses import (
    ConversationDetail,
    ConversationListResponse,
    ConversationResponse,
    ConversationSummary,
    SuccessResponse,
)
from backend.app.services import conversation_service
from shared.auth.jwt import AuthUser

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================
# GET / — List conversations
# ============================================

@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    case_id: Optional[str] = Query(None, description="Filter by case_id (UUID)"),
    limit: int = Query(50, ge=1, le=100, description="Max items to return"),
    offset: int = Query(0, ge=0, description="Number of items to skip"),
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """List conversations for the authenticated user, optionally filtered by case."""
    data = conversation_service.list_conversations(
        supabase,
        current_user.auth_id,
        case_id=case_id,
        limit=limit,
        offset=offset,
    )

    return ConversationListResponse(
        conversations=[_to_conversation_summary(c) for c in data["conversations"]],
        total=data["total"],
        has_more=data["has_more"],
    )


# ============================================
# POST / — Create conversation
# ============================================

@router.post("", response_model=ConversationResponse, status_code=201)
async def create_conversation(
    body: CreateConversationRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Create a new conversation, optionally linked to a case."""
    data = conversation_service.create_conversation(
        supabase,
        current_user.auth_id,
        case_id=body.case_id,
    )

    return ConversationResponse(conversation=_to_conversation_detail(data))


# ============================================
# GET /{conversation_id} — Conversation detail
# ============================================

@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: str,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Get a single conversation by ID."""
    data = conversation_service.get_conversation(
        supabase,
        current_user.auth_id,
        conversation_id,
    )

    return ConversationResponse(conversation=_to_conversation_detail(data))


# ============================================
# PATCH /{conversation_id} — Update conversation
# ============================================

@router.patch("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: str,
    body: UpdateConversationRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Update conversation title."""
    data = conversation_service.update_conversation(
        supabase,
        current_user.auth_id,
        conversation_id,
        title_ar=body.title_ar,
    )

    return ConversationResponse(conversation=_to_conversation_detail(data))


# ============================================
# DELETE /{conversation_id} — Soft-delete
# ============================================

@router.delete("/{conversation_id}", response_model=SuccessResponse)
async def delete_conversation(
    conversation_id: str,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Soft-delete a conversation."""
    conversation_service.delete_conversation(
        supabase,
        current_user.auth_id,
        conversation_id,
    )

    return SuccessResponse(success=True)


# ============================================
# POST /{conversation_id}/end-session — End session
# ============================================

@router.post("/{conversation_id}/end-session", response_model=ConversationResponse)
async def end_session(
    conversation_id: str,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """End a conversation session (sets ended_at timestamp)."""
    data = conversation_service.end_session(
        supabase,
        current_user.auth_id,
        conversation_id,
    )

    return ConversationResponse(conversation=_to_conversation_detail(data))


# ============================================
# MAPPERS — raw dict → Pydantic model
# ============================================

def _to_conversation_summary(data: dict) -> ConversationSummary:
    """Map a raw conversation dict to ConversationSummary."""
    return ConversationSummary(
        conversation_id=data["conversation_id"],
        case_id=data.get("case_id"),
        title_ar=data.get("title_ar"),
        message_count=data.get("message_count", 0),
        is_active=data.get("is_active", True),
        created_at=data["created_at"],
        updated_at=data["updated_at"],
    )


def _to_conversation_detail(data: dict) -> ConversationDetail:
    """Map a raw conversation dict to ConversationDetail."""
    return ConversationDetail(
        conversation_id=data["conversation_id"],
        case_id=data.get("case_id"),
        title_ar=data.get("title_ar"),
        message_count=data.get("message_count", 0),
        is_active=data.get("is_active", True),
        model_name=data.get("model"),
        created_at=data["created_at"],
        updated_at=data["updated_at"],
    )
