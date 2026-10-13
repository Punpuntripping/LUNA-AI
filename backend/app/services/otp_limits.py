"""Fail-STRICT counters for the email-code auth routes (.claude/plans/email_otp_login.md §B3.1).

The OTP / signup / password-reset limiters are a security boundary, not a
damper: without them anyone could send unlimited codes to any inbox and guess
codes with no lockout. So when Redis is missing (``redis is None``) or a Redis
command raises, every primitive here degrades to a per-process sliding window
(``route_limits.ProcessLocalWindow`` — bounded at 20k identities, LRU-evicted)
instead of letting the request through. Callers pass STRICTER numbers for that
path (``local_*``) because each worker/replica keeps its own window.

Every function returns the backend that actually answered (``"redis"`` or
``"process"``) so the route can stamp ``otp.limiter_backend`` on its span.

Keys are built by the caller from HASHED identities only (email hash, IP hash) —
never a raw address.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from redis.asyncio import Redis as AsyncRedis

from backend.app.middleware.route_limits import ProcessLocalWindow

logger = logging.getLogger(__name__)

REDIS = "redis"
PROCESS = "process"

_LOCAL: dict[tuple[str, int, int], ProcessLocalWindow] = {}

# Global "the mail path is failing" flag (GoTrue 5xx / SMTP / timeout on a
# background send). Kept locally too, so it works with no Redis at all.
_SEND_DEGRADED_KEY = "otp:send:degraded"
_send_degraded_until = 0.0


def _local(kind: str, limit: int, window_s: int) -> ProcessLocalWindow:
    """One process-local window per (kind, limit, window). Families share an
    instance; their keys already differ by prefix (``otp:`` / ``signup:`` …)."""
    k = (kind, max(int(limit), 0), int(window_s))
    w = _LOCAL.get(k)
    if w is None:
        w = ProcessLocalWindow(limit=max(int(limit), 1), window_seconds=int(window_s))
        _LOCAL[k] = w
    return w


def reset_local_state() -> None:
    """Drop all process-local state (tests)."""
    global _send_degraded_until
    _LOCAL.clear()
    _send_degraded_until = 0.0


def _warn(what: str, key: str, e: Optional[BaseException]) -> None:
    prefix = key.split(":", 2)[:2]
    if e is None:
        logger.warning("otp_limits.%s: Redis unavailable — process-local fallback (%s)", what, ":".join(prefix))
    else:
        logger.warning("otp_limits.%s: Redis error — process-local fallback (%s): %s", what, ":".join(prefix), e)


# ---------------------------------------------------------------------------
# Cooldown: "at most one per N seconds"
# ---------------------------------------------------------------------------


async def cooldown_blocked(
    redis: Optional[AsyncRedis], key: str, seconds: int, *, local_seconds: int
) -> tuple[bool, str]:
    """Claim the cooldown. True = already claimed (too soon)."""
    err: Optional[BaseException] = None
    if redis is not None:
        try:
            first = await redis.set(key, "1", nx=True, ex=seconds)
            return (not first), REDIS
        except Exception as e:  # noqa: BLE001
            err = e
    _warn("cooldown", key, err)
    n, _ = _local("cd", 1, local_seconds).hit(key)
    return n > 1, PROCESS


async def cooldown_release(
    redis: Optional[AsyncRedis], key: str, *, local_seconds: int
) -> None:
    """Give a claimed cooldown back (no email went out)."""
    if redis is not None:
        try:
            await redis.delete(key)
        except Exception as e:  # noqa: BLE001
            logger.warning("otp_limits.cooldown_release failed: %s", e)
    _local("cd", 1, local_seconds).discard(key)


# ---------------------------------------------------------------------------
# Counters: "at most N per window"
# ---------------------------------------------------------------------------


async def hit_over(
    redis: Optional[AsyncRedis],
    key: str,
    limit: int,
    window_s: int,
    *,
    local_limit: int,
    local_window_s: Optional[int] = None,
) -> tuple[bool, str]:
    """Record one hit; True when the count now EXCEEDS the limit."""
    err: Optional[BaseException] = None
    if redis is not None:
        try:
            n = await redis.incr(key)
            if n == 1:
                await redis.expire(key, window_s)
            return n > limit, REDIS
        except Exception as e:  # noqa: BLE001
            err = e
    _warn("hit", key, err)
    n, _ = _local("ctr", local_limit, local_window_s or window_s).hit(key)
    return n > local_limit, PROCESS


async def reached(
    redis: Optional[AsyncRedis],
    key: str,
    limit: int,
    window_s: int,
    *,
    local_limit: int,
    local_window_s: Optional[int] = None,
) -> tuple[bool, str]:
    """Peek WITHOUT recording: True once the count is at/over the limit."""
    err: Optional[BaseException] = None
    if redis is not None:
        try:
            val = await redis.get(key)
            return (val is not None and int(val) >= limit), REDIS
        except Exception as e:  # noqa: BLE001
            err = e
    _warn("peek", key, err)
    n = _local("ctr", local_limit, local_window_s or window_s).count(key)
    return n >= local_limit, PROCESS


async def record(
    redis: Optional[AsyncRedis],
    key: str,
    window_s: int,
    *,
    local_limit: int,
    local_window_s: Optional[int] = None,
) -> str:
    """Record one hit with no decision (e.g. a failed verify)."""
    err: Optional[BaseException] = None
    if redis is not None:
        try:
            n = await redis.incr(key)
            if n == 1:
                await redis.expire(key, window_s)
            return REDIS
        except Exception as e:  # noqa: BLE001
            err = e
    _warn("record", key, err)
    _local("ctr", local_limit, local_window_s or window_s).hit(key)
    return PROCESS


async def clear(
    redis: Optional[AsyncRedis],
    key: str,
    window_s: int,
    *,
    local_limit: int,
    local_window_s: Optional[int] = None,
) -> None:
    """Reset a counter (e.g. fails after a successful verify) — both backends."""
    if redis is not None:
        try:
            await redis.delete(key)
        except Exception as e:  # noqa: BLE001
            logger.warning("otp_limits.clear failed: %s", e)
    _local("ctr", local_limit, local_window_s or window_s).discard(key)


async def unrecord(redis: Optional[AsyncRedis], key: str) -> None:
    """Best-effort DECR of a Redis counter (a counted send that never went out).
    The process-local window is not refunded — the fallback only gets stricter."""
    if redis is None:
        return
    try:
        await redis.decr(key)
    except Exception as e:  # noqa: BLE001
        logger.warning("otp_limits.unrecord failed: %s", e)


# ---------------------------------------------------------------------------
# Global "sends are failing" flag
# ---------------------------------------------------------------------------


async def mark_send_degraded(redis: Optional[AsyncRedis], seconds: int) -> None:
    global _send_degraded_until
    _send_degraded_until = time.time() + seconds
    if redis is None:
        return
    try:
        await redis.set(_SEND_DEGRADED_KEY, "1", ex=seconds)
    except Exception as e:  # noqa: BLE001
        logger.warning("otp_limits.mark_send_degraded: Redis error: %s", e)


async def send_degraded(redis: Optional[AsyncRedis]) -> bool:
    if time.time() < _send_degraded_until:
        return True
    if redis is None:
        return False
    try:
        return (await redis.get(_SEND_DEGRADED_KEY)) is not None
    except Exception as e:  # noqa: BLE001
        logger.warning("otp_limits.send_degraded: Redis error: %s", e)
        return False


# Public accessor for callers that need a raw window (the global send cap).
local_window = _local
