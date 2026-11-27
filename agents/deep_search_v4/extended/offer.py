"""«بحث شامل» as an option of the ONE deep-search planner (.claude/plans/one_planner.md).

Owner 2026-11-27: "we need the planner to be 1 agent and one agent only, using v4.1".
With ``ONE_PLANNER_ENABLED`` on, the separate extended planner does not run: the
deep-search planner itself may end its run in an :class:`ExtendedSearchPlan`. This
module decides, per turn, what the planner is told about «بحث شامل»:

- **available** — the same code checks as before (``eligibility.check_eligibility``:
  the flag, the plan, the points, a fresh angle map with ≥ 2 angles) plus room in the
  workspace for two reports and the merged answer. The planner gets the rules
  (``prompts.one_planner_available_section``) and the data (budget, documents, angles,
  ``view.render_extended_block``), and its output types gain ``ExtendedSearchPlan``.
- **not available** — the planner gets the reason in the user's words and the rule
  "say so and offer «بحث عميق»" (owner O2).
- ``None`` — «بحث شامل» is off for this user: nothing is said, as today.

The router's ``DispatchAgent.extended`` is a HINT (owner O1): it only changes one line
of the section. The planner decides.

Masking: the data block is encoded with the turn codec when it is built — the caller
persists new fakes before the planner runs (both call sites already flush the codec
right after building the planner's inputs). The plan the planner returns is decoded
(:func:`decode_offer_plan`) before a card is made from it.

Sync parts (``prepare_offer``) read the database — call them in a thread. Never raise.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from .models import BudgetMode, ExtendedPlan, ExtendedSearchPlan, PlannerView

logger = logging.getLogger(__name__)

#: The smallest plan: two reports + the merged answer.
MIN_PLAN_ITEMS = 3
#: Not an eligibility reason of ``eligibility``: the workspace cannot hold a plan.
REASON_WORKSPACE_FULL = "workspace_full"
WORKSPACE_FULL_AR = (
    "مساحة العمل في هذه المحادثة لا تتّسع لتقارير بحث شامل، "
    "فاحذف بعض المستندات أو ابدأ محادثة جديدة."
)
#: ``paused_runs.deferred_payload`` key: what a clarify pause keeps for the resume leg.
PAUSE_KEY = "extended_offer"


@dataclass
class ExtendedOffer:
    """What the one planner is told about «بحث شامل» this turn."""

    available: bool
    reason: str
    #: The router read the message as asking for «بحث شامل» (``DispatchAgent.extended``).
    hint: bool = False
    #: The turn's attached item ids the eligibility looked at.
    item_ids: list[str] = field(default_factory=list)
    #: Not available: the words for the user.
    unavailable_ar: str = ""
    view: Optional[PlannerView] = None
    mode: Optional[BudgetMode] = None
    points_left: Optional[float] = None
    plan_id: Optional[str] = None
    #: Most searches the workspace can hold (one item each + the merged answer).
    max_searches: Optional[int] = None
    #: The planner's section — rules + encoded data — rendered once, when prepared.
    instructions: str = ""

    def pause_payload(self) -> dict[str, Any]:
        """What a clarify pause keeps so the resume leg can offer it again."""
        return {"hint": bool(self.hint), "item_ids": list(self.item_ids)}


# --------------------------------------------------------------------------- seams (tests patch these)


def _flag_on(user_id: Optional[str]) -> bool:
    from agents.utils.extended_search_flags import extended_search_enabled

    return extended_search_enabled(user_id)


def _eligibility(supabase: Any, *, user_id: str, attached_item_ids: Sequence[str]) -> Any:
    from shared import quota

    from .eligibility import check_eligibility

    return check_eligibility(
        supabase, user_id=user_id, gate=quota.active_deep_search_gate(), attached_item_ids=attached_item_ids,
    )


def _workspace_count(supabase: Any, conversation_id: str) -> tuple[int, int]:
    """``(items now, the cap)`` — the orchestrator's own count."""
    from agents import orchestrator

    return int(orchestrator._count_artifact_kinds(supabase, conversation_id)), int(orchestrator._WORKSPACE_CAP)


def _planner_view(supabase: Any, angle_map: dict, *, user_id: str) -> PlannerView:
    from .view import build_planner_view

    return build_planner_view(supabase, angle_map, user_id=user_id)


