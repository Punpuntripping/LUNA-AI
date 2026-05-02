"""Deep-Search v4 Planner agent — single-shot mode + caps + sectors decider.

The planner runs once at the front of the v4 pipeline (before the parallel
reg / compliance / case executors) and emits a structured :class:`PlannerOutput`
that the orchestrator overlays onto :class:`FullLoopDeps` via
:func:`apply_plan_to_deps`.

Public surface:
    - :class:`PlannerOutput` — structured plan schema (see V4_PLANNER_DESIGN.md §4.1).
    - :class:`PlannerDeps` — minimal runtime deps (model override + event sink).
    - :func:`run_planner` — async one-shot runner with degraded fallback.
    - :func:`apply_plan_to_deps` — pure-ish overlay onto FullLoopDeps.
    - :func:`create_planner_agent` — Pydantic AI factory (no deps_type).

Lazy imports for ``agent.py`` / ``runner.py`` follow the aggregator package
convention so ``models`` + ``apply`` remain usable when pydantic_ai or the
model registry isn't importable (e.g. lightweight unit tests).
"""
from __future__ import annotations

from .models import Executor, FocusLevel, PlannerDeps, PlannerOutput
from .apply import (
    FOCUS_PROFILES,
    INVOKE_TO_AGG_PROMPT,
    apply_plan_to_deps,
    derive_aggregator_prompt_key,
)
from .prompts import PLANNER_SYSTEM_PROMPT, build_planner_user_message

# Optional imports — agent.py / runner.py depend on pydantic_ai +
# agents.model_registry. Mirror aggregator/__init__.py's lazy pattern so
# the package stays importable in environments without those deps.
try:
    from .agent import create_planner_agent  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - construction-time only
    create_planner_agent = None  # type: ignore[assignment]

try:
    from .runner import run_planner  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - construction-time only
    run_planner = None  # type: ignore[assignment]


__all__ = [
    "Executor",
    "FocusLevel",
    "PlannerOutput",
    "PlannerDeps",
    "FOCUS_PROFILES",
    "INVOKE_TO_AGG_PROMPT",
    "run_planner",
    "apply_plan_to_deps",
    "derive_aggregator_prompt_key",
    "create_planner_agent",
    "PLANNER_SYSTEM_PROMPT",
    "build_planner_user_message",
]
