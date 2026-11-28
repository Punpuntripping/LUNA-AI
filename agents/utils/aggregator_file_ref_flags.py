"""On/off check for the aggregator's file refs («أقسام من مستنداتك»).

Plan: ``.claude/plans/aggregator_file_refs.md`` §4.9. A leaf module — the deep-search
orchestrator asks it without importing the aggregator. Same rule as
``citation_pin_flags``: the flag is on, and either no allowlist is set or this user is
on it (an empty ``AGGREGATOR_FILE_REFS_USERS`` = everyone). Settings are cached, so a
change needs a redeploy.

| Flag | Off means |
|---|---|
| ``AGGREGATOR_FILE_REFS_ENABLED`` | the aggregator's input, prompt and output are byte-identical to before: no ``S`` numbers, no file-tag rules, no ``workspace_item_file_refs`` rows |
"""
from __future__ import annotations

from typing import Any

from shared.config import get_settings


def file_refs_enabled(user_id: Any) -> bool:
    """The aggregator may cite this user's attachment sections as ``[F1·S7]``
    (``user_id`` is a ``users.user_id``). Never raises; off on error."""
    try:
        settings = get_settings()
        if not getattr(settings, "AGGREGATOR_FILE_REFS_ENABLED", False):
            return False
        raw = getattr(settings, "AGGREGATOR_FILE_REFS_USERS", None) or ""
        allowed = {u.strip() for u in str(raw).split(",") if u.strip()}
        return not allowed or str(user_id or "") in allowed
    except Exception:  # noqa: BLE001 — off is today's behaviour
        return False


__all__ = ["file_refs_enabled"]
