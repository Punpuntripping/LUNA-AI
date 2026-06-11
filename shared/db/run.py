"""Run sync Supabase service functions off the event loop."""
from __future__ import annotations

import asyncio
from typing import Any, Callable, TypeVar

T = TypeVar("T")


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
