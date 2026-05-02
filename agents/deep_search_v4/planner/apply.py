"""Apply a :class:`PlannerOutput` onto a :class:`FullLoopDeps` instance.

The planner LLM emits a tiny schema (``invoke``, ``focus``, ``sectors``,
``rationale``). All concrete numbers — expander caps, reranker caps,
aggregator prompt key — are derived **here** in code via two tables:

- :data:`FOCUS_PROFILES` — per-executor mapping
  ``focus_level -> {expander_max_queries, reranker_max_high, reranker_max_medium}``.
  ``"default"`` mirrors the existing :class:`FullLoopDeps` defaults so a plan
  that picks ``default`` everywhere is byte-identical to the planner-disabled
  baseline.
- :data:`INVOKE_TO_AGG_PROMPT` — set of invoked executors (frozenset) → the
  registered aggregator prompt key (``prompt_reg_only``, ``prompt_1``, ...).

This separation keeps the planner prompt short and lets us re-tune the numeric
profile without touching the LLM.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .models import Executor, FocusLevel, PlannerOutput

if TYPE_CHECKING:
    from agents.deep_search_v4.orchestrator import FullLoopDeps

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Focus profiles — per-executor numeric caps for each focus level.
# ``default`` mirrors FullLoopDeps defaults; ``high`` widens, ``low`` narrows.
# Tunable without touching the planner prompt.
# ---------------------------------------------------------------------------

FOCUS_PROFILES: dict[Executor, dict[FocusLevel, dict[str, int]]] = {
    "reg": {
        "high":    {"expander_max_queries": 7, "reranker_max_high": 12, "reranker_max_medium": 6},
        "default": {"expander_max_queries": 5, "reranker_max_high": 8,  "reranker_max_medium": 4},
        "low":     {"expander_max_queries": 3, "reranker_max_high": 5,  "reranker_max_medium": 2},
    },
    "compliance": {
        "high":    {"expander_max_queries": 5, "reranker_max_high": 10, "reranker_max_medium": 5},
        "default": {"expander_max_queries": 3, "reranker_max_high": 6,  "reranker_max_medium": 4},
        "low":     {"expander_max_queries": 2, "reranker_max_high": 4,  "reranker_max_medium": 2},
    },
    "cases": {
        "high":    {"expander_max_queries": 4, "reranker_max_high": 10, "reranker_max_medium": 6},
        "default": {"expander_max_queries": 2, "reranker_max_high": 6,  "reranker_max_medium": 4},
        "low":     {"expander_max_queries": 1, "reranker_max_high": 4,  "reranker_max_medium": 2},
    },
}


# ---------------------------------------------------------------------------
# Invoke set -> aggregator prompt key. frozenset key, deterministic.
# ---------------------------------------------------------------------------

INVOKE_TO_AGG_PROMPT: dict[frozenset[Executor], str] = {
    frozenset({"reg"}):                            "prompt_reg_only",
    frozenset({"compliance"}):                    "prompt_comp_only",
    frozenset({"cases"}):                         "prompt_cases_only",
    frozenset({"compliance", "cases"}):           "prompt_cases_focus",
    frozenset({"reg", "compliance"}):             "prompt_1",
    frozenset({"reg", "cases"}):                  "prompt_1",
    frozenset({"reg", "compliance", "cases"}):    "prompt_1",
}


def derive_aggregator_prompt_key(plan: PlannerOutput) -> str:
    """Map ``plan.invoke`` (as a set) to a registered aggregator prompt key.

    Falls back to ``prompt_1`` (CRAC multi-source) for any invoke set the
    table doesn't list — keeps the pipeline robust if a future planner
    iteration adds a combination we forgot to register.
    """
    key = frozenset(plan.invoke)
    return INVOKE_TO_AGG_PROMPT.get(key, "prompt_1")


def _profile_for(executor: Executor, level: FocusLevel) -> dict[str, int]:
    """Return the numeric profile for ``(executor, focus_level)``.

    Defensive: an unknown focus value falls back to ``default`` rather than
    raising — the LLM might emit a stale literal once in a while.
    """
    by_level = FOCUS_PROFILES[executor]
    if level not in by_level:
        logger.warning(
            "planner: unknown focus=%r for executor=%s; falling back to 'default'",
            level, executor,
        )
        level = "default"
    return by_level[level]


def apply_plan_to_deps(
    deps: "FullLoopDeps",
    plan: PlannerOutput,
) -> "FullLoopDeps":
    """Overlay ``plan`` onto ``deps`` in-place and return ``deps``.

    Steps:
    1. Set ``include_*`` flags from ``plan.invoke`` (executors not invoked
       are turned OFF; previously-True flags for non-invoked executors are
       cleared).
    2. For each invoked executor, look up its focus profile and write
       ``*_max_high`` / ``*_max_medium`` + the per-executor entry in
       ``expander_max_queries``. Disabled executors keep whatever defaults
       the caller already set.
    3. Set ``sectors_override`` from ``plan.sectors``.
    4. Stash a ``_planner_plan`` reference for telemetry. The orchestrator
       reads :func:`derive_aggregator_prompt_key` separately to pick the
       prompt key — apply doesn't return it, keeping this function purely
       about deps state.
    """
    invoke_set = set(plan.invoke)
    deps.include_reg = "reg" in invoke_set
    deps.include_compliance = "compliance" in invoke_set
    deps.include_cases = "cases" in invoke_set

    # Build the per-executor expander cap dict explicitly. Only invoked
    # executors land in the dict — the orchestrator reads with .get(...).
    expander_caps: dict[str, int] = {}
    for executor in plan.invoke:
        prof = _profile_for(executor, plan.focus[executor])
        expander_caps[executor] = prof["expander_max_queries"]
        if executor == "reg":
            deps.reg_max_high = prof["reranker_max_high"]
            deps.reg_max_medium = prof["reranker_max_medium"]
        elif executor == "compliance":
            deps.compliance_max_high = prof["reranker_max_high"]
            deps.compliance_max_medium = prof["reranker_max_medium"]
        elif executor == "cases":
            deps.case_max_high = prof["reranker_max_high"]
            deps.case_max_medium = prof["reranker_max_medium"]
    deps.expander_max_queries = expander_caps

    deps.sectors_override = list(plan.sectors) if plan.sectors else None

    # RRF / score thresholds aren't planner-driven anymore; they remain
    # caller-set fields on FullLoopDeps. Don't touch them here.

    logger.info(
        "apply_plan_to_deps: invoke=%s focus=%s sectors=%s "
        "(reg=%s/%s, comp=%s/%s, cases=%s/%s, expander=%s)",
        sorted(plan.invoke),
        {k: plan.focus[k] for k in sorted(plan.focus)},
        plan.sectors,
        deps.reg_max_high, deps.reg_max_medium,
        deps.compliance_max_high, deps.compliance_max_medium,
        deps.case_max_high, deps.case_max_medium,
        expander_caps,
    )
    return deps


__all__ = [
    "FOCUS_PROFILES",
    "INVOKE_TO_AGG_PROMPT",
    "apply_plan_to_deps",
    "derive_aggregator_prompt_key",
]
