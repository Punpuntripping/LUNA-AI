"""
FastAPI dependency injection functions.
Used with Depends() in route handlers.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from shared.auth.jwt import AuthUser, verify_request, AuthError
from supabase import Client as SupabaseClient
from redis.asyncio import Redis as AsyncRedis

logger = logging.getLogger(__name__)

# HTTPBearer extracts the Authorization header automatically.
# auto_error=False so we can return Arabic error messages.
_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> AuthUser:
    """
    Validate the JWT from the Authorization header and return the authenticated user.

    Raises:
        HTTPException 401 with Arabic message if token is missing/invalid/expired.
    """
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="بيانات الدخول غير صحيحة",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user = verify_request(f"Bearer {credentials.credentials}")
        return user
    except AuthError as e:
        logger.warning("Auth failed: %s", e.message)
        raise HTTPException(
            status_code=e.status_code,
            detail="بيانات الدخول غير صحيحة",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_supabase(request: Request) -> SupabaseClient:
    """Return the Supabase client from app state (initialized at startup)."""
    return request.app.state.supabase


def get_redis(request: Request) -> Optional[AsyncRedis]:
    """
    Return the async Redis client from app state.
    May be None if Redis was unavailable at startup.
    """
    return getattr(request.app.state, "redis", None)
