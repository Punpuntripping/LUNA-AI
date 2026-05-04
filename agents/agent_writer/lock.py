"""Agent-write lock helper for ``workspace_items.locked_by_agent_until``.

Cut-1 acquired the lock once and immediately released it because the writer
ran the LLM in a single non-streaming call. Wave 8D adds a heartbeat-style
hold so token-by-token streaming can keep the column fresh while the agent
is mid-output.

Usage::

    async with agent_lock_scope(deps, item_id, ttl=30, refresh_every=10) as lock:
        async for token in stream:
            ...
        # exiting the scope releases the lock and emits workspace_item_unlocked.

If a refresh raises, the scope swallows the error -- a flaky DB write must
never break the user-visible stream. The caller is still notified via the
``workspace_item_locked`` event each time the column is extended (so the
frontend can display an accurate "Luna يحرر…" indicator).
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Callable

logger = logging.getLogger(__name__)


def _now_plus(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def write_lock_column(supabase, item_id: str, locked_until_iso: str | None) -> None:
    """Best-effort write of ``locked_by_agent_until``. Never raises."""
    if not item_id:
        return
    try:
        supabase.table("workspace_items").update(
            {"locked_by_agent_until": locked_until_iso}
        ).eq("item_id", item_id).execute()
    except Exception as exc:
        logger.warning(
            "agent_lock: locked_by_agent_until update on %s failed: %s",
            item_id, exc, exc_info=True,
        )


@asynccontextmanager
async def agent_lock_scope(
    *,
    supabase,
    item_id: str,
    emit: Callable[[dict], None],
    ttl_seconds: int = 30,
    refresh_every: int = 10,
) -> AsyncIterator[None]:
    """Hold an agent lock on ``item_id`` for the duration of the scope.

    On entry: writes ``locked_by_agent_until = now + ttl`` and emits
    ``workspace_item_locked``. While active: a background asyncio task
    refreshes the column every ``refresh_every`` seconds. On exit:
    clears the column and emits ``workspace_item_unlocked`` -- including
    when the body raises.

    Args:
        supabase: Sync Supabase client.
        item_id: workspace_items PK.
        emit: SSE event sink. Receives the locked / unlocked dicts.
        ttl_seconds: How far in the future ``locked_by_agent_until`` is set
            on each write. Should be >= ``refresh_every`` so a missed
            refresh does not flap the lock from the frontend's POV.
        refresh_every: Seconds between heartbeat refreshes.
    """
    if refresh_every <= 0 or refresh_every >= ttl_seconds:
        # Sanity: refresh must fire before the column expires.
        refresh_every = max(1, ttl_seconds // 3)

    locked_until_iso = _now_plus(ttl_seconds)
    write_lock_column(supabase, item_id, locked_until_iso)
    emit({
        "type": "workspace_item_locked",
        "item_id": item_id,
        "locked_until": locked_until_iso,
    })

    stop = asyncio.Event()

    async def _heartbeat() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=refresh_every)
                return  # stop signalled
            except asyncio.TimeoutError:
                # interval elapsed -- refresh the column
                pass
            new_iso = _now_plus(ttl_seconds)
            write_lock_column(supabase, item_id, new_iso)
            try:
                emit({
                    "type": "workspace_item_locked",
                    "item_id": item_id,
                    "locked_until": new_iso,
                })
            except Exception:
                logger.debug("agent_lock: emit failed on heartbeat", exc_info=True)

    task = asyncio.create_task(_heartbeat())
    try:
        yield
    finally:
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()
        except Exception:
            logger.debug("agent_lock: heartbeat task errored", exc_info=True)

        write_lock_column(supabase, item_id, None)
        try:
            emit({"type": "workspace_item_unlocked", "item_id": item_id})
        except Exception:
            logger.debug("agent_lock: emit failed on unlock", exc_info=True)
