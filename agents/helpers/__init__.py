"""Helper frame: background helpers, critical / not critical.

Plan: ``.claude/plans/helper_frame.md``. Behind ``HELPER_FRAME_ENABLED``.

* ``spec.py``     — :class:`HelperSpec`, the ``Consumer`` names, :class:`Readiness`.
* ``registry.py`` — the matrix as code (the single source of truth).
* ``turn.py``     — :class:`TurnHelpers`, one per user message.
* ``status.py``   — across turns and processes: stored status + lease
  (``helper_runs``), and :func:`ensure_ready`, the call a deep consumer makes.

A deep consumer (deep-search ``run_retrieval``, the writer's walkers) asks for an
item helper's result with::

    from agents.helpers import ensure_ready

    readiness = await ensure_ready(
        "segmenter", item_ids, consumer="aggregator",
        supabase=deps.supabase, user_id=deps.user_id, on_wait=show_status_line,
    )
    # {item_id: Readiness(status="ready" | "fallback" | "not_applicable", ...)}
"""
from __future__ import annotations

from ._runtime import MESSAGE_KEY, HelperOutcome, current_turn
from .registry import REGISTRY, get_spec, register, topo_order, unregister
from .spec import CONSUMERS, Consumer, HelperContext, HelperSpec, Readiness
from .status import (
    ON_WAIT_AFTER_S,
    WAIT_STATUS_TEXT,
    HelperRunStore,
    RunRow,
    SupabaseHelperRunStore,
    ensure_ready,
)
from .turn import TurnHelpers, TurnReadiness, bind_turn, unbind_turn

__all__ = [
    "CONSUMERS",
    "Consumer",
    "HelperContext",
    "HelperOutcome",
    "HelperRunStore",
    "HelperSpec",
    "MESSAGE_KEY",
    "ON_WAIT_AFTER_S",
    "REGISTRY",
    "Readiness",
    "RunRow",
    "SupabaseHelperRunStore",
    "TurnHelpers",
    "TurnReadiness",
    "WAIT_STATUS_TEXT",
    "bind_turn",
    "current_turn",
    "ensure_ready",
    "get_spec",
    "register",
    "topo_order",
    "unbind_turn",
    "unregister",
]
