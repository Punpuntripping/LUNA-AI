"""Dedicated two-window rate limiter for blog-post submissions.

A GLOBAL (not per-IP) sliding-window limiter over Redis sorted sets, mirroring
the ZSET idiom in ``backend/app/middleware/rate_limit.py`` but enforcing TWO
windows at once:

* ``EDITORIAL_RATE_LIMIT_PER_HOUR`` over a rolling 3600s window, and
* ``EDITORIAL_RATE_LIMIT_PER_DAY``  over a rolling 86400s window.

A breach of *either* window → 429 with the Arabic ``RATE_LIMITED`` envelope +
``Retry-After`` + ``X-RateLimit-Remaining-Hour`` / ``X-RateLimit-Remaining-Day``
headers.

Two deliberate design points from the plan (§7):

* **Invoked in-handler, AFTER the idempotency lookup** — NOT as a plain
  ``Depends`` — so idempotency retries (which return the existing job) never
  consume the 100/hr · 300/day budget. The counter is only touched for a
  genuinely-new submission.
* **Fail-open** when Redis is down: this is a cost/safety cap, not a security
  boundary (the service key is the security boundary). A Redis blip must never
  block marketing's own batch.

The cap is on the API as a whole (a single internal caller), so the keys are
global constants, not keyed by IP or user.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from fastapi import Request
from redis.asyncio import Redis as AsyncRedis

from backend.app.errors import ErrorCode, LunaHTTPException
from shared.config import get_settings

logger = logging.getLogger(__name__)

# Global keys — one internal caller; the cap is API-wide, not per-IP.
_HOUR_WINDOW_S = 3600
_DAY_WINDOW_S = 86400
_KEY_HOUR = f"ratelimit:editorial:blog-post-jobs:{_HOUR_WINDOW_S}"
_KEY_DAY = f"ratelimit:editorial:blog-post-jobs:{_DAY_WINDOW_S}"

# Arabic 429 message — matches the global middleware's wording (Rule #5).
_MSG_RATE_LIMITED = "تم تجاوز الحد المسموح من الطلبات"


@dataclass
class RateLimitState:
    """Remaining budget after a successful (allowed) check — for headers."""

    hour_remaining: int
    day_remaining: int


async def enforce_editorial_rate_limit(request: Request) -> RateLimitState:
    """Count one new submission against both windows; raise 429 on breach.

    Call this **after** the idempotency lookup has confirmed a genuinely-new
    submission (retries must be free). Returns the remaining budget so the
    caller can attach ``X-RateLimit-Remaining-*`` headers to the 2xx response.

    Fail-open: if Redis is unavailable/errors, returns full remaining and does
    NOT raise.
    """
    settings = get_settings()
    hour_limit = int(settings.EDITORIAL_RATE_LIMIT_PER_HOUR)
    day_limit = int(settings.EDITORIAL_RATE_LIMIT_PER_DAY)

    redis: Optional[AsyncRedis] = getattr(request.app.state, "redis", None)
    if redis is None:
        # Fail-open — no Redis, no cap. (Middleware uses the same stance.)
        return RateLimitState(hour_remaining=hour_limit, day_remaining=day_limit)

    now = time.time()
    # Unique member so two submissions sharing the same float `now` don't
    # collapse into one ZSET entry (ZADD dedups on member, not score).
    member = f"{now}:{uuid.uuid4().hex}"

    try:
        pipe = redis.pipeline()
        # HOUR window (results 0..3)
        pipe.zremrangebyscore(_KEY_HOUR, 0, now - _HOUR_WINDOW_S)
        pipe.zadd(_KEY_HOUR, {member: now})
        pipe.zcard(_KEY_HOUR)
        pipe.expire(_KEY_HOUR, _HOUR_WINDOW_S)
        # DAY window (results 4..7)
        pipe.zremrangebyscore(_KEY_DAY, 0, now - _DAY_WINDOW_S)
        pipe.zadd(_KEY_DAY, {member: now})
        pipe.zcard(_KEY_DAY)
        pipe.expire(_KEY_DAY, _DAY_WINDOW_S)
        results = await pipe.execute()
    except Exception as e:  # noqa: BLE001
        logger.warning("Editorial rate limiter error (failing open): %s", e)
        return RateLimitState(hour_remaining=hour_limit, day_remaining=day_limit)

    hour_count = int(results[2])
    day_count = int(results[6])
    hour_remaining = max(0, hour_limit - hour_count)
    day_remaining = max(0, day_limit - day_count)

    # A breach of EITHER window denies the request. Hour is the tighter/nearer
    # window, so report it first when both are over.
    breached_window: Optional[int] = None
    if hour_count > hour_limit:
        breached_window = _HOUR_WINDOW_S
    elif day_count > day_limit:
        breached_window = _DAY_WINDOW_S

    if breached_window is not None:
        # Roll back the member we just added to BOTH windows — this request is
        # denied, so it must not consume budget (mirrors the middleware).
        try:
            rollback = redis.pipeline()
            rollback.zrem(_KEY_HOUR, member)
            rollback.zrem(_KEY_DAY, member)
            await rollback.execute()
        except Exception:  # noqa: BLE001
            pass  # best-effort; the entry expires with the window anyway

        retry_after = await _seconds_to_reset(redis, breached_window, now)
        raise LunaHTTPException(
            status_code=429,
            code=ErrorCode.RATE_LIMITED,
            detail=_MSG_RATE_LIMITED,
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Remaining-Hour": str(hour_remaining),
                "X-RateLimit-Remaining-Day": str(day_remaining),
            },
        )

    return RateLimitState(hour_remaining=hour_remaining, day_remaining=day_remaining)


async def _seconds_to_reset(redis: AsyncRedis, window_s: int, now: float) -> int:
    """Seconds until the offending window frees a slot = oldest_score + window - now.

    Best-effort: on any Redis trouble, fall back to the full window length (a
    safe upper bound). Always >= 1.
    """
    key = _KEY_HOUR if window_s == _HOUR_WINDOW_S else _KEY_DAY
    try:
        oldest = await redis.zrange(key, 0, 0, withscores=True)
        if oldest:
            _member, score = oldest[0]
            reset_in = int((float(score) + window_s) - now)
            return max(1, reset_in)
    except Exception:  # noqa: BLE001
        pass
    return window_s


__all__ = ["enforce_editorial_rate_limit", "RateLimitState"]
