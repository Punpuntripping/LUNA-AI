"""Who gets the extended planner on a deep-search turn (plan D30, D23, D24, D33).

Code checks, in order: the flag (``EXTENDED_SEARCH_ENABLED`` + allowlist) → the
EFFECTIVE plan is Pro / Pro Plus / Max → points left (``DeepSearchGate.remaining``, the
tightest limited window; ``None`` = unlimited = no budget) ≥ 10 → a FRESH stored angle
map covering the turn's attachments (several maps → the one covering most of them)
with at least 2 angles → the budget mode (``budget.pick_mode``). Any check that fails —
or any failure at all — means today's planner, silently (Q16); the ``reason`` says why,
for logging only (never shown to the user).

Sync (call it in a thread): the angle-map loaders read the database. Never raises.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from .budget import ELIGIBLE_PLANS, MIN_POINTS, pick_mode
from .models import BudgetMode

logger = logging.getLogger(__name__)

#: D11/D15 — an extended search needs at least two angles to choose from.
MIN_ANGLES = 2

# Reasons (logged; the D30 data). Every "not eligible" carries one.
REASON_OK = "eligible"
REASON_FLAG_OFF = "flag_off"
REASON_NO_GATE = "no_gate"
REASON_PLAN = "plan_not_eligible"
REASON_LOW_POINTS = "low_points"
REASON_NO_ATTACHMENTS = "no_attachments"
REASON_NO_MAP = "no_fresh_map"
REASON_OTHER_USER = "map_of_other_user"
REASON_FEW_ANGLES = "too_few_angles"
REASON_NO_MODE = "no_mode"
REASON_ERROR = "error"


@dataclass
class Eligibility:
    eligible: bool
    reason: str
    mode: BudgetMode | None = None
    points_left: float | None = None
    plan_id: str | None = None
    #: ``store.load_angle_map_for_item`` shape: ``{"map", "items", "angles"}``.
    angle_map: dict | None = None
    #: How many of the turn's attachments the chosen map holds (logging).
    covered: int = 0


def _not(reason: str, **kw: Any) -> Eligibility:
    return Eligibility(eligible=False, reason=reason, **kw)


def _best_fresh_map(supabase: Any, item_ids: Sequence[str]) -> tuple[dict | None, int]:
    """The fresh map holding the most of ``item_ids`` (first found wins a tie), and how
    many it holds. An item already inside a found map is not looked up again.

    An item with no map of its own — a «الملفات» copy — carries its file's map from
    the conversation it was made in, whole (owner 2026-11-27,
    ``angle_mapper.library``), its members pointed at this turn's copies of the
    same files (:func:`~agents.memory.angle_mapper.library.localize_angle_map`)."""
    from agents.memory.angle_mapper.library import load_carried_angle_map, localize_angle_map
    from agents.memory.angle_mapper.store import load_fresh_angle_map_for_item

    found: dict[str, tuple[dict, set[str]]] = {}
    covered: set[str] = set()
    for item_id in item_ids:
        if item_id in covered:
            continue
        stored = load_fresh_angle_map_for_item(supabase, item_id)
        if not stored:
            stored = load_carried_angle_map(supabase, item_id)
            if not stored:
                continue
            stored = localize_angle_map(supabase, stored, item_ids)
        map_id = str((stored.get("map") or {}).get("map_id") or "")
        members = {str(i.get("item_id") or "") for i in stored.get("items") or []}
        mine = members & set(item_ids)
        covered |= mine
        if map_id and map_id not in found:
            found[map_id] = (stored, mine)
    if not found:
        return None, 0
    best, mine = max(found.values(), key=lambda pair: len(pair[1]))
    return best, len(mine)


def check_eligibility(
    supabase: Any,
    *,
    user_id: str,
    gate: Any,
    attached_item_ids: Sequence[str],
) -> Eligibility:
    """Whether this turn goes to the extended planner, and in which budget mode.

    ``gate`` — the turn's ``shared.quota.DeepSearchGate`` (``plan_id`` = the effective
    plan, ``remaining`` = points left in the tightest limited window); ``None`` (the
    gate failed open) = not eligible. ``attached_item_ids`` — the turn's attachment
    items (``workspace_items.item_id``). Never raises."""
    try:
        from agents.utils.extended_search_flags import extended_search_enabled

        if not extended_search_enabled(user_id):
            return _not(REASON_FLAG_OFF)
        if gate is None:
            return _not(REASON_NO_GATE)
        plan_id = str(getattr(gate, "plan_id", None) or "") or None
        if plan_id not in ELIGIBLE_PLANS:
            return _not(f"{REASON_PLAN}:{plan_id or '-'}", plan_id=plan_id)
        points_left: Optional[float] = getattr(gate, "remaining", None)
        if points_left is not None:
            points_left = float(points_left)
            if points_left < MIN_POINTS:
                return _not(REASON_LOW_POINTS, plan_id=plan_id, points_left=points_left)
        ids = list(dict.fromkeys(str(i) for i in attached_item_ids or [] if i))
        if not ids:
            return _not(REASON_NO_ATTACHMENTS, plan_id=plan_id, points_left=points_left)
        stored, covered = _best_fresh_map(supabase, ids)
        if not stored:
            return _not(REASON_NO_MAP, plan_id=plan_id, points_left=points_left)
        if str((stored.get("map") or {}).get("user_id") or "") != str(user_id or ""):
            return _not(REASON_OTHER_USER, plan_id=plan_id, points_left=points_left)
        if len(stored.get("angles") or []) < MIN_ANGLES:
            return _not(REASON_FEW_ANGLES, plan_id=plan_id, points_left=points_left, covered=covered)
        mode = pick_mode(points_left, plan_id)
        if mode is None:
            return _not(REASON_NO_MODE, plan_id=plan_id, points_left=points_left, covered=covered)
        return Eligibility(
            eligible=True,
            reason=REASON_OK,
            mode=mode,
            points_left=points_left,
            plan_id=plan_id,
            angle_map=stored,
            covered=covered,
        )
    except Exception as exc:  # noqa: BLE001 — any failure = today's path
        logger.warning("extended_search: eligibility check failed (%s)", type(exc).__name__)
        return _not(f"{REASON_ERROR}:{type(exc).__name__}")


__all__ = [
    "Eligibility",
    "MIN_ANGLES",
    "REASON_ERROR",
    "REASON_FEW_ANGLES",
    "REASON_FLAG_OFF",
    "REASON_LOW_POINTS",
    "REASON_NO_ATTACHMENTS",
    "REASON_NO_GATE",
    "REASON_NO_MAP",
    "REASON_NO_MODE",
    "REASON_OK",
    "REASON_OTHER_USER",
    "REASON_PLAN",
    "check_eligibility",
]
