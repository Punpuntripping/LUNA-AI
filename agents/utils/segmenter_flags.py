"""On/off check for the attachment segmenter (``ATTACHMENT_SEGMENTER_ENABLED``).

Plan: ``.claude/plans/attachment_segmentation.md`` §4.9. A leaf module so the OCR
step, the segmenter runner and (later) the helper frame can all ask without
importing each other. Same rule as ``prior_coverage.analyzer_enabled``: the flag
is on, and either no allowlist is set or this user is on it.

The delivery flag (``ATTACHMENT_SEGMENT_DELIVERY_ENABLED``) has its own check in
``agents/utils/attachment_delivery.py``.
"""
from __future__ import annotations

from typing import Any

from shared.config import get_settings


def _on_for(enabled: Any, users: Any, user_id: Any) -> bool:
    if not enabled:
        return False
    allowed = {u.strip() for u in str(users or "").split(",") if u.strip()}
    return not allowed or str(user_id or "") in allowed


def segmenter_enabled(user_id: Any) -> bool:
    """``ATTACHMENT_SEGMENTER_ENABLED`` and (no ``ATTACHMENT_SEGMENTER_USERS``
    allowlist, or ``user_id`` — a ``users.user_id`` — is on it). Never raises;
    off on error (off is today's behaviour)."""
    try:
        settings = get_settings()
        return _on_for(
            getattr(settings, "ATTACHMENT_SEGMENTER_ENABLED", False),
            getattr(settings, "ATTACHMENT_SEGMENTER_USERS", None),
            user_id,
        )
    except Exception:  # noqa: BLE001
        return False


__all__ = ["segmenter_enabled"]
