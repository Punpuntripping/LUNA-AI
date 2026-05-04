"""
Preferences API routes — /api/v1/
2 endpoints: get/update preferences.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from supabase import Client as SupabaseClient

from backend.app.deps import get_current_user, get_supabase
from backend.app.models.requests import UpdatePreferencesRequest
from backend.app.models.responses import PreferencesResponse
from backend.app.services import preferences_service
from shared.auth.jwt import AuthUser

logger = logging.getLogger(__name__)

router = APIRouter()


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
