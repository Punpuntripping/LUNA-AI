"""
Supabase client factory.
Used by both backend (async) and agents (async).
Sync client available for scripts and migrations.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

import httpx
from supabase import create_client, Client
from supabase._async.client import create_client as create_async_client, AsyncClient
from storage3._sync.client import SyncStorageClient

# supabase-py 2.28 exposes the options classes at supabase.lib.client_options; a
# future bump may relocate them. The SYNC client requires SyncClientOptions —
# the base ClientOptions has no `storage` field (auth session store) and makes
# create_client crash with AttributeError ('ClientOptions' object has no
# attribute 'storage'). Guard the import so the factories never break on the path.
try:
    from supabase.lib.client_options import SyncClientOptions
except Exception:  # noqa: BLE001
    from supabase import ClientOptions as SyncClientOptions

from shared.config import get_settings

logger = logging.getLogger(__name__)


# Railway <-> Supabase ap-south-1: intra-region RTT ~1-5ms (same region) or
# ~70-90ms cross-region. p99 PostgREST query well under 2s.
POSTGREST_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=15.0, pool=5.0)
# httpx read/write timeouts are PER socket operation (per chunk), not whole-
# transfer — 60s/op is generous even for the 50 MB upload/download paths.
STORAGE_TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=60.0, pool=5.0)

_LIMITS = httpx.Limits(
    max_connections=50,
    max_keepalive_connections=20,
    keepalive_expiry=30.0,
)


def _harden_sessions(client: Client) -> None:
    """Replace postgrest + storage httpx sessions with HTTP/1.1-only clients
    that carry explicit per-operation timeouts.

    Why HTTP/2 is disabled: deep_search v4 fans out reg/compliance/case search
    via asyncio.to_thread. The shared sync supabase client wraps a single
    httpx.Client; under HTTP/2 multiplexing, concurrent threaded writes overflow
    the send window and raise httpcore.WriteError (broken pipe). HTTP/1.1 with a
    connection pool avoids the multiplexing path entirely and is more forgiving
    under threaded sync concurrency.

    Why timeouts: supabase-py's defaults are 120s flat on postgrest / 20s flat on
    storage — functionally a hang. POSTGREST_TIMEOUT / STORAGE_TIMEOUT bound each
    socket op so a stalled connection fails fast instead of pinning a thread.
    """
    # postgrest: keep the proven session-swap (postgrest requests go via .session)
    try:
        old = client.postgrest.session
        client.postgrest.session = httpx.Client(
            base_url=old.base_url,
            headers=old.headers,
            timeout=POSTGREST_TIMEOUT,
            http2=False,
            limits=_LIMITS,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not harden postgrest session: %s", e)

    # storage: session-swap does NOT work — storage3's from_() binds _client at
    # __init__ (bucket proxies are built from self._client). Build a fresh
    # SyncStorageClient around our own httpx client and assign the lazy
    # property's backing field.
    try:
        client._storage = SyncStorageClient(
            url=str(client.storage_url),
            headers=client.options.headers,
            http_client=httpx.Client(
                timeout=STORAGE_TIMEOUT,
                http2=False,
                limits=_LIMITS,
                follow_redirects=True,
            ),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not harden storage session: %s", e)


def _client_options() -> SyncClientOptions:
    """Belt-and-suspenders timeouts on the ClientOptions level so that even if
    _harden_sessions degrades on a future supabase-py bump, the lazy postgrest /
    storage clients are still constructed with bounded timeouts.
    """
    return SyncClientOptions(
        postgrest_client_timeout=httpx.Timeout(
            connect=5.0, read=15.0, write=15.0, pool=5.0
        ),
        storage_client_timeout=60,  # storage_client_timeout is int seconds only
    )


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
        options=_client_options(),
    )
    _harden_sessions(client)
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
        options=_client_options(),
    )
    # The anon client serves GoTrue auth in app.state.supabase_auth and was
    # previously unhardened — harden it too so auth/data calls share the bounded
    # timeout + HTTP/1.1 profile.
    _harden_sessions(client)
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
