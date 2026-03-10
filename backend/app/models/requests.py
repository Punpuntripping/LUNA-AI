"""
Pydantic request models for API endpoints.
"""
from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


# ── Auth ──────────────────────────────────────────────

class LoginRequest(BaseModel):
    """POST /api/v1/auth/login"""
    email: EmailStr
    password: str = Field(..., min_length=1)


class RegisterRequest(BaseModel):
    """POST /api/v1/auth/register"""
    email: EmailStr
    password: str = Field(..., min_length=8)
    full_name_ar: str = Field(..., min_length=2, max_length=100)


class RefreshRequest(BaseModel):
    """POST /api/v1/auth/refresh"""
    refresh_token: str
