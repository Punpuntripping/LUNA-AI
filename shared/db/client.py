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
# ~70-90ms cross-region. p99 PostgREST query well under 2s for ordinary CRUD.
#
# read/write RAISED 15.0 -> 25.0 on 2026-08-22. The 15s ceiling was CLIPPING
# LEGITIMATE WORK: deep_search's vector RPCs (`search_topics`,
# `search_case_topics`) were measured finishing at 10.3-13.4s under a 9-wide
# fan-out on 2026-08-18/20/21 — inside the timeout, but with almost no margin.
# On 2026-08-22 a 16-wide fan-out pushed the batch past 15s and all 16 calls
# died at exactly 15.0s, taking the entire retrieval phase (and the turn's
# sources) with them. 25s restores headroom over the observed worst case.
#
# This is only safe BECAUSE of the fan-out cap in
# ``agents/deep_search_v4/shared/db_gate.py``. Ordering matters: raising the
# timeout WITHOUT the admission cap does not fix anything — it converts fast
# failures into slow ones while pinning `asyncio.to_thread` worker threads and
# pooled connections for 10 extra seconds each, which deepens the very
# contention that caused the overrun. Cap admission first, then extend the
# timeout to cover a capped batch. Do not raise this further to "fix" a
# timeout; a timeout above the cap means the cap is too high.
POSTGREST_TIMEOUT = httpx.Timeout(connect=5.0, read=25.0, write=25.0, pool=5.0)
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

    ``auto_refresh_token=False`` + ``persist_session=False`` because the backend
    is STATELESS about user sessions — it verifies JWTs with PyJWT and hands
    tokens to the browser, which owns rotation from then on. With the defaults
    (both True), every ``sign_in_with_password`` on the shared auth client
    parked that login's session AND armed a gotrue ``threading.Timer`` at
    ``expires_at − EXPIRY_MARGIN`` (login + 3590s). When it fired, it replayed
    the PARKED refresh token — long since rotated away by the browser — and
    GoTrue's reuse-detection revoked the entire session family, force-logging
    the user out exactly one hour after login (measured 3589–3591s across 29
    sessions in auth.refresh_tokens; the ±1s precision is the unthrottled
    server-side timer). Each later /login re-parked the shared client and
    cancelled the previous timer, which is why the kill looked sporadic: only
    the LAST login of the hour on a backend process died.
    """
    return SyncClientOptions(
        # Reference the module constant rather than a second literal: this used
        # to hardcode its own copy of connect=5/read=15/write=15/pool=5, so the
        # 2026-08-22 read-timeout bump had to be applied in two places or the
        # belt would silently keep enforcing the old ceiling the suspenders had
        # just relaxed. One source of truth — see POSTGREST_TIMEOUT above.
        postgrest_client_timeout=POSTGREST_TIMEOUT,
        storage_client_timeout=60,  # storage_client_timeout is int seconds only
        auto_refresh_token=False,
        persist_session=False,
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


def create_isolated_anon_client() -> Client:
    """Fresh, UNCACHED anon client for a one-off GoTrue password check.

    ``sign_in_with_password`` parks the resulting session in the client's own
    in-memory auth storage, and ``auth.sign_out()`` acts on whatever session is
    parked there (gotrue_client.py:789). ``get_supabase_anon_client()`` is an
    lru_cached singleton shared by every request, so re-verifying a password on
    it would overwrite another request's parked session. Sensitive re-auth
    (change-password, delete-account) gets its own throwaway client instead.

    Not hardened: ``postgrest``/``storage`` are lazy properties, so an auth-only
    client opens exactly one HTTP client. Callers MUST ``client.auth.close()``.
    """
    settings = get_settings()
    return create_client(
        supabase_url=settings.SUPABASE_URL,
        supabase_key=settings.SUPABASE_ANON_KEY,
        options=_client_options(),
    )


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
