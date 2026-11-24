"""Budget modes, the mode pick, the budget block and the draft estimate (plan §4.3).

Code — never the model — decides how much an extended search may use: it picks a
**budget mode** from the points the user has left (D22, D23) and tells the planner the
mode's limits in words (the budget block). Over 50 points left there is **no budget**
(D33): the planner gets no limits and may leave a search uncapped. The estimate on the
plan card is computed here too, from each search's side(s) and sub-query cap (§4.3,
§2.8) — a DRAFT formula until the cost analysis (D25, §7 Q12) replaces the numbers.

Every number is a named constant (build spec, "Defaults for the plan's open questions").
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Optional

from .models import BudgetMode

# --------------------------------------------------------------------------- modes (§4.3, Q12 draft)

#: Mode → (max searches, max sub-queries per side, max ``full``). The 15 mode is the
#: owner's (D22); 20–50 are the §4.3 draft, scaled up with more ``full``.
MODES: dict[int, BudgetMode] = {
    15: BudgetMode(points=15, max_searches=3, max_per_side=8, max_full=2),
    20: BudgetMode(points=20, max_searches=3, max_per_side=8, max_full=3),
    30: BudgetMode(points=30, max_searches=4, max_per_side=10, max_full=4),
    40: BudgetMode(points=40, max_searches=5, max_per_side=10, max_full=5),
    50: BudgetMode(points=50, max_searches=6, max_per_side=12, max_full=6),
}

#: D33 — over ``NO_BUDGET_ABOVE`` points left (or no limited window at all): no limits.
#: Q18 technical ceiling under no budget: none (``NO_BUDGET_MAX_SEARCHES``).
NO_BUDGET = BudgetMode(points=None, max_searches=None, max_per_side=None, max_full=None)
NO_BUDGET_MAX_SEARCHES: Optional[int] = None

#: D23 — one step up is allowed: the biggest mode ≤ points left + the plan's margin.
#: The keys are also the plans that may use extended search at all (D24) — plus ``dev``,
#: the internal staff/test plan (unlimited → no budget; owner 2026-11-24 test rollout:
#: the main test account is on dev).
MARGINS: dict[str, int] = {"pro": 5, "pro_plus": 5, "max": 10, "dev": 10}
ELIGIBLE_PLANS: frozenset[str] = frozenset(MARGINS)

#: D23 — under this many points left: no extended search (today's path, silently — Q16).
MIN_POINTS = 10
#: D33 — over this many points left: no budget.
NO_BUDGET_ABOVE = 50


def pick_mode(points_left: float | None, plan_id: str | None) -> BudgetMode | None:
    """The budget mode for ``points_left`` on ``plan_id`` (the EFFECTIVE plan), or
    ``None`` when the user gets no extended search (a plan outside D24, or fewer than
    ``MIN_POINTS`` left).

    ``points_left`` ``None`` = no limited window (unlimited) → :data:`NO_BUDGET`; over
    ``NO_BUDGET_ABOVE`` → :data:`NO_BUDGET` (D33); else the biggest mode ≤ points left +
    margin (D23: Pro 15 left → the 20 mode; 10 left → the 15 mode)."""
    margin = MARGINS.get(str(plan_id or ""))
    if margin is None:
        return None
    if points_left is None:
        return NO_BUDGET
    try:
        left = float(points_left)
    except (TypeError, ValueError):
        return None
    if math.isnan(left) or left < MIN_POINTS:
        return None
    if left > NO_BUDGET_ABOVE:
        return NO_BUDGET
    fits = [m for m in MODES if m <= left + margin]
    return MODES[max(fits)] if fits else None


def mode_by_label(label: str | None) -> BudgetMode | None:
    """The mode a stored ``BudgetMode.label`` names (``"20"`` / ``"none"``), or ``None``."""
    text = str(label or "").strip()
    if text == NO_BUDGET.label:
        return NO_BUDGET
    try:
        return MODES.get(int(text))
    except ValueError:
        return None


# --------------------------------------------------------------------------- the budget block (§4.3)

_NO_BUDGET_BLOCK = (
    "<budget>\n"
    "No budget limit for this extended search: plan the searches, sides and caps the question needs.\n"
    "Fewer is still better — every search adds minutes and a report the user must read.\n"
    "</budget>"
)


def budget_block(mode: BudgetMode) -> str:
    """The ``<budget>`` block of the planner's input: the mode's limits in words — no
    points, no prices (§4.3); the D33 lines under no budget."""
    if mode.no_budget:
        return _NO_BUDGET_BLOCK
    s, q, f = mode.max_searches, mode.max_per_side, mode.max_full
    full_line = (
        f"- any of the {s} may be `full`;"
        if f >= s
        else f"- at most {f} searches may be `full`; the others search regulations only or judgments only."
    )
    return (
        "<budget>\n"
        "The most this extended search may use:\n"
        f"- at most {s} searches;\n"
        f"- per search, at most {q} sub-queries on each side it runs (regulations, judgments) — "
        f"a `full` search may run {q} + {q};\n"
        f"{full_line}\n"
        "These are limits, not targets: use less when the question needs less.\n"
        "</budget>"
    )


# --------------------------------------------------------------------------- the draft estimate (§4.3, §2.8)

#: Points = USD × 100. Per search: 0.55 fixed + 1.4 for reading the whole file (§2.8).
FIXED_PER_SEARCH_POINTS = 0.55
WHOLE_FILE_PER_SEARCH_POINTS = 1.4
#: Per sub-query: ≈ 0.185 model + the 0.1 search fee.
PER_SUB_QUERY_POINTS = 0.285
#: The merger, when the option runs more than one search.
MERGER_POINTS = 2.0
#: An uncapped side (no budget, ``max_sub_queries`` null) is priced at today's average.
UNCAPPED_REGULATION_SUB_QUERIES = 10
UNCAPPED_JUDGMENT_SUB_QUERIES = 7
#: «بحث عادي» (D31): one ordinary search ≈ 1.95 + 12 × 0.285 ≈ 5.4.
ORDINARY_SUB_QUERIES = 12

_SIDES_OF_MODE: dict[str, tuple[str, ...]] = {
    "reg_compliance_led": ("regulations",),
    "case_led": ("judgments",),
    "full": ("regulations", "judgments"),
}
_UNCAPPED = {"regulations": UNCAPPED_REGULATION_SUB_QUERIES, "judgments": UNCAPPED_JUDGMENT_SUB_QUERIES}


def _field(request: Any, name: str) -> Any:
    if isinstance(request, Mapping):
        return request.get(name)
    return getattr(request, name, None)


def sub_queries_of(request: Any) -> int:
    """Sub-queries a request may run: its cap × its sides; an uncapped side counts
    today's average (10 regulations / 7 judgments)."""
    sides = _SIDES_OF_MODE.get(str(_field(request, "mode") or "reg_compliance_led"), ("regulations",))
    cap = _field(request, "max_sub_queries")
    total = 0
    for side in sides:
        try:
            total += int(cap) if cap is not None else _UNCAPPED[side]
        except (TypeError, ValueError):
            total += _UNCAPPED[side]
    return total


