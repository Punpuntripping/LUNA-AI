"""On/off check for the one deep-search planner on V4.1 (.claude/plans/one_planner.md).

A leaf module, same rule as ``extended_search_flags``: the flag is on, and either no
allowlist is set or this user is on it. Settings are cached, so a change needs a
redeploy. Rollback = the flag off (today's two planners, byte-identical).
"""
from __future__ import annotations

from shared.config import get_settings

from .extended_search_flags import _on_for


def one_planner_enabled(user_id: str | None) -> bool:
    """The deep-search turn is planned by the one V4.1 planner for this user
    (``user_id`` is a ``users.user_id``). Never raises; off on error."""
    try:
        settings = get_settings()
        return _on_for(
            getattr(settings, "ONE_PLANNER_ENABLED", False),
            getattr(settings, "ONE_PLANNER_USERS", None),
            user_id,
        )
    except Exception:  # noqa: BLE001
        return False


__all__ = ["one_planner_enabled"]
