"""On/off checks for the ``user_files`` read and write switches (Phase 2).

Plan: ``.claude/plans/my_files_phase2.md`` §C1 / §C2. Same rule as
``agents/utils/segmenter_flags.py``: the flag is on, and either no allow-list is
set or this ``users.user_id`` is on it. A ``None`` user with an allow-list set
reads as off. Off on error — off is today's behaviour.

Both switches also need the schema: until PostgREST serves ``user_files`` and
``workspace_items.file_id`` (migrations 221 + 222), they read as OFF whatever
the flags say. The probe result is cached for the process once it is True; while
False it is re-run at most once a minute and each failed probe logs one error.

``override`` forces a switch for tests and ``scripts/user_files_parity.py``. It
is process-wide (not per request) on purpose: the parity script calls loaders
that hop threads. Never use it in request code.
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from shared.config import get_settings

logger = logging.getLogger(__name__)

FILES_TABLE = "user_files"
ITEMS_TABLE = "workspace_items"

#: Seconds between two probes while the schema is not there yet.
PROBE_RETRY_S = 60.0

_lock = threading.Lock()
_ready = False
_last_probe: Optional[float] = None
#: {"read": bool | None, "write": bool | None} — None = the settings decide.
_override: dict[str, Optional[bool]] = {"read": None, "write": None}


def _on_for(enabled: Any, users: Any, user_id: Any) -> bool:
    if not enabled:
        return False
    allowed = {u.strip() for u in str(users or "").split(",") if u.strip()}
    return not allowed or str(user_id or "") in allowed


def _probe(supabase: Any) -> bool:
    """Both relations answer. Raises nothing."""
    try:
        supabase.table(FILES_TABLE).select("file_id").limit(1).execute()
        supabase.table(ITEMS_TABLE).select("file_id").limit(1).execute()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "user_files: schema not ready (%s) — the files switches read as off",
            type(exc).__name__,
        )
        return False


def schema_ready(supabase: Any) -> bool:
    """True once PostgREST serves ``user_files`` and ``workspace_items.file_id``.

    Cached for the process once True; while False, probed at most once every
    ``PROBE_RETRY_S`` seconds. A ``None`` client cannot probe: it reads the cache.
    """
    global _ready, _last_probe
    if _ready:
        return True
    if supabase is None:
        return False
    with _lock:
        if _ready:
            return True
        now = time.monotonic()
        if _last_probe is not None and now - _last_probe < PROBE_RETRY_S:
            return False
        _last_probe = now
        _ready = _probe(supabase)
        return _ready


def _switch(kind: str, user_id: Any) -> bool:
    forced = _override.get(kind)
    if forced is not None:
        return bool(forced)
    settings = get_settings()
    prefix = f"USER_FILES_{kind.upper()}"
    return _on_for(
        getattr(settings, f"{prefix}_ENABLED", False),
        getattr(settings, f"{prefix}_USERS", None),
        user_id,
    )


def files_read_enabled(user_id: Any = None, *, supabase: Any = None) -> bool:
    """``USER_FILES_READ_ENABLED`` / ``USER_FILES_READ_USERS``, and the schema.
    Never raises; off on error."""
    try:
        return _switch("read", user_id) and schema_ready(supabase)
    except Exception:  # noqa: BLE001
        return False


def files_write_enabled(user_id: Any = None, *, supabase: Any = None) -> bool:
    """``USER_FILES_WRITE_ENABLED`` / ``USER_FILES_WRITE_USERS``, and the schema.
    Never raises; off on error."""
    try:
        return _switch("write", user_id) and schema_ready(supabase)
    except Exception:  # noqa: BLE001
        return False


@contextmanager
def override(*, read: Optional[bool] = None, write: Optional[bool] = None) -> Iterator[None]:
    """Force the read and/or write switch inside the block (tests, parity
    script). ``None`` leaves that switch to the settings. The schema probe still
    applies: a forced-on switch is off until the schema is there."""
    previous = dict(_override)
    if read is not None:
        _override["read"] = read
    if write is not None:
        _override["write"] = write
    try:
        yield
    finally:
        _override.clear()
        _override.update(previous)


def reset_probe() -> None:
    """Forget the probe result (tests, and the parity script between phases)."""
    global _ready, _last_probe
    with _lock:
        _ready = False
        _last_probe = None


__all__ = [
    "PROBE_RETRY_S",
    "files_read_enabled",
    "files_write_enabled",
    "override",
    "reset_probe",
    "schema_ready",
]
