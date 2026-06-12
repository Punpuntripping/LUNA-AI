"""
Auth API routes — /api/v1/auth/
5 endpoints: login, register, refresh, logout, me
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from redis.asyncio import Redis as AsyncRedis
from supabase import Client as SupabaseClient
from supabase_auth.errors import (
    AuthApiError,
    AuthRetryableError,
    AuthSessionMissingError,
)

from backend.app.errors import (
    LunaHTTPException,
    ErrorCode,
    MSG_SERVICE_UNAVAILABLE,
)
from backend.app.deps import get_current_user, get_supabase, get_supabase_auth, get_redis
from backend.app.models.requests import LoginRequest, RefreshRequest
from backend.app.models.responses import (
    LoginResponse,
    TokenResponse,
    UserProfile,
    UserProfileResponse,
    SuccessResponse,
)
from shared.auth.jwt import AuthUser
from shared.db.run import run_db

logger = logging.getLogger(__name__)

router = APIRouter()

# Redis session TTL: 24 hours
_SESSION_TTL = 86400

# Hard deadline for any single sync GoTrue call (matches gotrue's own httpx
# default of 5s, so a wait_for-abandoned thread self-terminates quickly).
_GOTRUE_TIMEOUT = 5.0


async def _gotrue_call(fn, /, *args, **kwargs):
    """Run a sync GoTrue call off the event loop with a hard 5s deadline.

    On Python 3.11+ asyncio.TimeoutError is builtins.TimeoutError, so callers
    catch TimeoutError to detect a hung GoTrue.
    """
    return await asyncio.wait_for(
        asyncio.to_thread(fn, *args, **kwargs), timeout=_GOTRUE_TIMEOUT
    )


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
        response = await _gotrue_call(
            supabase_auth.auth.sign_in_with_password,
            {"email": body.email, "password": body.password},
        )
    except (AuthRetryableError, TimeoutError) as e:
        # Network error inside gotrue, GoTrue 502/503/504, or GoTrue hung >5s.
        logger.error("GoTrue unavailable during login: %s", e)
        raise LunaHTTPException(
            status_code=503,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            detail=MSG_SERVICE_UNAVAILABLE,
        )
    except AuthApiError as e:
        if e.status in (400, 401, 403, 422):
            raise LunaHTTPException(
                status_code=401,
                code=ErrorCode.AUTH_INVALID,
                detail="بيانات الدخول غير صحيحة",
            )
        # Other status (5xx) — GoTrue server error, not the user's credentials.
        logger.error(
            "GoTrue API error during login (status=%s code=%s)", e.status, e.code
        )
        raise LunaHTTPException(
            status_code=503,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            detail=MSG_SERVICE_UNAVAILABLE,
        )
    except Exception as e:
        # AuthUnknownError / AuthSessionMissingError / anything unexpected:
        # don't blame the user's password for a garbage/unexpected response.
        logger.exception("Unexpected login error: %s", e)
        raise LunaHTTPException(
            status_code=503,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            detail=MSG_SERVICE_UNAVAILABLE,
        )

    session = response.session
    user = response.user

    if session is None or user is None:
        raise LunaHTTPException(status_code=401, code=ErrorCode.AUTH_INVALID, detail="بيانات الدخول غير صحيحة")

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


# Signup is performed in the browser via supabase.auth.signUp() (see
# frontend/stores/auth-store.ts). Doing it client-side keeps the PKCE
# code_verifier in the same browser that opens the email-confirmation link,
# which is required for /auth/callback's exchangeCodeForSession() to succeed.


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
        response = await _gotrue_call(
            supabase_auth.auth.refresh_session, body.refresh_token
        )
        session = response.session
        if session is None:
            raise LunaHTTPException(
                status_code=401,
                code=ErrorCode.AUTH_EXPIRED,
                detail="الرمز منتهي الصلاحية",
            )

        return TokenResponse(
            access_token=session.access_token,
            refresh_token=session.refresh_token,
        )
    except LunaHTTPException:
        raise
    except (AuthRetryableError, TimeoutError) as e:
        # Headline fix: an outage must NOT masquerade as an expired token, or
        # the frontend force-logs-out every user during a Supabase blip.
        logger.error("GoTrue unavailable during refresh: %s", e)
        raise LunaHTTPException(
            status_code=503,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            detail=MSG_SERVICE_UNAVAILABLE,
        )
    except AuthSessionMissingError:
        raise LunaHTTPException(
            status_code=401,
            code=ErrorCode.AUTH_EXPIRED,
            detail="الرمز منتهي الصلاحية",
        )
    except AuthApiError as e:
        if e.status in (400, 401, 403):
            raise LunaHTTPException(
                status_code=401,
                code=ErrorCode.AUTH_EXPIRED,
                detail="الرمز منتهي الصلاحية",
            )
        logger.error(
            "GoTrue API error during refresh (status=%s code=%s)", e.status, e.code
        )
        raise LunaHTTPException(
            status_code=503,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            detail=MSG_SERVICE_UNAVAILABLE,
        )
    except Exception as e:
        logger.exception("Unexpected token refresh error: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ داخلي",
        )


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

    Always returns 200 even when degraded: the client discards its tokens
    regardless, and a 503 would trap users who just want to log out. Shared-
    device risk is bounded by token expiry. Degradation is logged loudly once.
    """
    gotrue_ok = True
    redis_ok = True
    gotrue_err: Optional[Exception] = None
    redis_err: Optional[Exception] = None

    # Sign out from Supabase (invalidates tokens)
    try:
        await _gotrue_call(supabase_auth.auth.sign_out)
    except Exception as e:
        gotrue_ok = False
        gotrue_err = e

    # Delete Redis session
    if redis is not None:
        try:
            await redis.delete(f"session:{current_user.auth_id}")
        except Exception as e:
            redis_ok = False
            redis_err = e

    if not (gotrue_ok and redis_ok):
        logger.warning(
            "Degraded logout (gotrue_ok=%s redis_ok=%s): gotrue_err=%s redis_err=%s",
            gotrue_ok,
            redis_ok,
            gotrue_err,
            redis_err,
        )

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
    def _fetch_profile():
        return (
            supabase.table("users")
            .select("user_id, auth_id, email, full_name_ar, subscription_tier, plan_id, created_at")
            .eq("auth_id", current_user.auth_id)
            .maybe_single()
            .execute()
        )

    try:
        # Run the sync Supabase query off the event loop (httpx is blocking).
        result = await run_db(_fetch_profile)
    except Exception as e:
        logger.exception("Error querying user profile: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ داخلي")

    if result is None or result.data is None:
        raise LunaHTTPException(status_code=404, code=ErrorCode.USER_NOT_FOUND, detail="الملف الشخصي غير موجود")

    profile = result.data
    return UserProfileResponse(
        user_id=profile["user_id"],
        email=profile["email"],
        full_name_ar=profile.get("full_name_ar"),
        subscription_tier=profile.get("subscription_tier", "free"),
        plan_id=profile.get("plan_id"),
        created_at=profile.get("created_at"),
    )
