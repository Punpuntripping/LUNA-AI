"""Family-side wiring helpers for the unified responder (plan unified_responder.md P2).

* :func:`responder_unified` — the per-family rollout flag
  ``RESPONDER_UNIFIED_FAMILIES`` (D11). Comma list of ``deep_search``,
  ``simple_search``, ``writer``; unset / empty = the old responders everywhere.
* :func:`load_profession_group` — D9's ``users.profession_group``, read only on
  a flagged path (one indexed PK lookup, best-effort).
* :func:`extract_unfilled_placeholders` — the writing package's «placeholders
  the draft left unfilled».

Placeholder convention: the writing executor is told to keep a template's
``[…]`` placeholder verbatim when nobody supplied its value («[رقم الهوية]» —
``agents/writer/prompts.py`` «A placeholder nobody gave a value for stays
as-is»), and the writer_planner turns every missing detail into one
(«[اسم المدعي]» — ``agents/writer_planner/prompts.py`` § «Missing details are
placeholders, not questions»). So an unfilled placeholder is a single-line
square-bracket span containing Arabic text. Excluded: markdown links
(``[text](url)``), numeric citation markers (``[3]``), and empty / ellipsis-only
brackets («[…]» is the template convention's *name*, not a fact).
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

#: Flag tokens → the family keys used here. ``writing`` (the dispatch family
#: name) is accepted as an alias of ``writer`` (the plan's flag token).
_FLAG_ALIASES: dict[str, str] = {
    "deep_search": "deep_search",
    "simple_search": "simple_search",
    "writer": "writer",
    "writing": "writer",
}


def parse_unified_families(raw: str | None) -> frozenset[str]:
    """``"deep_search, writer"`` → ``{"deep_search", "writer"}``. Unknown tokens drop."""
    out: set[str] = set()
    for tok in (raw or "").split(","):
        key = _FLAG_ALIASES.get(tok.strip().lower())
        if key:
            out.add(key)
    return frozenset(out)


def responder_unified(family: str) -> bool:
    """True when ``family`` (deep_search / simple_search / writer|writing) is on
    the unified responder. Never raises — a config error means the old path."""
    key = _FLAG_ALIASES.get((family or "").strip().lower())
    if key is None:
        return False
    try:
        from shared.config import get_settings

        raw = getattr(get_settings(), "RESPONDER_UNIFIED_FAMILIES", None)
        return key in parse_unified_families(raw)
    except Exception:  # noqa: BLE001 — flag read must never break a turn
        logger.debug("responder_unified: settings read failed", exc_info=True)
        return False


def load_profession_group(supabase: Any, user_id: str | None) -> str | None:
    """``users.profession_group`` for ``user_id`` (a users.user_id), or None."""
    if supabase is None or not user_id:
        return None
    try:
        row = (
            supabase.table("users")
            .select("profession_group")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        data = getattr(row, "data", None) if row is not None else None
        if isinstance(data, dict):
            group = data.get("profession_group")
            return str(group) if isinstance(group, str) and group.strip() else None
    except Exception:  # noqa: BLE001 — D9 degrades to neutral wording
        logger.debug("load_profession_group failed", exc_info=True)
    return None


def load_style_profile(supabase: Any, user_id: str | None) -> str | None:
    """The user's rendered style profile (plan §7 / P4), or None. Never raises.

    ``user_id`` is a ``users.user_id`` (NOT auth_id). Identifier-free by
    construction (style_profiler validator), so it is NOT masking-encoded.
    Missing table (migration 176 not applied yet) → None.
    """
    if supabase is None or not user_id:
        return None
    try:
        from agents.memory.style_profiler import load_style_profile_md

        return load_style_profile_md(supabase, str(user_id))
    except Exception:  # noqa: BLE001 — the profile is optional decoration
        logger.debug("load_style_profile failed", exc_info=True)
        return None


def schedule_style_refresh(
    supabase: Any, user_id: str | None, *, conversation_id: str | None = None
) -> bool:
    """Post-turn style-profile refresh trigger (plan §7).

    Gated on ``STYLE_PROFILE_REFRESH_ENABLED`` (kill switch, default on — the
    call counts against the user's usage like every background agent) AND on at
    least one family being on the unified responder: the profile's only reader
    is the responder, so with no family flagged a refresh would bill the user
    for a profile nothing reads. Returns True when a refresh task was
    scheduled. Never raises."""
    if supabase is None or not user_id:
        return False
    try:
        from shared.config import get_settings

        settings = get_settings()
        if not bool(getattr(settings, "STYLE_PROFILE_REFRESH_ENABLED", False)):
            return False
        if not parse_unified_families(
            getattr(settings, "RESPONDER_UNIFIED_FAMILIES", None)
        ):
            return False
        from agents.memory.style_profiler import schedule_style_profile_refresh

        return (
            schedule_style_profile_refresh(
                supabase, str(user_id), conversation_id=conversation_id
            )
            is not None
        )
    except Exception:  # noqa: BLE001 — a background refresh must never break a turn
        logger.debug("schedule_style_refresh failed", exc_info=True)
        return False


_PLACEHOLDER_RE = re.compile(r"\[([^\[\]\n]{1,80})\](?!\()")
_ARABIC_RE = re.compile(r"[؀-ۿ]")
#: Cap — the package lists them; a draft with dozens is a template skeleton.
MAX_PLACEHOLDERS = 15


def extract_unfilled_placeholders(draft_md: str | None) -> list[str]:
    """Distinct ``[…]`` placeholders left in ``draft_md``, in order of appearance."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _PLACEHOLDER_RE.finditer(draft_md or ""):
        inner = m.group(1).strip()
        if not inner or not _ARABIC_RE.search(inner):
            continue
        if inner.strip(".… ") == "":
            continue
        token = f"[{inner}]"
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
        if len(out) >= MAX_PLACEHOLDERS:
            break
    return out


__all__ = [
    "parse_unified_families",
    "responder_unified",
    "load_profession_group",
    "load_style_profile",
    "schedule_style_refresh",
    "extract_unfilled_placeholders",
    "MAX_PLACEHOLDERS",
]
