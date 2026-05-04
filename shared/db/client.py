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


def _harden_postgrest_session(client: Client) -> None:
    """Replace the postgrest httpx session with an HTTP/1.1-only client.

    Why: deep_search v4 fans out reg/compliance/case search via asyncio.to_thread.
    The shared sync supabase client wraps a single httpx.Client; under HTTP/2
    multiplexing, concurrent threaded writes overflow the send window and
    raise httpcore.WriteError (broken pipe). HTTP/1.1 with a connection pool
    avoids the multiplexing path entirely and is more forgiving under
    threaded sync concurrency.
    """
    try:
        import httpx
        old = client.postgrest.session
        client.postgrest.session = httpx.Client(
            base_url=old.base_url,
            headers=old.headers,
            timeout=old.timeout,
            http2=False,
            limits=httpx.Limits(
                max_connections=50,
                max_keepalive_connections=20,
                keepalive_expiry=30.0,
            ),
        )
    except Exception as e:
        logger.warning("Could not harden postgrest session: %s", e)


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
    _harden_postgrest_session(client)
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
