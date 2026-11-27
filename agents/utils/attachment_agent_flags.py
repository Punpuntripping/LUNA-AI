"""On/off check for the attachment agent (.claude/plans/attachment_agent.md §4).

On for a user: the router gets the ``ask_attachment_agent`` tool and the new
«own documents» section, and its ``unfold_workspace_item`` no longer opens the
user's uploaded files. Off: the router reads attachments as before. A leaf
module, same rule as ``router_wait_flags``: the flag is on, and either no
allowlist is set or this user is on it. Settings are cached, so a change needs a
redeploy. Rollback = the flag off.
"""
from __future__ import annotations

from typing import Any

from shared.config import get_settings


def _on_for(enabled: Any, users: Any, user_id: Any) -> bool:
    if not enabled:
        return False
    allowed = {u.strip() for u in str(users or "").split(",") if u.strip()}
    return not allowed or str(user_id or "") in allowed


def attachment_agent_enabled(user_id: str | None) -> bool:
    """The attachment agent is on for this user (``users.user_id``).
    Never raises; off on error."""
    try:
        settings = get_settings()
        return _on_for(
            getattr(settings, "ATTACHMENT_AGENT_ENABLED", False),
            getattr(settings, "ATTACHMENT_AGENT_USERS", None),
            user_id,
        )
    except Exception:  # noqa: BLE001
        return False


__all__ = ["attachment_agent_enabled"]