def estimate_points(requests: Iterable[Any], *, planner_points: float = 0.0) -> float:
    """Code's estimate for one option (§4.3): a fixed part per search (whole file
    included) + a part per sub-query at the caps + the merger when there is more than
    one search + the planner's measured cost. ``requests`` — ``ExtendedRequest`` objects
    or dicts with ``mode`` / ``max_sub_queries``."""
    reqs = list(requests or [])
    per_search = FIXED_PER_SEARCH_POINTS + WHOLE_FILE_PER_SEARCH_POINTS
    total = len(reqs) * per_search + sum(sub_queries_of(r) for r in reqs) * PER_SUB_QUERY_POINTS
    if len(reqs) > 1:
        total += MERGER_POINTS
    return round(total + float(planner_points or 0.0), 4)


def ordinary_estimate_points() -> float:
    """«بحث عادي» (D31) on the card: one ordinary search at today's average (§2.8)."""
    return round(
        FIXED_PER_SEARCH_POINTS + WHOLE_FILE_PER_SEARCH_POINTS + ORDINARY_SUB_QUERIES * PER_SUB_QUERY_POINTS, 4
    )


def round_points(value: float) -> int:
    """A card number: round half up (14.5 → 15)."""
    return int(math.floor(float(value) + 0.5))


__all__ = [
    "ELIGIBLE_PLANS",
    "FIXED_PER_SEARCH_POINTS",
    "MARGINS",
    "MERGER_POINTS",
    "MIN_POINTS",
    "MODES",
    "NO_BUDGET",
    "NO_BUDGET_ABOVE",
    "NO_BUDGET_MAX_SEARCHES",
    "ORDINARY_SUB_QUERIES",
    "PER_SUB_QUERY_POINTS",
    "UNCAPPED_JUDGMENT_SUB_QUERIES",
    "UNCAPPED_REGULATION_SUB_QUERIES",
    "WHOLE_FILE_PER_SEARCH_POINTS",
    "budget_block",
    "estimate_points",
    "mode_by_label",
    "ordinary_estimate_points",
    "pick_mode",
    "round_points",
    "sub_queries_of",
]
