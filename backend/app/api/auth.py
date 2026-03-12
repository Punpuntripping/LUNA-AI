"""
Auth API routes — /api/v1/auth/
5 endpoints: login, register, refresh, logout, me
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from redis.asyncio import Redis as AsyncRedis
from supabase import Client as SupabaseClient

from backend.app.deps import get_current_user, get_supabase, get_supabase_auth, get_redis
from backend.app.models.requests import LoginRequest, RegisterRequest, RefreshRequest
from backend.app.models.responses import (
    LoginResponse,
    RegisterResponse,
    TokenResponse,
    UserProfile,
    UserProfileResponse,
    SuccessResponse,
)
from shared.auth.jwt import AuthUser

logger = logging.getLogger(__name__)

router = APIRouter()

# Redis session TTL: 24 hours
_SESSION_TTL = 86400


# ============================================
# POST /login
# ============================================

@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    supabase_auth: SupabaseClient = Depends(get_supabase_auth),
    redis: Optional[AsyncRedis] = Depends(get_redis),
):
    """
    Authenticate a user with email + password.
    Returns access_token, refresh_token, and user profile.
    """
    try:
        response = supabase_auth.auth.sign_in_with_password(
            {"email": body.email, "password": body.password}
        )
    except Exception as e:
        error_msg = str(e).lower()
        if "invalid" in error_msg or "credentials" in error_msg or "400" in error_msg:
            raise HTTPException(status_code=401, detail="بيانات الدخول غير صحيحة")
        logger.exception("Login error: %s", e)
        raise HTTPException(status_code=401, detail="بيانات الدخول غير صحيحة")

    session = response.session
    user = response.user

    if session is None or user is None:
        raise HTTPException(status_code=401, detail="بيانات الدخول غير صحيحة")

    # Create Redis session (fail silently if Redis unavailable)
    if redis is not None:
        try:
            session_data = json.dumps(
                {
                    "auth_id": user.id,
                    "email": user.email,
                    "logged_in_at": str(session.expires_at),
                },
                ensure_ascii=False,
            )
            await redis.set(f"session:{user.id}", session_data, ex=_SESSION_TTL)
        except Exception as e:
            logger.warning("Failed to create Redis session: %s", e)

    user_metadata = user.user_metadata or {}

    return LoginResponse(
        access_token=session.access_token,
        refresh_token=session.refresh_token,
        user=UserProfile(
            user_id=user.id,
            email=user.email or "",
            full_name_ar=user_metadata.get("full_name_ar"),
            subscription_tier="free",
            created_at=user.created_at if user.created_at else None,
        ),
    )


# ============================================
# POST /register
# ============================================

@router.post("/register", response_model=RegisterResponse, status_code=201)
async def register(
    body: RegisterRequest,
    supabase_auth: SupabaseClient = Depends(get_supabase_auth),
):
    """
    Register a new user with email, password, and Arabic full name.
    Profile row is created automatically by DB trigger.
    """
    try:
        response = supabase_auth.auth.sign_up(
            {
                "email": body.email,
                "password": body.password,
                "options": {
                    "data": {
                        "full_name_ar": body.full_name_ar,
                    },
                },
            }
        )
    except Exception as e:
        error_msg = str(e).lower()
        if "already" in error_msg or "exists" in error_msg or "duplicate" in error_msg or "422" in error_msg:
            raise HTTPException(
                status_code=409,
                detail="البريد الإلكتروني مسجل مسبقاً",
            )
        if "rate limit" in error_msg or "429" in error_msg:
            raise HTTPException(
                status_code=429,
                detail="تم تجاوز الحد المسموح من الطلبات",
            )
        logger.exception("Registration error: %s", e)
        raise HTTPException(status_code=400, detail="فشل إنشاء الحساب")

    user = response.user
    if user is None:
        raise HTTPException(status_code=400, detail="فشل إنشاء الحساب")

    # Check if user already existed (Supabase returns user but identities is empty)
    if hasattr(user, "identities") and user.identities is not None and len(user.identities) == 0:
        raise HTTPException(
            status_code=409,
            detail="البريد الإلكتروني مسجل مسبقاً",
        )

    user_metadata = user.user_metadata or {}

    return RegisterResponse(
        user=UserProfile(
            user_id=user.id,
            email=user.email or "",
            full_name_ar=user_metadata.get("full_name_ar", body.full_name_ar),
            subscription_tier="free",
            created_at=user.created_at if user.created_at else None,
        ),
        verification_sent=True,
    )


# ============================================
# POST /refresh
# ============================================

@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    supabase_auth: SupabaseClient = Depends(get_supabase_auth),
):
    """
    Exchange a refresh token for a new access + refresh token pair.
    """
    try:
        response = supabase_auth.auth.refresh_session(body.refresh_token)
        session = response.session
        if session is None:
            raise HTTPException(status_code=401, detail="الرمز منتهي الصلاحية")

        return TokenResponse(
            access_token=session.access_token,
            refresh_token=session.refresh_token,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Token refresh error: %s", e)
        raise HTTPException(status_code=401, detail="الرمز منتهي الصلاحية")


# ============================================
# POST /logout
# ============================================

@router.post("/logout", response_model=SuccessResponse)
async def logout(
    current_user: AuthUser = Depends(get_current_user),
    supabase_auth: SupabaseClient = Depends(get_supabase_auth),
    redis: Optional[AsyncRedis] = Depends(get_redis),
):
    """
    Sign out the current user, delete Redis session.
    """
    # Sign out from Supabase (invalidates tokens)
    try:
        supabase_auth.auth.sign_out()
    except Exception as e:
        logger.warning("Supabase sign_out failed: %s", e)

    # Delete Redis session
    if redis is not None:
        try:
            await redis.delete(f"session:{current_user.auth_id}")
        except Exception as e:
            logger.warning("Failed to delete Redis session: %s", e)

    return SuccessResponse(success=True)


# ============================================
# GET /me
# ============================================

@router.get("/me", response_model=UserProfileResponse)
async def me(
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """
    Return the authenticated user's profile from the users table.
    """
    try:
        result = (
            supabase.table("users")
            .select("user_id, auth_id, email, full_name_ar, subscription_tier, created_at")
            .eq("auth_id", current_user.auth_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logger.exception("Error querying user profile: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ داخلي")

    if result is None or result.data is None:
        raise HTTPException(status_code=404, detail="الملف الشخصي غير موجود")

    profile = result.data
    return UserProfileResponse(
        user_id=profile["user_id"],
        email=profile["email"],
        full_name_ar=profile.get("full_name_ar"),
        subscription_tier=profile.get("subscription_tier", "free"),
        created_at=profile.get("created_at"),
    )
