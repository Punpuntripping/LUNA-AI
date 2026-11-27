"""On/off check for the angle map (``ANGLE_MAP_ENABLED`` + ``ANGLE_MAP_USERS``).

Plan: ``.claude/plans/relationship_map.md`` §4.7. A leaf module, like
``segmenter_flags.py``: the flag is on, and either no allowlist is set or this user
is on it. The map is built from the segmenter's sections, so it is also off for a
user the segmenter is off for.
"""
from __future__ import annotations

from typing import Any

from shared.config import get_settings

from .segmenter_flags import _on_for, segmenter_enabled


def angle_map_enabled(user_id: Any) -> bool:
    """``ANGLE_MAP_ENABLED`` and (no ``ANGLE_MAP_USERS`` allowlist, or ``user_id`` —
    a ``users.user_id`` — is on it), and the segmenter on for that user. Never
    raises; off on error (off is today's behaviour)."""
    try:
        settings = get_settings()
        if not _on_for(
            getattr(settings, "ANGLE_MAP_ENABLED", False),
            getattr(settings, "ANGLE_MAP_USERS", None),
            user_id,
        ):
            return False
        return segmenter_enabled(user_id)
    except Exception:  # noqa: BLE001
        return False


def library_file_maps_enabled(user_id: Any) -> bool:
    """``LIBRARY_FILE_MAPS_ENABLED`` and (no ``LIBRARY_FILE_MAPS_USERS`` allowlist,
    or ``user_id`` is on it): «الملفات» copies carry their file's map, and one with
    no map is mapped (``agents/memory/angle_mapper/library.py``). Never raises; off
    on error (off = a copy has no map, as before)."""
    try:
        settings = get_settings()
        return _on_for(
            getattr(settings, "LIBRARY_FILE_MAPS_ENABLED", False),
            getattr(settings, "LIBRARY_FILE_MAPS_USERS", None),
            user_id,
        )
    except Exception:  # noqa: BLE001
        return False


__all__ = ["angle_map_enabled", "library_file_maps_enabled"]
