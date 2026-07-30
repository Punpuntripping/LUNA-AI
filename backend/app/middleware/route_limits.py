"""Route-scoped, verified-identity, fail-CLOSED rate limiter.

Implements ``.claude/plans/access_tiers_gating_DECISIONS.md`` D13.2 + D13.3.

WHY THIS EXISTS ALONGSIDE ``rate_limit.RateLimitMiddleware``
------------------------------------------------------------
The global middleware cannot do this job, for two reasons that are both
structural rather than stylistic:

1. ``request.scope["route"]`` is not populated inside ``BaseHTTPMiddleware``
   (routing has not run yet — PART 9 trap 4), so a middleware cannot key off a
   route template.
2. The middleware keys authed traffic off the **unverified** JWT ``sub`` it
   decodes with ``verify_signature: False`` (PART 9 trap 11). A forged token
   mints a fresh bucket on every request, so that key is worthless as an
   entitlement bound. This limiter runs AFTER routing, verifies the token
   itself, and keys off the verified ``AuthUser.auth_id``. An unverifiable or
   absent token falls back to the client IP — never to a claimed ``sub``.

FAIL-CLOSED, AND WHY IT DIFFERS FROM THE MIDDLEWARE
---------------------------------------------------
The middleware fails OPEN when Redis is missing/erroring, and that is correct
for chat: a Redis blip must not take the product down, and the limiter there is
a cost damper, not a security boundary.

Here the limiter IS the boundary — it is the only per-request bound on the paid
library bytes (``/library/full/*`` and the workspace reference source). A Redis
outage must not silently remove it, so we fall back to a process-local sliding
window with the same budget.

That fallback is a **floor, not a guarantee**: it is per worker process, so N
uvicorn workers (or N Railway replicas) each allow the full budget, i.e. the
true ceiling during a Redis outage is ``limit × workers`` per window. It is
still bounded, which is the point; do not describe it as an exact cap.

USAGE (you are wiring this; this module does not wire itself)
-------------------------------------------------------------
Works unchanged on an auth-required route and on an optional-auth route,
because it resolves identity itself instead of borrowing the route's user::

    from backend.app.middleware.route_limits import library_rate_limit

    # auth-required route (e.g. GET /api/v1/library/full/{content_type}/{key})
    @router.get("/library/full/{content_type}/{key:path}")
    async def get_library_full(
        content_type: str,
        key: str,
        current_user: AuthUser = Depends(get_current_user),
        _rl=Depends(library_rate_limit),
    ):
        ...

    # optional-auth route — identical wiring
    @router.get("/workspace/{item_id}/references/{n}/source")
    async def get_reference_source(
        item_id: str,
        n: int,
        current_user: Optional[AuthUser] = Depends(get_current_user_optional),
        _rl=Depends(library_rate_limit),
    ):
        ...

Both consumers share the ``library`` scope on purpose: one 20/min budget across
the whole reveal family, so an attacker cannot get 40/min by alternating between
``/library/full`` and the reference-source endpoint. If you ever want a separate
budget, build another instance rather than reusing this one::

    my_limit = RouteRateLimiter(scope="something-else", limit=10)

The dependency returns a :class:`RouteRateLimitState` if you want the remaining
budget in the handler; ignoring the return value is fine.

Notes:
* On refusal it raises ``LunaHTTPException(429, ErrorCode.RATE_LIMITED, ...)``
  whose rendered body and headers are byte-identical to the middleware's 429
  (both build from ``rate_limit.RATE_LIMIT_MESSAGE`` / ``rate_limit_headers``).
* It does NOT set ``X-RateLimit-*`` on allowed responses — the global middleware
  owns those headers for the 2xx path, and two writers would fight.
* Denied attempts still consume the window (no rollback), mirroring the
  middleware: a scraper that keeps hammering stays locked out instead of
  settling into a steady 20/min drip.
"""
# NO `from __future__ import annotations` IN THIS MODULE — and do not add it back.
#
# `library_rate_limit` is a callable INSTANCE, not a function. FastAPI resolves a
# dependency's annotations with `getattr(call, "__globals__", {})`; an instance has
# no `__globals__`, so with PEP 563 in force every annotation stays an unevaluated
# ForwardRef. FastAPI then cannot see that `request: Request` is a Request, treats
# it as a QUERY PARAMETER, and every single call fails with
#   422 {"loc": ["query", "request"], "msg": "Field required"}
# — i.e. the limiter silently breaks every route it guards.
# Runtime annotations keep `inspect.signature()` returning real classes.
# `test_route_limits_annotations_are_runtime_resolvable` pins this.

