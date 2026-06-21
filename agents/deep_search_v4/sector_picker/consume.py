"""Consumer-side resolution of the shared sector_picker future.

The sector_picker runs as ONE shared background task (a single future) spawned
in ``run_retrieval``, concurrently with the executors. Each executor awaits the
result at its own sector-filter join point (post-RPC for reg/case, pre-RPC for
compliance — see ``orchestrator._spawn_sector_picker_task``).

The picker itself has **no internal hard timeout**. Instead each consumer grants
it a bounded *grace* measured from the moment that consumer actually needs the
filter — i.e. after that executor's essential expander + embed (+ RPC) work,
which the picker has been running in the shadow of. If the picker has not
resolved within the grace, the consumer proceeds **unfiltered** — a strictly
safe degradation, since the sector filter is a coarse pre-filter on top of
semantic retrieval — *without* cancelling the picker. A slower executor may
still consume its result; the task is finally reaped by ``run_full_loop`` once
retrieval ends.

Why ``asyncio.shield`` is mandatory: the future is shared. A plain
``wait_for(future, grace)`` would cancel the underlying picker on *this*
consumer's timeout, starving every other executor. ``shield`` lets this consumer
stop waiting without killing the picker.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# Grace one executor grants the picker, measured from when it reaches its
# sector-filter join point (after its essential expander/embed work). Because
# the picker runs in the shadow of that work, this is the *additional* latency
# it can add to the critical path in the worst case — never the picker's full
# runtime. Tuned from observed picker latencies (~10s avg, ~18s p100).
SECTOR_PICKER_GRACE_S: float = 10.0


async def resolve_sector_filter(
    future: "asyncio.Future[list[str] | None] | None",
    *,
    grace_s: float = SECTOR_PICKER_GRACE_S,
    label: str = "",
) -> list[str] | None:
    """Resolve the shared sector_picker future at an executor join point.

    Returns the canonical sector list when the picker resolves within
    ``grace_s``, or ``None`` (run unfiltered) on grace-timeout / picker error /
    null pick. Never raises (a real cancellation of the *calling* coroutine
    still propagates) and never cancels the picker task.

    Args:
        future: the shared picker future, or ``None`` (static / CLI paths) —
            ``None`` short-circuits to ``None`` (no filter).
        grace_s: max additional wait at this join point. Defaults to
            :data:`SECTOR_PICKER_GRACE_S`.
        label: short tag for the log line when the grace elapses (e.g. the
            sub-query text).
    """
    if future is None:
        return None
    try:
        # shield: don't let our timeout cancel the shared picker.
        picked = await asyncio.wait_for(asyncio.shield(future), timeout=grace_s)
    except asyncio.TimeoutError:
        logger.info(
            "sector_picker: grace %.0fs elapsed before resolve (%s) — "
            "running unfiltered",
            grace_s, label or "?",
        )
        return None
    except Exception as exc:  # picker raised internally — degrade to unfiltered
        logger.warning(
            "sector_picker: future raised %s (%s) — running unfiltered",
            type(exc).__name__, label or "?",
        )
        return None
    return list(picked) if picked else None


__all__ = ["SECTOR_PICKER_GRACE_S", "resolve_sector_filter"]
