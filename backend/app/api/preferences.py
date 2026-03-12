"""
Preferences + Templates API routes — /api/v1/
6 endpoints: get/update preferences, list/create/update/delete templates
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from supabase import Client as SupabaseClient

from backend.app.deps import get_current_user, get_supabase
from backend.app.models.requests import (
    UpdatePreferencesRequest,
    CreateTemplateRequest,
    UpdateTemplateRequest,
)
from backend.app.models.responses import (
    PreferencesResponse,
    TemplateResponse,
    TemplateListResponse,
    SuccessResponse,
)
from backend.app.services import preferences_service
from shared.auth.jwt import AuthUser

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================
# GET /preferences
# ============================================

@router.get("/preferences", response_model=PreferencesResponse)
async def get_preferences(
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Get user preferences."""
    data = preferences_service.get_preferences(
        supabase, current_user.auth_id,
    )
    return PreferencesResponse(
        user_id=data["user_id"],
        preferences=data.get("preferences", {}),
    )


# ============================================
# PATCH /preferences
# ============================================

@router.patch("/preferences", response_model=PreferencesResponse)
async def update_preferences(
    body: UpdatePreferencesRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Update user preferences (merge with existing)."""
    data = preferences_service.update_preferences(
        supabase, current_user.auth_id, body.preferences,
    )
    return PreferencesResponse(
        user_id=data["user_id"],
        preferences=data.get("preferences", {}),
    )


# ============================================
# GET /templates
# ============================================

@router.get("/templates", response_model=TemplateListResponse)
async def list_templates(
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """List user's templates."""
    templates = preferences_service.list_templates(
        supabase, current_user.auth_id,
    )
    return TemplateListResponse(
        templates=[_to_template(t) for t in templates],
        total=len(templates),
    )


# ============================================
# POST /templates
# ============================================

@router.post("/templates", response_model=TemplateResponse, status_code=201)
async def create_template(
    body: CreateTemplateRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Create a new prompt template."""
    data = preferences_service.create_template(
        supabase,
        current_user.auth_id,
        title=body.title,
        description=body.description,
        prompt_template=body.prompt_template,
        agent_family=body.agent_family,
    )
    return _to_template(data)


# ============================================
# PATCH /templates/{template_id}
# ============================================

@router.patch("/templates/{template_id}", response_model=TemplateResponse)
async def update_template(
    template_id: str,
    body: UpdateTemplateRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Update a template's fields."""
    data = preferences_service.update_template(
        supabase,
        current_user.auth_id,
        template_id,
        title=body.title,
        description=body.description,
        prompt_template=body.prompt_template,
        agent_family=body.agent_family,
        is_active=body.is_active,
    )
    return _to_template(data)


# ============================================
# DELETE /templates/{template_id}
# ============================================

@router.delete("/templates/{template_id}", response_model=SuccessResponse)
async def delete_template(
    template_id: str,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Soft-delete a template."""
    preferences_service.delete_template(
        supabase, current_user.auth_id, template_id,
    )
    return SuccessResponse(success=True)


# ============================================
# MAPPER
# ============================================

def _to_template(data: dict) -> TemplateResponse:
    return TemplateResponse(
        template_id=data["template_id"],
        user_id=data["user_id"],
        title=data["title"],
        description=data.get("description", ""),
        prompt_template=data["prompt_template"],
        agent_family=data["agent_family"],
        is_active=data.get("is_active", True),
        created_at=data["created_at"],
    )