import asyncio
import logging
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Deque, Optional, Tuple

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis as AsyncRedis

from backend.app.errors import ErrorCode, LunaHTTPException
from backend.app.middleware.rate_limit import (
    RATE_LIMIT_MESSAGE,
    client_ip_for_rate_limit,
    rate_limit_headers,
)
from shared.auth.jwt import extract_user

logger = logging.getLogger(__name__)

# D13.2: 20 requests / 60 s for the library reveal family.
LIBRARY_ROUTE_RATE_LIMIT = 20
LIBRARY_ROUTE_WINDOW_SECONDS = 60

# Cached on request.state so two limiters on one route verify the token once.
_IDENTITY_STATE_ATTR = "luna_rate_limit_identity"

# Deliberately NOT supported: picking an AuthUser off request.state that some
# other layer stashed there. The whole point of this limiter is that the
# identity it keys on was signature-verified HERE; borrowing an object whose
# provenance we cannot see would re-open trap 11 the first time someone stashes
# an unverified decode. The cost of verifying again is a local decode against a
# cached JWKS, which deps.get_current_user pays anyway.

_bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class RateLimitIdentity:
    """Who the budget is charged to."""

    key: str          # "user:<auth_id>" or "ip:<addr>"
    verified: bool    # True only when a signature-verified JWT produced it


@dataclass(frozen=True)
class RouteRateLimitState:
    """Remaining budget after an allowed request — handy for logging/headers."""

    identity: str
    verified: bool
    limit: int
    window_seconds: int
    remaining: int
    reset_at: int
    backend: str      # "redis" | "process" (process => Redis was unavailable)


class _ProcessLocalWindow:
    """Per-process sliding window used when Redis is unavailable (fail-closed).

    Bounded by construction:
    * each identity keeps at most ``limit + 1`` timestamps (``deque(maxlen=...)``),
      so a flood cannot grow a bucket — and because the oldest entry is evicted
      rather than the newest dropped, sustained hammering keeps the identity
      blocked for a full window after it stops.
    * the identity table is capped; stale (fully-expired) buckets are swept
      first, and if that is not enough the least-recently-touched entries go.

    Not thread-safe by design: every method is synchronous with no awaits, so it
    is atomic with respect to the event loop it runs on.
    """

    MAX_TRACKED_IDENTITIES = 20_000

    def __init__(self, limit: int, window_seconds: int) -> None:
        self._limit = limit
        self._window = window_seconds
        self._buckets: "OrderedDict[str, Deque[float]]" = OrderedDict()

    def hit(self, identity_key: str) -> Tuple[int, float]:
        """Record one request; return ``(count_in_window, now)``."""
        now = time.time()
        cutoff = now - self._window

        bucket = self._buckets.get(identity_key)
        if bucket is None:
            bucket = deque(maxlen=self._limit + 1)
            self._buckets[identity_key] = bucket
        self._buckets.move_to_end(identity_key)

        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        bucket.append(now)

        if len(self._buckets) > self.MAX_TRACKED_IDENTITIES:
            self._evict(cutoff)

        return len(bucket), now

    def _evict(self, cutoff: float) -> None:
        stale = [k for k, b in self._buckets.items() if not b or b[-1] <= cutoff]
        for k in stale:
            self._buckets.pop(k, None)
        while len(self._buckets) > self.MAX_TRACKED_IDENTITIES:
            self._buckets.popitem(last=False)  # least recently touched

    def reset(self) -> None:
        """Drop all state (tests)."""
        self._buckets.clear()


