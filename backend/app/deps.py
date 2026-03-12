"""
FastAPI dependency injection functions.
Used with Depends() in route handlers.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from shared.auth.jwt import AuthUser, AuthError, extract_user, TokenExpiredError, TokenInvalidError
from supabase import Client as SupabaseClient
from redis.asyncio import Redis as AsyncRedis

logger = logging.getLogger(__name__)

# HTTPBearer extracts the Authorization header automatically.
# auto_error=False so we can return Arabic error messages.
_bearer_scheme = HTTPBearer(auto_error=False)

_AUTH_401 = HTTPException(
    status_code=401,
    detail="بيانات الدخول غير صحيحة",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> AuthUser:
    """
    Validate the JWT locally using PyJWT (HS256) and return the authenticated user.
    Uses shared.auth.jwt.extract_user() for local decode — no network call to Supabase.

    Raises:
        HTTPException 401 with Arabic message if token is missing/invalid/expired.
    """
    if credentials is None:
        raise _AUTH_401

    token = credentials.credentials

    try:
        user = extract_user(token)
    except TokenExpiredError:
        logger.warning("JWT expired")
        raise HTTPException(
            status_code=401,
            detail="انتهت صلاحية الجلسة",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except (TokenInvalidError, AuthError) as e:
        logger.warning("Auth verification failed: %s", e)
        raise _AUTH_401
    except Exception as e:
        logger.warning("Unexpected auth error: %s", e)
        raise _AUTH_401

    return user


def get_supabase(request: Request) -> SupabaseClient:
    """Return the service-role Supabase client (bypasses RLS). Use for data operations."""
    return request.app.state.supabase


def get_supabase_auth(request: Request) -> SupabaseClient:
    """Return the anon-key Supabase client for GoTrue auth operations.
    Separate from service-role client to prevent sign_in from polluting its session."""
    return request.app.state.supabase_auth


def get_redis(request: Request) -> Optional[AsyncRedis]:
    """
    Return the async Redis client from app state.
    May be None if Redis was unavailable at startup.
    """
    return getattr(request.app.state, "redis", None)
