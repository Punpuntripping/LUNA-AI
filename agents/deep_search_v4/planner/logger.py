"""Event emission for the planner runner.

Mirrors the executor packages' logger convention: the planner appends every
lifecycle event onto ``deps._events`` (read back by the orchestrator + monitor)
and, when an ``emit_sse`` callback is present, fires it too.

The planner is a control agent, not a retrieval executor — it has no on-disk
``run.json`` dump. It only emits in-memory events. ``run_retrieval`` produces
the heavy per-phase logs via the executor loggers.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# Planner lifecycle event names — kept as constants so the monitor and tests
# reference one source of truth.
EVENT_DECIDED = "planner_decided"
EVENT_PAUSED = "planner_paused"
EVENT_RETRIEVAL_DONE = "planner_retrieval_done"
EVENT_RESPONDED = "planner_responded"
EVENT_ERROR = "planner_error"


def emit(deps: Any, event: dict) -> None:
    """Append ``event`` to ``deps._events`` and fire ``deps.emit_sse`` if set."""
    try:
        deps._events.append(event)
    except Exception:  # pragma: no cover - defensive
        logger.debug("planner.emit: _events append failed", exc_info=True)
    callback = getattr(deps, "emit_sse", None)
    if callback is not None:
        try:
            callback(event)
        except Exception:  # pragma: no cover - defensive
            logger.warning("planner.emit: emit_sse callback raised", exc_info=True)


__all__ = [
    "emit",
    "EVENT_DECIDED",
    "EVENT_PAUSED",
    "EVENT_RETRIEVAL_DONE",
    "EVENT_RESPONDED",
    "EVENT_ERROR",
]
