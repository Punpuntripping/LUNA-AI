"""Mode profiles + the pure ``build_retrieval_config`` function.

The planner LLM emits a tiny :class:`~.models.PlannerDecision` (mode + support).
Every concrete number — result budgets, aggregator prompt key — is derived
**here**, in code, from these tables. The LLM never sees a number, and the
planner no longer caps the expander's sub-query count.

The result-budget model (full spec: ``planning/MODE_PROFILES.md``):

- Each executor carries a ``result_budget`` (target total results) — **not** a
  fixed reranker keep, and **not** a cap on the expander's sub-query count.
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
    "base":    {"result_budget": 60},
    "support": {"result_budget": 30},
}


# ---------------------------------------------------------------------------
# Full mode — explicit, lower per-executor budgets. Three executors unioned
# would otherwise flood the aggregator, so 'full' does not use ROLE_PROFILES.
# ---------------------------------------------------------------------------

FULL_PROFILE: dict[str, dict[str, int]] = {
    "reg":        {"result_budget": 40},
    "cases":      {"result_budget": 25},
    "compliance": {"result_budget": 15},
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

    ``result_budget`` is keyed by executor name (``"reg"`` / ``"compliance"`` /
    ``"cases"``) and contains an entry only for *included* executors.

    Phase C: ``context_labels`` echoes ``PlannerDecision.context_labels`` here
    as a pass-through. The field is plumbed in Wave 2 but not yet consumed
    downstream — Phase D wires it into ``LoopState`` + ``AggregatorInput`` via
    ``ContextBlock`` objects rendered by ``run_retrieval``.
    """

    include_reg: bool
    include_compliance: bool
    include_cases: bool
    result_budget: dict[str, int]
    aggregator_prompt_key: str
    # ``sectors_override`` retained as a field for CLI / monitor smoke paths
    # that pre-set a static sector filter on ``FullLoopDeps``. In the
    # planner-driven loop the decider no longer picks sectors (Wave B —
    # moved to the parallel ``sector_picker`` agent), so ``run_retrieval``
    # always leaves this ``None`` and the picker future drives the filter.
    sectors_override: list[str] | None = None
    # Echoed for telemetry / logging — not consumed downstream.
    mode: Mode | None = None
    support: bool = False
    # Phase C — planner-emitted context label list (placeholder; consumed in
    # Phase D when run_retrieval builds ContextBlock objects from it).
    context_labels: list[str] = field(default_factory=list)


def build_retrieval_config(decision: PlannerDecision) -> RetrievalConfig:
    """Expand a :class:`PlannerDecision` into a concrete :class:`RetrievalConfig`.

    Pure function — no I/O, no side effects. See MODE_PROFILES.md §6.

    - Modes 1–3: the ``base`` executor always runs; the ``support`` executor
      runs iff ``decision.support`` is True. Budgets come from ``ROLE_PROFILES``
      by role.
    - ``full``: all three executors run as peers; ``decision.support`` is
      ignored (structural — 'full' has no support role). Budgets come from
      ``FULL_PROFILE`` per executor.
    """
    profile = MODE_PROFILES[decision.mode]
    result_budget: dict[str, int] = {}

    if decision.mode == "full":
        for executor in profile["executors"]:
            result_budget[executor] = FULL_PROFILE[executor]["result_budget"]
    else:
        base = profile["base"]
        result_budget[base] = ROLE_PROFILES["base"]["result_budget"]
        if decision.support:
            support = profile["support"]
            result_budget[support] = ROLE_PROFILES["support"]["result_budget"]

    included = set(result_budget)
    return RetrievalConfig(
        include_reg="reg" in included,
        include_compliance="compliance" in included,
        include_cases="cases" in included,
        result_budget=result_budget,
        aggregator_prompt_key=profile["aggregator_prompt_key"],
        sectors_override=None,  # Wave B — picker future drives the filter
        mode=decision.mode,
        support=False if decision.mode == "full" else decision.support,
        # Phase C — pass through the planner's label selection. Phase D will
        # consume these via ContextBlock objects in run_retrieval.
        context_labels=list(getattr(decision, "context_labels", []) or []),
    )


__all__ = [
    "MIN_EXPANDER_DIVISOR",
    "ROLE_PROFILES",
    "FULL_PROFILE",
    "MODE_PROFILES",
    "RetrievalConfig",
    "build_retrieval_config",
]
