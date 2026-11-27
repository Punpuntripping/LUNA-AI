"""«بحث موسّع» as the router sees it (extended_search.md D34, owner 2026-11-27).

The ROUTER chooses «بحث موسّع» now — ``DispatchAgent(extended=True)`` — instead of
code deciding silently inside the deep-search turn (D30 revised). Before the
router runs, code makes the same checks the planning turn makes
(:func:`eligibility.check_eligibility`: the flag, the plan, the points, a fresh
angle map with at least two angles) over the conversation's attached files, and
the router gets one short section: available, with the files and their angle
titles — or not available, with the reason in Arabic for a user who asks for it
by name.

Flag off for this user → ``None``: no section at all and no read, so every other
user's router prompt stays exactly as it was. The planning turn re-checks
everything at dispatch (``wiring.plan_extended_turn``), so this view only decides
what the router is told.

Sync (call it in a thread): the angle-map loaders read the database. Never raises.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from . import eligibility as _elig
from .card import CARD_TITLE_AR

logger = logging.getLogger(__name__)

#: At most this many angle titles go to the router (it only needs the picture).
MAX_ANGLE_TITLES = 12

#: What the router tells a user who asks for «بحث موسّع» when it is not available.
UNAVAILABLE_AR: dict[str, str] = {
    _elig.REASON_PLAN: (
        "البحث الموسّع متاح في باقات «الاحترافية» و«الاحترافية بلس» و«القصوى»."
    ),
    _elig.REASON_LOW_POINTS: (
        "رصيدك المتبقي من النقاط لا يكفي لبحث موسّع الآن، ويتجدّد مع بداية الفترة القادمة."
    ),
    _elig.REASON_NO_ATTACHMENTS: (
        "البحث الموسّع يدرس ملفاً مرفقاً من عدة أوجه، فأرفق الملف الذي تريد دراسته."
    ),
    _elig.REASON_NO_MAP: (
        "لم تُستخرج خيوط هذا الملف، فلا يتاح له بحث موسّع الآن."
    ),
    _elig.REASON_FEW_ANGLES: (
        "هذا الملف يدور حول مسألة واحدة، فيكفيه البحث العادي."
    ),
}
UNAVAILABLE_DEFAULT_AR = "البحث الموسّع غير متاح الآن."


@dataclass
class RouterExtendedView:
    """What the router is told about «بحث موسّع» this turn."""

    available: bool
    #: ``eligibility`` reason (logging; ``eligible`` when available).
    reason: str
    #: The mapped files, in the map's order: ``(alias "WI-n", item_id, title)``.
    files: list[tuple[str, str, str]] = field(default_factory=list)
    #: The map's angle titles, ENCODED with the turn codec.
    angle_titles: list[str] = field(default_factory=list)

    @property
    def unavailable_ar(self) -> str:
        base = self.reason.split(":", 1)[0]
        return UNAVAILABLE_AR.get(base, UNAVAILABLE_DEFAULT_AR)

    @property
    def file_item_ids(self) -> list[str]:
        return [item_id for _, item_id, _ in self.files]


def _encode(text: str) -> str:
    try:
        from backend.app.services.masking_service import encode_active

        return encode_active(text)
    except Exception:  # noqa: BLE001 - masking off / unavailable = the text as is
        return text


def router_extended_view(
    supabase: Any,
    *,
    user_id: str,
    workspace_item_summaries: Sequence[Mapping[str, Any]],
) -> RouterExtendedView | None:
    """The router's «بحث موسّع» section for this turn, or ``None`` (flag off).

    ``workspace_item_summaries`` — the router context's item dicts (``item_id``,
    ``wi_seq``, ``kind``, ``title``); the attachments among them are the files the
    map may cover. Titles there are already what the router reads (encoded)."""
    try:
        from agents.utils.extended_search_flags import extended_search_enabled

        if not extended_search_enabled(user_id):
            return None
    except Exception:  # noqa: BLE001
        return None
    try:
        from shared import quota

        attachments = [
            s for s in workspace_item_summaries or []
            if str(s.get("kind") or "") == "attachment" and s.get("item_id")
        ]
        by_id = {str(s["item_id"]): s for s in attachments}
        elig = _elig.check_eligibility(
            supabase,
            user_id=user_id,
            gate=quota.active_deep_search_gate(),
            attached_item_ids=list(by_id),
        )
        if not elig.eligible or not elig.angle_map:
            return RouterExtendedView(available=False, reason=str(elig.reason))
        files: list[tuple[str, str, str]] = []
        for member in elig.angle_map.get("items") or []:
            item_id = str(member.get("item_id") or "")
            summary = by_id.get(item_id)
            if summary is None or summary.get("wi_seq") is None:
                continue  # a member outside this conversation's workspace
            files.append((f"WI-{summary['wi_seq']}", item_id, str(summary.get("title") or "")))
        if not files:
            return RouterExtendedView(available=False, reason=_elig.REASON_NO_MAP)
        angles = [
            _encode(str(a.get("title") or "").strip())
            for a in (elig.angle_map.get("angles") or [])[:MAX_ANGLE_TITLES]
        ]
        return RouterExtendedView(
            available=True,
            reason=str(elig.reason),
            files=files,
            angle_titles=[a for a in angles if a],
        )
    except Exception as exc:  # noqa: BLE001 - any failure = no extended search offered
        logger.warning("extended_search: router view failed (%s)", type(exc).__name__)
        return RouterExtendedView(available=False, reason=f"{_elig.REASON_ERROR}:{type(exc).__name__}")


def render_router_section(view: RouterExtendedView | None) -> str:
    """The router's dynamic instruction. Empty for ``None`` (flag off)."""
    if view is None:
        return ""
    if not view.available:
        return (
            "\n## «بحث موسّع» — not available this turn\n\n"
            "Never set `extended=true` this turn. If the user asks for «بحث موسّع» "
            "by name, reply with `ChatResponse`: tell them in one sentence, in these "
            f"words, «{view.unavailable_ar}» and offer an ordinary deep search "
            "instead. Any other request: route it as usual.\n"
        )
    files = "، ".join(f"{alias} «{title}»" if title else alias for alias, _, title in view.files)
    angles = "\n".join(f"  {n}. {title}" for n, title in enumerate(view.angle_titles, start=1))
    aliases = ", ".join(f'"{alias}"' for alias, _, _ in view.files)
    return (
        "\n## «بحث موسّع» — available this turn\n\n"
        f"These files have been read into their angles («الخيوط» — the separate legal "
        f"questions the file raises): {files}\n"
        + (f"Angles:\n{angles}\n" if angles else "")
        + "\n«بحث موسّع» studies those files from several angles at once: its own "
        "planner splits the work into two or more searches, shows the user a plan "
        "card with its cost in points and an «بحث عادي» button, and nothing runs "
        "until the user clicks. Choose it with "
        f'`DispatchAgent(agent_family="deep_search", extended=true, attached_wis=[{aliases}])` when:\n'
        "- the user asks for it by name («بحث موسّع»، «البحث الموسع»، «ابحث بحثاً موسّعاً»), or\n"
        "- the user asks to research the file as a whole, across several of its points "
        "(«ادرس القضية من كل جوانبها»، «ابحث لي في كل نقاط الدفاع في المذكرة»), or\n"
        f"- your last reply is a plan card («{CARD_TITLE_AR}») and the user's message "
        "changes that plan instead of pressing a button (drop or add an angle, fewer "
        "searches, judgments only…): a new plan is made with their words.\n"
        "This holds even when the workspace already holds a prior search report: an "
        "extended search is a new search, never `CheckPriorReports`.\n"
        "Keep `extended=false` — an ordinary deep search — for a question about one "
        "point of the file, or one that does not need the file.\n"
    )


__all__ = [
    "MAX_ANGLE_TITLES",
    "RouterExtendedView",
    "UNAVAILABLE_AR",
    "UNAVAILABLE_DEFAULT_AR",
    "render_router_section",
    "router_extended_view",
]
