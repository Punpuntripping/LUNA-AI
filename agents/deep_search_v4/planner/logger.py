"""Event emission for the planner runner.

Mirrors the executor packages' logger convention: the planner appends every
lifecycle event onto ``deps._events`` (read back by the orchestrator + monitor)
and, when an ``emit_sse`` callback is present, fires it too.

The planner is a control agent, not a retrieval executor — it has no on-disk
``run.json`` dump. It only emits in-memory events. ``run_retrieval`` produces
the heavy per-phase logs via the executor loggers.

Event payload reference (built by ``runner.py``; this module only defines the
event-name constants):

- ``EVENT_DECIDED`` — emitted after phase 1 (both fresh dispatch + resume).
  Payload keys:
    * ``mode`` (str) — chosen :class:`~.models.Mode`
    * ``support`` (bool)
    * ``sectors`` (list[str])
    * ``planner_brief_chars`` (int) — Phase C: length of decision.planner_brief
    * ``context_labels`` (list[str]) — Phase C: decision.context_labels
    * ``workspace_reads_count`` (int) — Phase C: count of `unfold_workspace_item`
      tool_call events emitted onto deps._events during this turn
    * ``ask_user_invoked`` (bool) — Phase C: True iff the decider called
      ask_user (the run paused; this branch is reachable only on resume)
    * ``duration_s`` (float) — wall-clock seconds for phase 1
    * ``resumed`` (bool, optional) — present iff this is a resume re-emit
- ``EVENT_PAUSED`` — emitted when phase 1 paused via ``ask_user``.
- ``EVENT_RETRIEVAL_DONE`` — emitted after phase 2.
- ``EVENT_RESPONDED`` — emitted after phase 3.
  Payload keys:
    * ``build_artifact`` (bool) — Phase E: ``PlannerResponse.build_artifact``,
      the orchestrator's publish-gate branch input. False on empty-results or
      prior-artifact-covers; True otherwise.
    * ``referenced_item_id`` (str | None) — Phase E: when
      ``build_artifact=False`` because a prior artifact covers the question,
      the ``item_id`` of that prior artifact (used by the orchestrator to emit
      the ``referenced_existing_item`` SSE event). Null otherwise.
    * ``duration_s`` (float) — wall-clock seconds for phase 3.
  Phase E note: the legacy ``suggested_action`` key has been REMOVED from this
  payload (§3.8 / §9 O4 — promoted to decided) along with the field on
  ``PlannerResponse``.
- ``EVENT_ERROR`` — emitted on any phase exception.

The ``unfold_workspace_item`` tool itself emits ``{"type": "tool_call", "tool":
"unfold_workspace_item", "item_id": …}`` items onto ``deps._events`` (NOT a
lifecycle event — a tool-call audit record). The runner counts these to derive
``workspace_reads_count`` (the legacy ``read_workspace_item`` name is still
tallied for historical events).
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