def _encode(text: str) -> str:
    try:
        from backend.app.services.masking_service import encode_active

        return encode_active(text)
    except Exception:  # noqa: BLE001 - masking off / unavailable = the text as is
        return text


# --------------------------------------------------------------------------- building the offer


def unavailable_words(reason: str) -> str:
    """The user-facing Arabic for a reason (the router's words, plus workspace-full)."""
    from .router_view import UNAVAILABLE_AR, UNAVAILABLE_DEFAULT_AR

    base = str(reason or "").split(":", 1)[0]
    if base == REASON_WORKSPACE_FULL:
        return WORKSPACE_FULL_AR
    return UNAVAILABLE_AR.get(base, UNAVAILABLE_DEFAULT_AR)


def _unavailable(reason: str, *, hint: bool, item_ids: list[str], **kw: Any) -> ExtendedOffer:
    from .prompts import one_planner_unavailable_section

    words = unavailable_words(reason)
    return ExtendedOffer(
        available=False, reason=reason, hint=hint, item_ids=item_ids, unavailable_ar=words,
        instructions=one_planner_unavailable_section(words, hint=hint), **kw,
    )


def _wi_of(wi_alias_map: Mapping[int, str] | None) -> dict[str, str]:
    return {str(item_id): f"WI-{seq}" for seq, item_id in (wi_alias_map or {}).items() if item_id}


def prepare_offer(
    supabase: Any,
    *,
    user_id: str,
    conversation_id: str,
    item_ids: Sequence[str],
    hint: bool = False,
    wi_alias_map: Mapping[int, str] | None = None,
) -> ExtendedOffer | None:
    """The turn's offer, or ``None`` when «بحث شامل» is off for this user. Never raises:
    a failure is an unavailable offer (the planner then plans as usual)."""
    try:
        if not _flag_on(user_id):
            return None
    except Exception:  # noqa: BLE001
        return None
    ids = list(dict.fromkeys(str(i) for i in item_ids or [] if i))
    try:
        from .eligibility import REASON_NO_ATTACHMENTS

        if not ids:
            # The router attached nothing (or a pause kept nothing): the conversation's
            # files — what the router's own «بحث شامل» section looks at.
            ids = conversation_attachment_ids(supabase, conversation_id=conversation_id, user_id=user_id)
        if not ids:
            return _unavailable(REASON_NO_ATTACHMENTS, hint=hint, item_ids=ids)
        elig = _eligibility(supabase, user_id=user_id, attached_item_ids=ids)
        if not elig.eligible or not elig.angle_map or elig.mode is None:
            return _unavailable(
                str(elig.reason), hint=hint, item_ids=ids,
                points_left=elig.points_left, plan_id=elig.plan_id,
            )
        count, cap = _workspace_count(supabase, conversation_id)
        room = cap - count - 1  # one item per report + the merged answer
        if room < MIN_PLAN_ITEMS - 1:
            return _unavailable(
                REASON_WORKSPACE_FULL, hint=hint, item_ids=ids,
                points_left=elig.points_left, plan_id=elig.plan_id,
            )
        view = _planner_view(supabase, elig.angle_map, user_id=user_id)
        from .prompts import one_planner_available_section
        from .view import render_extended_block

        data = _encode(render_extended_block(view, elig.mode, wi_of=_wi_of(wi_alias_map)))
        instructions = (
            one_planner_available_section(elig.mode, hint=hint)
            + "\n<extended_search>\n" + data + "\n</extended_search>\n"
        )
        return ExtendedOffer(
            available=True, reason=str(elig.reason), hint=hint, item_ids=ids, view=view,
            mode=elig.mode, points_left=elig.points_left, plan_id=elig.plan_id,
            max_searches=room, instructions=instructions,
        )
    except Exception as exc:  # noqa: BLE001 - never lose the turn over the offer
        logger.warning("one_planner: «بحث شامل» offer failed (%s) — offered as unavailable", type(exc).__name__,
                       exc_info=True)
        try:
            from .eligibility import REASON_ERROR

            return _unavailable(f"{REASON_ERROR}:{type(exc).__name__}", hint=hint, item_ids=ids)
        except Exception:  # noqa: BLE001
            return None