class RouteRateLimiter:
    """A FastAPI dependency: sliding window keyed on the VERIFIED caller.

    Instantiate once at module level and pass the instance to ``Depends()``.
    """

    def __init__(
        self,
        *,
        scope: str,
        limit: int = LIBRARY_ROUTE_RATE_LIMIT,
        window_seconds: int = LIBRARY_ROUTE_WINDOW_SECONDS,
    ) -> None:
        self.scope = scope
        self.limit = limit
        self.window_seconds = window_seconds
        self._fallback = _ProcessLocalWindow(limit=limit, window_seconds=window_seconds)

    # -- identity ---------------------------------------------------------

    async def resolve_identity(
        self,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials],
    ) -> RateLimitIdentity:
        """Verified user if the bearer token checks out, else the client IP.

        A token that fails verification (forged, expired, unknown key, or JWKS
        unreachable) NEVER contributes its claimed ``sub`` — it degrades to the
        IP bucket, so forging tokens cannot mint fresh budgets.
        """
        cached = getattr(request.state, _IDENTITY_STATE_ATTR, None)
        if isinstance(cached, RateLimitIdentity):
            return cached

        identity: Optional[RateLimitIdentity] = None

        if credentials is not None and credentials.credentials:
            try:
                # extract_user does a local decode; the JWKS fetch inside is
                # sync urllib, so keep it off the event loop (same reasoning as
                # deps.get_current_user).
                user = await asyncio.to_thread(extract_user, credentials.credentials)
                if user and user.auth_id:
                    identity = RateLimitIdentity(
                        key=f"user:{user.auth_id}", verified=True
                    )
            except Exception as e:  # noqa: BLE001 - any failure => untrusted
                logger.debug("Route limiter: token not verified (%s) — keying by IP", e)

        if identity is None:
            identity = RateLimitIdentity(
                key=f"ip:{client_ip_for_rate_limit(request)}", verified=False
            )

        setattr(request.state, _IDENTITY_STATE_ATTR, identity)
        return identity

    # -- counting ---------------------------------------------------------

    def redis_key(self, identity: RateLimitIdentity) -> str:
        return f"ratelimit:route:{self.scope}:{identity.key}:{self.window_seconds}"

    async def _count_redis(self, redis: AsyncRedis, key: str) -> Tuple[int, float]:
        now = time.time()
        pipe = redis.pipeline()
        pipe.zremrangebyscore(key, 0, now - self.window_seconds)
        # Unique member: two requests sharing the same float timestamp would
        # otherwise collapse into one ZSET entry (ZADD dedups on member).
        pipe.zadd(key, {f"{now}:{uuid.uuid4().hex}": now})
        pipe.zcard(key)
        pipe.expire(key, self.window_seconds)
        results = await pipe.execute()
        return int(results[2]), now

    # -- the dependency ---------------------------------------------------

    async def __call__(
        self,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    ) -> RouteRateLimitState:
        identity = await self.resolve_identity(request, credentials)
        key = self.redis_key(identity)

        redis: Optional[AsyncRedis] = getattr(request.app.state, "redis", None)

        count: Optional[int] = None
        now = time.time()
        backend = "redis"

        if redis is not None:
            try:
                count, now = await self._count_redis(redis, key)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Route limiter '%s': Redis error, falling back to the "
                    "in-process limiter (fail-closed): %s",
                    self.scope,
                    e,
                )
                count = None

        if count is None:
            # FAIL-CLOSED. The global middleware fails OPEN here on purpose;
            # this family must not lose its only enumeration bound to a Redis
            # blip. Per-process => a floor, not an exact cap (see module docs).
            backend = "process"
            count, now = self._fallback.hit(identity.key)

        remaining = max(0, self.limit - count)
        reset_at = int(now + self.window_seconds)

        if count > self.limit:
            logger.info(
                "Route limiter '%s' refused %s (count=%s limit=%s backend=%s)",
                self.scope,
                identity.key,
                count,
                self.limit,
                backend,
            )
            raise LunaHTTPException(
                status_code=429,
                code=ErrorCode.RATE_LIMITED,
                detail=RATE_LIMIT_MESSAGE,
                headers=rate_limit_headers(
                    reset_at=reset_at, window_seconds=self.window_seconds, remaining=0
                ),
            )

        return RouteRateLimitState(
            identity=identity.key,
            verified=identity.verified,
            limit=self.limit,
            window_seconds=self.window_seconds,
            remaining=remaining,
            reset_at=reset_at,
            backend=backend,
        )


# The shared library-reveal budget. Wire with ``Depends(library_rate_limit)``.
library_rate_limit = RouteRateLimiter(
    scope="library",
    limit=LIBRARY_ROUTE_RATE_LIMIT,
    window_seconds=LIBRARY_ROUTE_WINDOW_SECONDS,
)


__all__ = [
    "LIBRARY_ROUTE_RATE_LIMIT",
    "LIBRARY_ROUTE_WINDOW_SECONDS",
    "RateLimitIdentity",
    "RouteRateLimitState",
    "RouteRateLimiter",
    "library_rate_limit",
]
