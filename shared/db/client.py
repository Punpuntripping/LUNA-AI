"""
Supabase client factory.
Used by both backend (async) and agents (async).
Sync client available for scripts and migrations.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

from supabase import create_client, Client
from supabase._async.client import create_client as create_async_client, AsyncClient

from shared.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    """
    Sync Supabase client (singleton).
    Use for: scripts, migrations, one-off operations.
    Do NOT use in async FastAPI routes.
    """
    settings = get_settings()
    client = create_client(
        supabase_url=settings.SUPABASE_URL,
        supabase_key=settings.SUPABASE_SERVICE_KEY,  # Service role for backend operations
    )
    logger.info("Supabase sync client initialized")
    return client


@lru_cache(maxsize=1)
def get_admin_client() -> Client:
    """
    Alias for get_supabase_client().
    Returns a sync Supabase client using the service role key (bypasses RLS).
    """
    return get_supabase_client()


@lru_cache(maxsize=1)
def get_supabase_anon_client() -> Client:
    """
    Sync Supabase client with ANON key.
    Use for: operations that should respect RLS.
    """
    settings = get_settings()
    client = create_client(
        supabase_url=settings.SUPABASE_URL,
        supabase_key=settings.SUPABASE_ANON_KEY,
    )
    return client


async def get_async_supabase_client() -> AsyncClient:
    """
    Async Supabase client.
    Use for: FastAPI routes, async agent operations.

    NOTE: Not cached with lru_cache because async creation.
    Backend should create once during lifespan and store in app.state.
    """
    settings = get_settings()
    client = await create_async_client(
        supabase_url=settings.SUPABASE_URL,
        supabase_key=settings.SUPABASE_SERVICE_KEY,
    )
    logger.info("Supabase async client initialized")
    return client


async def get_async_supabase_anon_client() -> AsyncClient:
    """
    Async Supabase client with ANON key (respects RLS).
    """
    settings = get_settings()
    client = await create_async_client(
        supabase_url=settings.SUPABASE_URL,
        supabase_key=settings.SUPABASE_ANON_KEY,
    )
    return client


def get_user_client(access_token: str) -> Client:
    """
    Create a Supabase client authenticated as a specific user.
    Use for: operations that need to respect RLS for a specific user.

    Args:
        access_token: The user's JWT access token from Supabase Auth.
    """
    settings = get_settings()
    client = create_client(
        supabase_url=settings.SUPABASE_URL,
        supabase_key=settings.SUPABASE_ANON_KEY,
    )
    client.auth.set_session(access_token=access_token, refresh_token="")
    return client
