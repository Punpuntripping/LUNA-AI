"""On/off checks for Completion Search and the attachment citation pins.

Plan: ``.claude/plans/completion_search.md`` §4.10. A leaf module — the planners, the
helper frame and the delivery code all ask it without importing each other. Same rule
as ``segmenter_flags``: the flag is on, and either no allowlist is set or this user is
on it. Settings are cached, so a change needs a redeploy. Rollback of any part = its
flag off.

| Flag | Off means |
|---|---|
| ``COMPLETION_SEARCH_ENABLED``        | the search planner keeps ``fetch_article``; the writer planner has no lookup tool |
| ``ATTACHMENT_CITATION_PINS_ENABLED`` | no scan, no rows |
| ``ATTACHMENT_PIN_DELIVERY_ENABLED``  | rows exist but nothing is printed under segments |
| ``ATTACHMENT_CITATION_AGENT_ENABLED`` (on by default, no user list) | the attachment flow is code only: the agent is not called and what it pinned before is not delivered |
"""
from __future__ import annotations

from typing import Any

from shared.config import get_settings


def _on_for(enabled: Any, users: Any, user_id: Any) -> bool:
    if not enabled:
        return False
    allowed = {u.strip() for u in str(users or "").split(",") if u.strip()}
    return not allowed or str(user_id or "") in allowed


def _flag(name: str, user_id: Any) -> bool:
    """Never raises; off on error (off is today's behaviour)."""
    try:
        settings = get_settings()
        return _on_for(
            getattr(settings, f"{name}_ENABLED", False),
            getattr(settings, f"{name}_USERS", None),
            user_id,
        )
    except Exception:  # noqa: BLE001
        return False


def completion_search_enabled(user_id: Any) -> bool:
    """The planners call the Completion Search helper (``user_id`` is a ``users.user_id``)."""
    return _flag("COMPLETION_SEARCH", user_id)


def citation_pins_enabled(user_id: Any) -> bool:
    """The citation scan runs on this user's attachments and stores what they cite."""
    return _flag("ATTACHMENT_CITATION_PINS", user_id)


def citation_agent_enabled() -> bool:
    """The attachment flow hands what code cannot prove to the Completion Search agent
    (owner decision C26). On unless switched off; a failed read = on, because that is
    the designed behaviour and "off" here is the emergency setting."""
    try:
        return bool(getattr(get_settings(), "ATTACHMENT_CITATION_AGENT_ENABLED", True))
    except Exception:  # noqa: BLE001
        return True


def pin_delivery_enabled(user_id: Any) -> bool:
    """Official articles are printed under the attachment segments that cite them."""
    return _flag("ATTACHMENT_PIN_DELIVERY", user_id)


__all__ = [
    "citation_agent_enabled",
    "citation_pins_enabled",
    "completion_search_enabled",
    "pin_delivery_enabled",
]
