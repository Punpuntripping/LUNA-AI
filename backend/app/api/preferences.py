"""
Preferences API routes — /api/v1/
4 endpoints: get/update preferences, get/update marketing-email consent.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from supabase import Client as SupabaseClient

from backend.app.deps import get_current_user, get_supabase
from backend.app.models.requests import (
    UpdateMarketingEmailRequest,
    UpdatePreferencesRequest,
)
from backend.app.models.responses import MarketingEmailResponse, PreferencesResponse
from backend.app.services import preferences_service
from shared.auth.jwt import AuthUser
from shared.db.run import run_db

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/preferences", response_model=PreferencesResponse)
async def get_preferences(
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Get user preferences."""
    data = await run_db(
        preferences_service.get_preferences,
        supabase, current_user.auth_id,
    )
    return PreferencesResponse(
        user_id=data["user_id"],
        preferences=data.get("preferences", {}),
    )


@router.get("/preferences/marketing-email", response_model=MarketingEmailResponse)
async def get_marketing_email(
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """The user's marketing-email consent (users.marketing_opt_in)."""
    opt_in = await run_db(
        preferences_service.get_marketing_opt_in,
        supabase, current_user.auth_id,
    )
    return MarketingEmailResponse(marketing_opt_in=opt_in)


@router.patch("/preferences/marketing-email", response_model=MarketingEmailResponse)
async def update_marketing_email(
    body: UpdateMarketingEmailRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Settings toggle — stamps marketing_consent_src='settings_toggle'."""
    opt_in = await run_db(
        preferences_service.set_marketing_opt_in,
        supabase, current_user.auth_id, body.marketing_opt_in,
    )
    return MarketingEmailResponse(marketing_opt_in=opt_in)


@router.patch("/preferences", response_model=PreferencesResponse)
async def update_preferences(
    body: UpdatePreferencesRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Update user preferences (merge with existing)."""
    data = await run_db(
        preferences_service.update_preferences,
        supabase, current_user.auth_id, body.preferences,
    )
    return PreferencesResponse(
        user_id=data["user_id"],
        preferences=data.get("preferences", {}),
    )
