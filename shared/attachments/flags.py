"""Whether a user's file edits are read at all (attachment_agent.md §4).

Text corrections (``attachment_text_corrections``) and section promotions
(``attachment_segment_overrides``) are written only by the attachment agent, which
runs only while ``ATTACHMENT_AGENT_ENABLED`` / ``ATTACHMENT_AGENT_USERS`` is on for
the user. So every reader asks this first: off for the user → no corrections read,
no promotions read, no file lookup — zero extra requests and today's bytes. On:
the overlays apply.

Same rule as ``agents/utils/attachment_agent_flags.attachment_agent_enabled`` (the
flag is on, and either no allowlist is set or the user is on it), plus: no user →
off (a reader that cannot name the owner reads nothing). Rollback = the flag off:
corrections and promotions stop applying; their rows are kept. Never raises; off
on error.
"""
from __future__ import annotations

from typing import Any

from shared.config import get_settings


def edits_enabled(user_id: Any) -> bool:
    """Read this user's text corrections and section promotions (``users.user_id``)."""
    if not user_id:
        return False
    try:
        settings = get_settings()
        if not getattr(settings, "ATTACHMENT_AGENT_ENABLED", False):
            return False
        users = getattr(settings, "ATTACHMENT_AGENT_USERS", None)
        allowed = {u.strip() for u in str(users or "").split(",") if u.strip()}
        return not allowed or str(user_id) in allowed
    except Exception:  # noqa: BLE001
        return False


__all__ = ["edits_enabled"]
