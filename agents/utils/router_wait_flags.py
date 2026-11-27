"""On/off check for the router's wait on the file helpers (extended_search.md D34/D35).

On for a user: before routing, the turn waits for the segmenter and the citation pins,
then the angle map, on the message's files, and the waiting line names each step. Off:
today's pre-router step, byte-identical. A leaf module, same rule as
``extended_search_flags``: the flag is on, and either no allowlist is set or this user is
on it. Settings are cached, so a change needs a redeploy. Rollback = the flag off.
"""
from __future__ import annotations

from typing import Any

from shared.config import get_settings


def _on_for(enabled: Any, users: Any, user_id: Any) -> bool:
    if not enabled:
        return False
    allowed = {u.strip() for u in str(users or "").split(",") if u.strip()}
    return not allowed or str(user_id or "") in allowed


def router_helper_waits_enabled(user_id: str | None) -> bool:
    """The router waits for the file helpers for this user (``users.user_id``).
    Never raises; off on error."""
    try:
        settings = get_settings()
        return _on_for(
            getattr(settings, "ROUTER_HELPER_WAITS_ENABLED", False),
            getattr(settings, "ROUTER_HELPER_WAITS_USERS", None),
            user_id,
        )
    except Exception:  # noqa: BLE001
        return False


__all__ = ["router_helper_waits_enabled"]
