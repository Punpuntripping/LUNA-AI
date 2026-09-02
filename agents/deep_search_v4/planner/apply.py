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
# Role-based budgets — modes 1–2 (case_led / reg_compliance_led).
# The budget depends on the executor's ROLE, not on which executor it is.
# ---------------------------------------------------------------------------

ROLE_PROFILES: dict[str, dict[str, int]] = {
    "base":    {"result_budget": 60},
    "support": {"result_budget": 30},
}


# ---------------------------------------------------------------------------
# Full mode — explicit, lower per-executor budgets. Two executors unioned
# would otherwise flood the aggregator, so 'full' does not use ROLE_PROFILES.
# ---------------------------------------------------------------------------

FULL_PROFILE: dict[str, dict[str, int]] = {
    "reg_compliance": {"result_budget": 40},
    "cases":      {"result_budget": 25},
}


# ---------------------------------------------------------------------------
# Mode -> executor roles + aggregator prompt key. Single source of truth.
# ---------------------------------------------------------------------------

MODE_PROFILES: dict[Mode, dict] = {
    "case_led": {
        "base": "cases", "support": "reg_compliance",
        "aggregator_prompt_key": "prompt_mode_case",
    },
    "reg_compliance_led": {
        "base": "reg_compliance", "support": "cases",
        "aggregator_prompt_key": "prompt_mode_reg_compliance",
    },
    "full": {
        "executors": ["reg_compliance", "cases"],   # both base-equivalent peers
        "aggregator_prompt_key": "prompt_mode_full",
    },
}


# ---------------------------------------------------------------------------
# Editorial twins — in-app aggregator prompt key -> public-blog article key.
# ---------------------------------------------------------------------------
#
# The editorial path (public blog wing — .claude/plans/blog_subjects.md §6)
# runs the SAME retrieval as the in-app path and differs only in the
# aggregator's rhetoric: the in-app prompt answers a lawyer who asked and is
# waiting, the editorial twin writes an article for a stranger who arrived
# from a search engine. Same mode body, same citation rules, so one twin per
# mode rather than one editorial prompt for all three — otherwise pinning the
# mode would select mode-specific guidance that the prompt then discards.
#
# Every value here is asserted to exist in ``AGGREGATOR_PROMPTS`` by
# ``aggregator/tests/test_prompts_mode_body_identity.py``. The check lives
# there, not here: this module stays import-pure (no aggregator import) so the
# apply tier runs without the agent runtime.

EDITORIAL_PROMPT_KEYS: dict[str, str] = {
    "prompt_mode_case": "prompt_editorial_case",
    "prompt_mode_reg_compliance": "prompt_editorial_reg_compliance",
    "prompt_mode_full": "prompt_editorial_full",
}


@dataclass
class RetrievalConfig:
    """Mode-derived retrieval knobs — the output of :func:`build_retrieval_config`.

    Plain dataclass, no heavy imports — stays in the pure layer. ``run_retrieval``
    reads it to assemble the internal ``FullLoopDeps``.

    ``result_budget`` is keyed by executor name (``"reg_compliance"`` / ``"cases"``) and
    contains an entry only for *included* executors.

    Phase C: ``context_labels`` echoes ``PlannerDecision.context_labels`` here
    as a pass-through. The field is plumbed in Wave 2 but not yet consumed
    downstream — Phase D wires it into ``LoopState`` + ``AggregatorInput`` via
    ``ContextBlock`` objects rendered by ``run_retrieval``.
    """

    include_reg_compliance: bool
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
    # True when ``aggregator_prompt_key`` was resolved to its editorial twin.
    # Echo only — the key itself is what drives the aggregator.
    editorial: bool = False
    # Phase C — planner-emitted context label list (placeholder; consumed in
    # Phase D when run_retrieval builds ContextBlock objects from it).
    context_labels: list[str] = field(default_factory=list)


def build_retrieval_config(
    decision: PlannerDecision,
    *,
    editorial: bool = False,
) -> RetrievalConfig:
    """Expand a :class:`PlannerDecision` into a concrete :class:`RetrievalConfig`.

    Pure function — no I/O, no side effects. See MODE_PROFILES.md §6.

    - Modes 1–2: the ``base`` executor always runs; the ``support`` executor
      runs iff ``decision.support`` is True. Budgets come from ``ROLE_PROFILES``
      by role.
    - ``full``: both executors run as peers; ``decision.support`` is ignored
      (structural — 'full' has no support role). Budgets come from
      ``FULL_PROFILE`` per executor.

    ``editorial`` (keyword-only) swaps **only** the aggregator prompt key for
    its :data:`EDITORIAL_PROMPT_KEYS` twin — retrieval, budgets, and executor
    selection are byte-identical to the in-app path. Default ``False`` leaves
    every existing caller unchanged.
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

    aggregator_prompt_key = profile["aggregator_prompt_key"]
    if editorial:
        # Fail LOUD on a mode with no twin. Falling back to the in-app key
        # would silently publish a chat-shaped answer as an article — the
        # failure mode that looks exactly like success.
        try:
            aggregator_prompt_key = EDITORIAL_PROMPT_KEYS[aggregator_prompt_key]
        except KeyError:
            raise KeyError(
                f"No editorial aggregator prompt for mode {decision.mode!r} "
                f"(key {aggregator_prompt_key!r}). "
                f"Known twins: {sorted(EDITORIAL_PROMPT_KEYS)}"
            ) from None

    included = set(result_budget)
    return RetrievalConfig(
        include_reg_compliance="reg_compliance" in included,
        include_cases="cases" in included,
        result_budget=result_budget,
        aggregator_prompt_key=aggregator_prompt_key,
        sectors_override=None,  # Wave B — picker future drives the filter
        mode=decision.mode,
        support=False if decision.mode == "full" else decision.support,
        editorial=editorial,
        # Phase C — pass through the planner's label selection. Phase D will
        # consume these via ContextBlock objects in run_retrieval.
        context_labels=list(getattr(decision, "context_labels", []) or []),
    )


__all__ = [
    "MIN_EXPANDER_DIVISOR",
    "ROLE_PROFILES",
    "FULL_PROFILE",
    "MODE_PROFILES",
    "EDITORIAL_PROMPT_KEYS",
    "RetrievalConfig",
    "build_retrieval_config",
]
