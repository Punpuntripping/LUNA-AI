"""Deep-Search v4 Planner — planner-driven two-phase retrieval loop.

The planner owns the loop. It runs in two LLM phases around a plain-Python
retrieval pass:

1. **decide** — ``planner_decider`` picks a :class:`PlannerDecision` (one of four
   modes + optional support), or pauses via ``ask_user`` for a vague query.
2. **retrieve** — ``run_retrieval`` runs the executors → URA → aggregator.
3. **respond** — ``planner_responder`` writes the :class:`PlannerResponse` —
   the user-facing chat summary + a next-step suggestion.

``handle_planner_turn`` is the single convergence point for phases 2–3; both
fresh dispatch and pause-resume enter it. See ``planning/PLANNER_REDESIGN_PLAN.md``.

Public surface:
    - :class:`PlannerDecision` / :class:`PlannerResponse` — phase schemas.
    - :data:`Mode` — the four-mode literal.
    - :data:`MODE_PROFILES` / :func:`build_retrieval_config` /
      :class:`RetrievalConfig` — pure mode → caps derivation.
    - :class:`PlannerDeps` / :func:`build_planner_deps` — phase 2–3 runtime deps.
    - :func:`create_planner_decider` / :func:`create_planner_responder` — agent
      factories; :func:`handle_planner_turn` — the two-phase runner.

``models``, ``apply`` and ``deps`` are pure (no ``pydantic_ai``); ``agent`` and
``runner`` are imported lazily so the package stays usable for unit tests that
don't have the agent runtime installed.
"""
from __future__ import annotations

from .models import Mode, PlannerDecision, PlannerResponse, SuggestedAction
from .apply import (
    FULL_PROFILE,
    MIN_EXPANDER_DIVISOR,
    MODE_PROFILES,
    ROLE_PROFILES,
    RetrievalConfig,
    build_retrieval_config,
)
from .deps import PlannerDeps, build_planner_deps

# Optional imports — agent.py / runner.py depend on pydantic_ai +
# agents.model_registry. Mirror the aggregator package's lazy pattern so
# models / apply / deps stay importable without those deps.
try:
    from .agent import (  # type: ignore[attr-defined]
        PLANNER_DECIDER_LIMITS,
        PLANNER_RESPONDER_LIMITS,
        create_planner_decider,
        create_planner_responder,
    )
except ImportError:  # pragma: no cover - construction-time only
    PLANNER_DECIDER_LIMITS = None  # type: ignore[assignment]
    PLANNER_RESPONDER_LIMITS = None  # type: ignore[assignment]
    create_planner_decider = None  # type: ignore[assignment]
    create_planner_responder = None  # type: ignore[assignment]

try:
    from .runner import PlannerTurnResult, handle_planner_turn  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - construction-time only
    PlannerTurnResult = None  # type: ignore[assignment,misc]
    handle_planner_turn = None  # type: ignore[assignment]


__all__ = [
    # schemas
    "Mode",
    "SuggestedAction",
    "PlannerDecision",
    "PlannerResponse",
    # apply / caps
    "MODE_PROFILES",
    "ROLE_PROFILES",
    "FULL_PROFILE",
    "MIN_EXPANDER_DIVISOR",
    "RetrievalConfig",
    "build_retrieval_config",
    # deps
    "PlannerDeps",
    "build_planner_deps",
    # agents / runner (lazy)
    "create_planner_decider",
    "create_planner_responder",
    "PLANNER_DECIDER_LIMITS",
    "PLANNER_RESPONDER_LIMITS",
    "handle_planner_turn",
    "PlannerTurnResult",
]
