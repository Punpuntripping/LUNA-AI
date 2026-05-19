"""Mode profiles + the pure ``build_retrieval_config`` function.

The planner LLM emits a tiny :class:`~.models.PlannerDecision` (mode + support +
sectors). Every concrete number — expander caps, result budgets, aggregator
prompt key — is derived **here**, in code, from three tables. The LLM never
sees a number.

The result-budget model (full spec: ``planning/MODE_PROFILES.md``):

- Each executor carries an ``expander_max_queries`` ceiling and a
  ``result_budget`` (target total results) — **not** a fixed reranker keep.
- The per-sub-query reranker keep is computed *inside each executor's loop* at
  runtime: ``ceil(result_budget / max(N, MIN_EXPANDER_DIVISOR))`` where ``N`` is
  the expander's actual emitted query count. ``build_retrieval_config`` does not
  compute it — only the loop knows ``N``.

This module is **pure** — only ``pydantic`` (via ``.models``) is imported, never
``pydantic_ai`` or an executor package, so the apply tier of the test suite
runs without the agent runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import Mode, PlannerDecision


# Divisor floor for the dynamic-keep formula. Documented in MODE_PROFILES.md §1;
# re-exported so the executor loops import the single source of truth.
MIN_EXPANDER_DIVISOR = 3


# ---------------------------------------------------------------------------
# Role-based budgets — modes 1–3 (case_led / reg_led / compliance_led).
# The budget depends on the executor's ROLE, not on which executor it is.
# ---------------------------------------------------------------------------

ROLE_PROFILES: dict[str, dict[str, int]] = {
    "base":    {"expander_max_queries": 10, "result_budget": 60},
    "support": {"expander_max_queries": 6,  "result_budget": 30},
}


# ---------------------------------------------------------------------------
# Full mode — explicit, lower per-executor budgets. Three executors unioned
# would otherwise flood the aggregator, so 'full' does not use ROLE_PROFILES.
# ---------------------------------------------------------------------------

FULL_PROFILE: dict[str, dict[str, int]] = {
    "reg":        {"expander_max_queries": 7, "result_budget": 40},
    "cases":      {"expander_max_queries": 4, "result_budget": 25},
    "compliance": {"expander_max_queries": 3, "result_budget": 15},
}


# ---------------------------------------------------------------------------
# Mode -> executor roles + aggregator prompt key. Single source of truth.
# ---------------------------------------------------------------------------

MODE_PROFILES: dict[Mode, dict] = {
    "case_led": {
        "base": "cases", "support": "reg",
        "aggregator_prompt_key": "prompt_mode_case",
    },
    "reg_led": {
        "base": "reg", "support": "compliance",
        "aggregator_prompt_key": "prompt_mode_reg",
    },
    "compliance_led": {
        "base": "compliance", "support": "reg",
        "aggregator_prompt_key": "prompt_mode_compliance",
    },
    "full": {
        "executors": ["reg", "cases", "compliance"],   # all base-equivalent peers
        "aggregator_prompt_key": "prompt_mode_full",
    },
}


@dataclass
class RetrievalConfig:
    """Mode-derived retrieval knobs — the output of :func:`build_retrieval_config`.

    Plain dataclass, no heavy imports — stays in the pure layer. ``run_retrieval``
    reads it to assemble the internal ``FullLoopDeps``.

    ``expander_max_queries`` and ``result_budget`` are keyed by executor name
    (``"reg"`` / ``"compliance"`` / ``"cases"``) and contain an entry only for
    *included* executors.
    """

    include_reg: bool
    include_compliance: bool
    include_cases: bool
    expander_max_queries: dict[str, int]
    result_budget: dict[str, int]
    aggregator_prompt_key: str
    sectors_override: list[str] | None = None
    # Echoed for telemetry / logging — not consumed downstream.
    mode: Mode | None = None
    support: bool = False


def build_retrieval_config(decision: PlannerDecision) -> RetrievalConfig:
    """Expand a :class:`PlannerDecision` into a concrete :class:`RetrievalConfig`.

    Pure function — no I/O, no side effects. See MODE_PROFILES.md §6.

    - Modes 1–3: the ``base`` executor always runs; the ``support`` executor
      runs iff ``decision.support`` is True. Caps come from ``ROLE_PROFILES``
      by role.
    - ``full``: all three executors run as peers; ``decision.support`` is
      ignored (structural — 'full' has no support role). Caps come from
      ``FULL_PROFILE`` per executor.
    """
    profile = MODE_PROFILES[decision.mode]
    expander_max_queries: dict[str, int] = {}
    result_budget: dict[str, int] = {}

    if decision.mode == "full":
        for executor in profile["executors"]:
            caps = FULL_PROFILE[executor]
            expander_max_queries[executor] = caps["expander_max_queries"]
            result_budget[executor] = caps["result_budget"]
    else:
        base = profile["base"]
        base_caps = ROLE_PROFILES["base"]
        expander_max_queries[base] = base_caps["expander_max_queries"]
        result_budget[base] = base_caps["result_budget"]
        if decision.support:
            support = profile["support"]
            support_caps = ROLE_PROFILES["support"]
            expander_max_queries[support] = support_caps["expander_max_queries"]
            result_budget[support] = support_caps["result_budget"]

    included = set(expander_max_queries)
    return RetrievalConfig(
        include_reg="reg" in included,
        include_compliance="compliance" in included,
        include_cases="cases" in included,
        expander_max_queries=expander_max_queries,
        result_budget=result_budget,
        aggregator_prompt_key=profile["aggregator_prompt_key"],
        sectors_override=list(decision.sectors) if decision.sectors else None,
        mode=decision.mode,
        support=False if decision.mode == "full" else decision.support,
    )


__all__ = [
    "MIN_EXPANDER_DIVISOR",
    "ROLE_PROFILES",
    "FULL_PROFILE",
    "MODE_PROFILES",
    "RetrievalConfig",
    "build_retrieval_config",
]
