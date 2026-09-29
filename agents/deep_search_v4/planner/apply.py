"""Mode profiles + the pure ``build_retrieval_config`` function.

The planner LLM emits a tiny :class:`~.models.PlannerDecision` (mode + support).
Every concrete number — result budgets, aggregator prompt key — is derived
**here**, in code, from these tables. The LLM never sees a number, and the
planner no longer caps the expander's sub-query count.

The ONE exception is the editorial ``cap`` pin (``planning/MODE_PROFILES.md`` §7):
an **operator** number carried on a headless editorial job — the most sub-queries
each executor's expander may produce on this run — passed straight through to
``expander_query_cap``. ⚠ It is **not a planner cap**: the planner still never
caps the expander on its own, never sees the number, and it is ``None`` on every
in-app run.

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
# ⚠ The editorial path no longer has aggregator prompts of its own. It once
# swapped each mode prompt for an "editorial twin" that wrote a published
# ARTICLE — headline, lede, ordinal sections, de-identified framing — and
# those three prompts have been deleted: article shaping now happens on the
# marketing side, which holds the answer and its references. ``editorial``
# survives as a flag because it still means something here that has nothing
# to do with rhetoric: a headless job has no user, so the planner converts a
# clarifying pause into a default decision instead of waiting for an answer
# nobody can give (``runner.EDITORIAL_PAUSE_REASON``). The aggregator prompt
# a blog job runs is now exactly the one an in-app turn would run.


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
    # Editorial query cap — the most sub-queries EVERY included executor's
    # expander may produce in this run (expander_query_cap.md §6 /
    # MODE_PROFILES.md §7). ``None`` = not pinned: no block is rendered into any
    # expander user message and no clamp runs.
    # Applies per expander CALL, not per run: under `reg_compliance_led` +
    # support both executors get the same cap — one number for the whole job,
    # not a number split between them.
    expander_query_cap: int | None = None


def build_retrieval_config(
    decision: PlannerDecision,
    *,
    editorial: bool = False,
    cap: int | None = None,
) -> RetrievalConfig:
    """Expand a :class:`PlannerDecision` into a concrete :class:`RetrievalConfig`.

    Pure function — no I/O, no side effects. See MODE_PROFILES.md §6.

    - Modes 1–2: the ``base`` executor always runs; the ``support`` executor
      runs iff ``decision.support`` is True. Budgets come from ``ROLE_PROFILES``
      by role.
    - ``full``: both executors run as peers; ``decision.support`` is ignored
      (structural — 'full' has no support role). Budgets come from
      ``FULL_PROFILE`` per executor.

    ``editorial`` (keyword-only) marks a headless job and is carried through
    to :class:`RetrievalConfig` for the pause conversion upstream. It no
    longer changes the aggregator prompt — the editorial twins were deleted —
    so an editorial run and an in-app run of the same mode are now identical
    here in every field.

    ``cap`` (keyword-only) is the editorial operator's own number: the most
    sub-queries every included executor's expander may produce on this run. It
    lands on ``expander_query_cap`` as a **straight pass-through** — no lookup
    table, no band, no validation. It changes nothing else: not the mode, not
    the executor set, not a budget. ``None`` (the default, and every in-app
    call) leaves the field ``None`` and the run byte-identical to before.

    ⚠ ``cap`` is **not a planner cap**. This function still never caps the
    expander on its own; it only carries a number the operator already chose.

    ⚠ ``None`` is never coerced to a number. An absent cap is a real answer —
    "each expander decides from its own prompt guidance" — and defaulting it
    would cap every editorial job that never asked to be capped.

    Nothing is validated here. The API router earns the 400 for an out-of-range
    value (min 2); by the time a run is dispatched, a nonsense value on a stale
    job row has already been degraded to ``None`` by the service layer, because
    a job that cannot be capped should still run.
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

    # One key per mode, editorial or not: the twins are gone.
    aggregator_prompt_key = profile["aggregator_prompt_key"]

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
        # Straight pass-through — the operator's number, unmodified. ⚠ Do NOT
        # default an absent cap: ``None`` means "each expander decides".
        expander_query_cap=cap,
    )


__all__ = [
    "MIN_EXPANDER_DIVISOR",
    "ROLE_PROFILES",
    "FULL_PROFILE",
    "MODE_PROFILES",
    "RetrievalConfig",
    "build_retrieval_config",
]
