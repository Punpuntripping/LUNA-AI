"""On/off check for «بحث موسّع» (extended search).

Plan: ``.claude/plans/extended_search.md`` §4.8. A leaf module — the orchestrator wiring
and the extended package ask it without importing each other. Same rule as
``citation_pin_flags``: the flag is on, and either no allowlist is set or this user is on
it. Settings are cached, so a change needs a redeploy. Rollback = the flag off (today's
deep search, byte-identical).
"""
from __future__ import annotations

from typing import Any

from shared.config import get_settings


def _on_for(enabled: Any, users: Any, user_id: Any) -> bool:
    if not enabled:
        return False
    allowed = {u.strip() for u in str(users or "").split(",") if u.strip()}
    return not allowed or str(user_id or "") in allowed


def extended_search_enabled(user_id: str | None) -> bool:
    """The deep-search turn may go to the extended planner for this user
    (``user_id`` is a ``users.user_id``). Never raises; off on error."""
    try:
        settings = get_settings()
        return _on_for(
            getattr(settings, "EXTENDED_SEARCH_ENABLED", False),
            getattr(settings, "EXTENDED_SEARCH_USERS", None),
            user_id,
        )
    except Exception:  # noqa: BLE001
        return False


__all__ = ["extended_search_enabled"]
