"""
Pydantic response models for API endpoints.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


# ── Shared ────────────────────────────────────────────

class SuccessResponse(BaseModel):
    """Generic success envelope."""
    success: bool = True


# ── Auth ──────────────────────────────────────────────

class UserProfile(BaseModel):
    """Embedded user profile in auth responses."""
    user_id: str
    email: str
    full_name_ar: Optional[str] = None
    subscription_tier: Optional[str] = None
    created_at: Optional[str] = None


class LoginResponse(BaseModel):
    """POST /api/v1/auth/login"""
    access_token: str
    refresh_token: str
    user: UserProfile


class RegisterResponse(BaseModel):
    """POST /api/v1/auth/register"""
    user: UserProfile
    verification_sent: bool = True


class TokenResponse(BaseModel):
    """POST /api/v1/auth/refresh"""
    access_token: str
    refresh_token: str


class UserProfileResponse(BaseModel):
    """GET /api/v1/auth/me — full profile from users table."""
    user_id: UUID
    email: str
    full_name_ar: Optional[str] = None
    subscription_tier: Optional[str] = None
    created_at: Optional[datetime] = None