def conversation_attachment_ids(supabase: Any, *, conversation_id: str, user_id: str) -> list[str]:
    """The conversation's live attached files, oldest first — what a resume leg offers
    over when its pause kept no offer inputs (a pause made before the flag). Never
    raises: ``[]`` on a failed read."""
    try:
        rows = (
            supabase.table("workspace_items")
            .select("item_id")
            .eq("conversation_id", conversation_id)
            .eq("user_id", user_id)
            .eq("kind", "attachment")
            .is_("deleted_at", "null")
            .order("created_at")
            .execute()
        ).data or []
        return [str(r["item_id"]) for r in rows if r.get("item_id")]
    except Exception:  # noqa: BLE001
        logger.warning("one_planner: conversation attachments not read", exc_info=True)
        return []


def resume_offer_inputs(payload: Any, *, reply_item_ids: Sequence[str]) -> tuple[list[str], bool] | None:
    """``(item ids, hint)`` the resume leg offers over, or ``None`` = no offer.

    ``payload`` — the paused run's ``deferred_payload[PAUSE_KEY]``: ``{"off": True}``
    keeps the run one search; ``{"hint", "item_ids"}`` = the paused run's offer; absent
    (a pause from before the flag) = no ids, no hint (``prepare_offer`` then looks at
    the conversation's files). The files attached to the reply always join."""
    if isinstance(payload, dict) and payload.get("off"):
        return None
    ids: list[str] = []
    hint = False
    if isinstance(payload, dict):
        ids = [str(i) for i in payload.get("item_ids") or [] if i]
        hint = bool(payload.get("hint"))
    ids = list(dict.fromkeys([*ids, *(str(i) for i in reply_item_ids or [] if i)]))
    return ids, hint


# --------------------------------------------------------------------------- the planner's plan


def offer_plan_problems(plan: ExtendedPlan, offer: ExtendedOffer) -> list[str]:
    """What is wrong with a plan for this offer (English, for the model's retry); the
    plan is normalised in place first. Empty = valid."""
    from .planner import normalise_plan, plan_problems

    if not offer.available or offer.view is None or offer.mode is None:
        return ["«بحث شامل» is not available this turn: answer with a PlannerDecision or ask_user."]
    normalise_plan(plan, offer.view)
    bad: list[str] = []
    if len(plan.requests) < 2:
        bad.append(
            "an ExtendedSearchPlan has two or more requests; ONE search is a PlannerDecision — "
            "answer with a PlannerDecision instead"
        )
    bad += plan_problems(plan, offer.view, offer.mode)
    room = offer.max_searches
    if room is not None:
        if len(plan.requests) > room:
            bad.append(f"plan: the conversation's workspace has room for at most {room} searches")
        if plan.cheaper and len(plan.cheaper.requests) > room:
            bad.append(f"cheaper option: the conversation's workspace has room for at most {room} searches")
    return bad


def decode_offer_plan(plan: ExtendedPlan) -> ExtendedSearchPlan:
    """The planner's plan with every text field decoded (store-real) and the shown
    titles in Latin digits — ``planner.decode_plan`` with the turn codec. Except
    ``request_quotes``: kept as written, because they are matched against the
    conversation the planner read (encoded), exactly as a decision's are."""
    from .planner import _active_codec, decode_plan

    decoded = decode_plan(_active_codec(), plan)
    data = decoded.model_dump()
    data.setdefault("request_quotes", list(getattr(plan, "request_quotes", []) or []))
    return ExtendedSearchPlan.model_validate(data)


def planner_points(result: Any) -> float:
    """The planner call's cost in points (USD × 100), priced the way the tracking layer
    prices it; 0 when it cannot be priced (the card's estimate is display-only)."""
    try:
        from agents.utils.tracking import _usage_attrs

        attrs = _usage_attrs(result, slot="deep_planner")
        return round(float(attrs.get("cost_usd") or 0.0) * 100.0, 4)
    except Exception:  # noqa: BLE001
        return 0.0


__all__ = [
    "ExtendedOffer",
    "MIN_PLAN_ITEMS",
    "PAUSE_KEY",
    "REASON_WORKSPACE_FULL",
    "WORKSPACE_FULL_AR",
    "conversation_attachment_ids",
    "decode_offer_plan",
    "offer_plan_problems",
    "planner_points",
    "prepare_offer",
    "resume_offer_inputs",
    "unavailable_words",
]
