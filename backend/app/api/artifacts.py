"""
Artifacts API routes — /api/v1/
5 endpoints: list by conversation, list by case, get, update, delete
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from supabase import Client as SupabaseClient

from backend.app.deps import get_current_user, get_supabase
from backend.app.models.requests import UpdateArtifactRequest
from backend.app.models.responses import (
    ArtifactResponse,
    ArtifactListResponse,
    SuccessResponse,
)
from backend.app.services import artifact_service
from shared.auth.jwt import AuthUser

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================
# GET /conversations/{conversation_id}/artifacts
# ============================================

@router.get(
    "/conversations/{conversation_id}/artifacts",
    response_model=ArtifactListResponse,
)
async def list_conversation_artifacts(
    conversation_id: str,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """List artifacts for a conversation."""
    artifacts = artifact_service.list_artifacts_by_conversation(
        supabase, current_user.auth_id, conversation_id,
    )
    return ArtifactListResponse(
        artifacts=[_to_artifact(a) for a in artifacts],
        total=len(artifacts),
    )


# ============================================
# GET /cases/{case_id}/artifacts
# ============================================

@router.get(
    "/cases/{case_id}/artifacts",
    response_model=ArtifactListResponse,
)
async def list_case_artifacts(
    case_id: str,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """List artifacts for a case."""
    artifacts = artifact_service.list_artifacts_by_case(
        supabase, current_user.auth_id, case_id,
    )
    return ArtifactListResponse(
        artifacts=[_to_artifact(a) for a in artifacts],
        total=len(artifacts),
    )


# ============================================
# GET /artifacts/{artifact_id}
# ============================================

@router.get(
    "/artifacts/{artifact_id}",
    response_model=ArtifactResponse,
)
async def get_artifact(
    artifact_id: str,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Get a single artifact."""
    data = artifact_service.get_artifact(
        supabase, current_user.auth_id, artifact_id,
    )
    return _to_artifact(data)


# ============================================
# PATCH /artifacts/{artifact_id}
# ============================================

@router.patch(
    "/artifacts/{artifact_id}",
    response_model=ArtifactResponse,
)
async def update_artifact(
    artifact_id: str,
    body: UpdateArtifactRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Update artifact content/title (only if editable)."""
    data = artifact_service.update_artifact(
        supabase,
        current_user.auth_id,
        artifact_id,
        content_md=body.content_md,
        title=body.title,
    )
    return _to_artifact(data)


# ============================================
# DELETE /artifacts/{artifact_id}
# ============================================

@router.delete(
    "/artifacts/{artifact_id}",
    response_model=SuccessResponse,
)
async def delete_artifact(
    artifact_id: str,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Soft-delete an artifact."""
    artifact_service.delete_artifact(
        supabase, current_user.auth_id, artifact_id,
    )
    return SuccessResponse(success=True)


# ============================================
# MAPPER
# ============================================

def _to_artifact(data: dict) -> ArtifactResponse:
    return ArtifactResponse(
        artifact_id=data["artifact_id"],
        user_id=data["user_id"],
        conversation_id=data.get("conversation_id"),
        case_id=data.get("case_id"),
        agent_family=data["agent_family"],
        artifact_type=data["artifact_type"],
        title=data["title"],
        content_md=data.get("content_md", ""),
        is_editable=data.get("is_editable", False),
        metadata=data.get("metadata", {}),
        created_at=data["created_at"],
        updated_at=data["updated_at"],
    )
