"""The plan card (plan §4.4, D26, D27, D31): the runnable options, the card payload and
its text.

Options, priced by code (``budget.estimate_points``), in display order:

- ``plan`` — the planner's requests, always;
- ``cheaper`` — the planner's cheaper option, only when it saves at least
  ``CHEAPER_MIN_SAVING_POINTS`` after rounding (Q13 default);
- ``ordinary`` — «بحث عادي» (D31): today's ordinary deep search, always.

The card's numbers are integers (round half up); ``points_left`` is rounded down. The
card's text (:func:`card_text_ar`) is the §4.4 mock in words, Latin digits — the
assistant row's ``content``: what a client without the card shows, and what the router
and the planner read in the history (D32: a typed adjustment must read as one).
"""
from __future__ import annotations

import math
from typing import Optional

from .budget import estimate_points, ordinary_estimate_points, round_points
from .models import (
    OPTION_LABELS_AR,
    SIDES_OF_MODE,
    ExtendedPlan,
    ExtendedPlanCard,
    ExtendedRequest,
    PlanOptionView,
    PlanSearchView,
    StoredOption,
)

#: Q13 — the cheaper option is dropped when it saves less than this (rounded points).
CHEAPER_MIN_SAVING_POINTS = 1

CARD_TITLE_AR = "بحث شامل — الخطة المقترحة"
MERGE_LINE_AR = "ثم أجمع النتائج في جواب واحد"
HINT_AR = "أو اكتب طلبك في خانة الرسالة"
SIDES_AR: dict[str, str] = {"regulations": "الأنظمة", "judgments": "الأحكام", "both": "الأنظمة والأحكام"}


def build_options(plan: ExtendedPlan, *, planner_points: float) -> list[StoredOption]:
    """``plan``, [``cheaper``], ``ordinary`` — each with code's estimate. The planner's
    measured cost is part of both extended options' estimates (§4.3); «بحث عادي» is
    priced as one ordinary search."""
    plan_est = estimate_points(plan.requests, planner_points=planner_points)
    options = [StoredOption(id="plan", requests=list(plan.requests), estimate_points=plan_est)]
    if plan.cheaper and plan.cheaper.requests:
        cheap_est = estimate_points(plan.cheaper.requests, planner_points=planner_points)
        if round_points(plan_est) - round_points(cheap_est) >= CHEAPER_MIN_SAVING_POINTS:
            options.append(StoredOption(id="cheaper", requests=list(plan.cheaper.requests), estimate_points=cheap_est))
    options.append(StoredOption(id="ordinary", requests=[], estimate_points=ordinary_estimate_points()))
    return options


def _search_views(requests: list[ExtendedRequest]) -> list[PlanSearchView]:
    return [PlanSearchView(title=r.title.strip(), sides=SIDES_OF_MODE[r.mode]) for r in requests]


def build_card(
    options: list[StoredOption], plan: ExtendedPlan, *, run_id: str, points_left: float | None,
) -> ExtendedPlanCard:
    """The card payload (``messages.metadata.extended_plan``). ``points_left`` ``None``
    (no limited window) stays ``None`` — the card and its text leave «المتبقي لك» out;
    otherwise it is rounded down (never below 0)."""
    views = [
        PlanOptionView(
            id=o.id,
            label=OPTION_LABELS_AR[o.id],
            points=round_points(o.estimate_points),
            searches=_search_views(o.requests),
        )
        for o in options
    ]
    by_id = {v.id: v for v in views}
    cheaper_note: Optional[str] = None
    cheaper_saving: Optional[int] = None
    if "cheaper" in by_id and "plan" in by_id and plan.cheaper:
        cheaper_note = plan.cheaper.note.strip() or None
        cheaper_saving = by_id["plan"].points - by_id["cheaper"].points
    left = None if points_left is None else max(0, int(math.floor(float(points_left))))
    return ExtendedPlanCard(
        run_id=run_id,
        points_left=left,
        options=views,
        cheaper_note=cheaper_note,
        cheaper_saving=cheaper_saving,
    )


def points_ar(n: int) -> str:
    """``14 نقطة`` / ``3 نقاط`` (3–10 take the plural)."""
    return f"{n} نقاط" if 3 <= n <= 10 else f"{n} نقطة"


def card_text_ar(card: ExtendedPlanCard) -> str:
    """The §4.4 mock in words — the card row's text. Latin digits."""
    by_id = {o.id: o for o in card.options}
    plan = by_id.get("plan")
    lines = [CARD_TITLE_AR]
    for i, s in enumerate(plan.searches if plan else [], 1):
        lines.append(f"{i}. {s.title} — {SIDES_AR[s.sides]}")
    lines.append(MERGE_LINE_AR)
    cost = f"التكلفة التقريبية: نحو {points_ar(plan.points if plan else 0)}"
    if card.points_left is not None:
        cost += f" · المتبقي لك: {points_ar(card.points_left)}"
    lines.append(cost)
    if card.cheaper_note and card.cheaper_saving is not None and "cheaper" in by_id:
        note = card.cheaper_note.rstrip(" .،؛")
        lines.append(f"{note} — وأوفّر نحو {points_ar(card.cheaper_saving)}")
    lines.append("   ".join(f"[ {o.label} · {o.points} ]" for o in card.options))
    lines.append(HINT_AR)
    return "\n".join(lines)


__all__ = [
    "CARD_TITLE_AR",
    "CHEAPER_MIN_SAVING_POINTS",
    "HINT_AR",
    "MERGE_LINE_AR",
    "SIDES_AR",
    "build_card",
    "build_options",
    "card_text_ar",
    "points_ar",
]
