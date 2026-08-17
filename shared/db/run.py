"""Run sync Supabase service functions off the event loop."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, TypeVar

import httpx

T = TypeVar("T")

logger = logging.getLogger(__name__)


class DbDeadlineExceeded(Exception):
    """Outer wall-clock deadline hit. Backend maps this to 503 SERVICE_UNAVAILABLE."""


async def run_db(fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Route-layer wrapper: await run_db(service.fn, supabase, auth_id, ...).

    Propagates exceptions (incl. LunaHTTPException) unchanged.
    Contextvars (Logfire trace context) flow into the thread (asyncio.to_thread
    uses contextvars.copy_context internally).
    """
    return await asyncio.to_thread(fn, *args, **kwargs)


async def run_db_deadline(
    deadline_s: float, fn: Callable[..., T], /, *args: Any, **kwargs: Any
) -> T:
    """run_db with an outer deadline.

    WARNING: cancellation does not kill the thread — it runs until httpx's own
    timeout fires. Keep deadline_s >= the httpx per-request total (~20s) or
    threads pile up during an outage.
    """
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn, *args, **kwargs), deadline_s)
    except asyncio.TimeoutError:
        raise DbDeadlineExceeded(getattr(fn, "__name__", str(fn)))


# Gateway/PostgREST statuses that mean "this attempt died in transit", never
# "the database rejected your data". Matched as text because postgrest-py raises
# a generic APIError whose shape has changed between versions — same reasoning
# as payment_service._is_unique_violation.
_TRANSIENT_MARKERS = (
    "502",
    "503",
    "504",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "server disconnected",
    "connection reset",
)


def is_transient_db_error(exc: BaseException) -> bool:
    """Is this failure worth another attempt?

    True for transport-level deaths — every ``httpx.TransportError`` subclass:
    pool-acquire timeouts (the shared sync client caps at 50 connections with a
    5s pool timeout, and deep_search fans out through it via ``to_thread``),
    read/write timeouts, connection resets, half-closed keepalive sockets — plus
    5xx from PostgREST or the gateway in front of it.

    False for anything the database *decided*: constraint violations, RLS
    denials, 4xx. Those are deterministic — retrying only reproduces them more
    slowly and hides the real error behind a longer stall.
    """
    if isinstance(exc, httpx.TransportError):
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


async def run_db_retry(
    fn: Callable[..., T],
    /,
    *args: Any,
    attempts: int = 3,
    backoff_s: float = 0.25,
    **kwargs: Any,
) -> T:
    """``run_db`` that retries TRANSIENT transport failures.

    CALLER CONTRACT — ``fn`` MUST be idempotent. A retry cannot know whether a
    timed-out attempt actually landed server-side before its response was lost,
    so anything passed here has to tolerate being applied twice. The usual
    shape: generate the primary key client-side and swallow the resulting
    unique-violation on the second pass (see
    ``message_service._insert_turn_rows``).

    Only transport-level failures are retried (see ``is_transient_db_error``); a
    constraint violation or 4xx propagates immediately from attempt 1.

    Backoff is linear and short — 0.25s then 0.5s by default — because callers
    are on the request path with a user waiting. This exists to survive a single
    dropped connection, not to ride out an outage.
    """
    for attempt in range(1, attempts + 1):
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except Exception as e:  # noqa: BLE001
            if attempt >= attempts or not is_transient_db_error(e):
                raise
            logger.warning(
                "run_db_retry: %s failed on attempt %d/%d (%s: %s) — retrying",
                getattr(fn, "__name__", str(fn)),
                attempt,
                attempts,
                type(e).__name__,
                e,
            )
            await asyncio.sleep(backoff_s * attempt)
    raise AssertionError("unreachable: loop either returns or raises")
