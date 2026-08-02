"""
Redis sliding-window rate limiter middleware.
Fails open if Redis is unavailable (requests are allowed through).
Sets X-RateLimit-Remaining and X-RateLimit-Reset response headers.

Two limiters live in this package and they deliberately behave differently:

* THIS middleware — global, best-effort, **fail-OPEN**. It is a cost/abuse
  damper for the whole API (chat included). A Redis outage must never take the
  product down, so when Redis is missing or errors we let traffic through.
* ``route_limits.RouteRateLimiter`` — route-scoped, verified-identity,
  **fail-CLOSED**, used only by the library reveal family. There the limiter is
  the only enumeration bound on paid bytes, so a Redis blip must not silently
  remove it.

Both produce the same 429 body/headers (see ``RATE_LIMIT_MESSAGE`` /
``rate_limit_headers`` below, which ``route_limits`` imports) — one contract.

Library path normalization (``normalize_rate_limit_path``) implements
``.claude/plans/access_tiers_gating_DECISIONS.md`` D13.1: the raw
``request.url.path`` used to go into the Redis key verbatim, so
``/api/v1/public/library/regulations/<slug>`` got its own 60/min bucket *per
slug* and breadth-first scraping never tripped anything. ``request.scope["route"]``
is NOT populated inside ``BaseHTTPMiddleware`` (routing has not run yet — PART 9
trap 4), so the template cannot be read here; string normalization is the only
option at this layer.

Client IP resolution (``resolve_client_ip``) is the backend's single trust
boundary for "who is calling": both limiters and Turnstile's ``remoteip`` route
through it, and it is gated on the ``TRUST_CF_HEADERS`` env var — see
``.claude/plans/cloudflare_navigation_hardening.md`` step 3.5 and the flip rule
on ``TRUST_CF_HEADERS_ENV`` below.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Optional

import jwt as pyjwt

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from redis.asyncio import Redis as AsyncRedis

logger = logging.getLogger(__name__)

# Default limits (per IP per window)
DEFAULT_RATE_LIMIT = 60          # requests
DEFAULT_WINDOW_SECONDS = 60      # per minute

# Stricter limits for auth endpoints
AUTH_RATE_LIMIT = 10             # requests
AUTH_WINDOW_SECONDS = 60         # per minute

# Stricter limits for message send (resource-intensive: DB writes + AI pipeline)
MESSAGE_SEND_RATE_LIMIT = 20     # requests
MESSAGE_SEND_WINDOW_SECONDS = 60 # per minute

# Stricter limits for activation-code redemption (burst control; the hard
# brute-force wall is the per-user 5-fails/24h counter in api/plans.py).
REDEEM_RATE_LIMIT = 5            # requests
REDEEM_WINDOW_SECONDS = 60       # per minute


def _env_int(name: str, default: int) -> int:
    """Read a positive int from the environment, falling back on junk values."""
    try:
        value = int(os.environ.get(name, "").strip() or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


# Collapsed public-library ITEM bucket (see normalize_rate_limit_path).
#
# Why this is not simply DEFAULT_RATE_LIMIT: normalization turns "60/min per
# slug" (unbounded in aggregate — the enumeration hole) into "N/min for every
# item page combined". That aggregate cap is now shared by a legitimate caller
# nobody should throttle: the public /regulations, /judgments, ... pages are
# rendered by the Next server (ISR, `frontend/lib/library/api.ts` — server-side
# fetch, no auth header), so EVERY anonymous visitor's cache miss arrives at the
# backend from ONE IP. A crawler walking 49k مادة pages would hammer that single
# bucket — and those fetchers return null on non-OK -> notFound(), so throttling
# them shows Google 404s on real pages. Override with the LIBRARY_ITEM_RATE_LIMIT
# env var without a code change.
#
# BE HONEST ABOUT WHAT THIS IS. Because anonymous library traffic reaches the
# backend through the ISR renderer, this bucket CANNOT tell a scraper from a
# crawler from a reader — they share one IP. It is a runaway-client backstop, not
# an enumeration control, and must not be tuned as though it were one. Per the
# plan's PART 7: "The free/anon layer cannot be made scrape-proof — it exists to
# be crawled." The two real bounds are:
#   * the unlock ledger, which meters the SCARCE layer per user, and
#   * route_limits.library_rate_limit (20/min, VERIFIED identity, fail-closed) on
#     /library/full/* and the reference-source endpoint — i.e. the paid bytes.
# Bounding the anon free layer properly is the edge's job (cloudflare_protection.md),
# deliberately out of scope until that stack is finalized.
#
# 600/min = 10/s aggregate: headroom for the renderer + Googlebot across a ~60k-page
# corpus, still strictly tighter than today's unbounded aggregate.
LIBRARY_ITEM_RATE_LIMIT = _env_int("LIBRARY_ITEM_RATE_LIMIT", 600)
LIBRARY_ITEM_WINDOW_SECONDS = 60

# Paths that are exempt from rate limiting
EXEMPT_PATHS = {"/api/v1/health", "/docs", "/redoc", "/openapi.json"}

# Arabic 429 message — the ONE string both limiters use (Rule #5).
RATE_LIMIT_MESSAGE = "تم تجاوز الحد المسموح من الطلبات"

# ---------------------------------------------------------------------------
# Library path normalization (DECISIONS D13.1)
# ---------------------------------------------------------------------------

PUBLIC_LIBRARY_PREFIX = "/api/v1/public/library/"
LIBRARY_FULL_PREFIX = "/api/v1/library/full/"

# Sections whose item tail is dynamic. `sitemap` is deliberately NOT here: it is
# a small fixed set of feeds, and keeping per-section keys costs nothing.
#
# `sectors` IS here (added 2026-08-01): `/public/library/sectors/{slug}` has 38
# tails, and each one left uncollapsed carries its own DEFAULT_RATE_LIMIT — 38 ×
# 60/min instead of the one shared 600/min bucket. That endpoint runs all four
# hub listers per request (~15-20 PostgREST round-trips), so it is the most
# expensive per-call surface in the wing and the least suitable one to hand a
# per-slug budget. The flat `/public/library/sectors` list has no tail and keeps
# its own key, like every other hub list path.
PUBLIC_LIBRARY_SECTIONS = frozenset(
    {"regulations", "compliance", "circulars", "judgments", "forms", "sectors"}
)

ITEM_PLACEHOLDER = ":item"


def normalize_rate_limit_path(path: str) -> str:
    """Collapse the dynamic tail of known library paths onto one bucket key.

    ``/api/v1/public/library/{section}/<anything...>``
        -> ``/api/v1/public/library/{section}/:item``
        The nested مادة route
        ``/public/library/regulations/{slug}/articles/{article_slug}`` collapses
        into the SAME ``regulations/:item`` bucket — deliberately, so an
        attacker cannot get a second budget by walking articles instead of
        documents.

    ``/api/v1/library/full/{content_type}/<anything...>``
        -> ``/api/v1/library/full/{content_type}/:item``

    Hub list paths (``/api/v1/public/library/regulations``, no tail) are left
    alone and keep their own key — pagination rides a query param, which was
    never part of the key.

    Everything else is returned unchanged. Pure string work: this runs on every
    request, so no regex, no routing, no allocation beyond one f-string on the
    library paths themselves.
    """
    if path.startswith(PUBLIC_LIBRARY_PREFIX):
        tail = path[len(PUBLIC_LIBRARY_PREFIX):]
        section, sep, rest = tail.partition("/")
        if sep and rest and section in PUBLIC_LIBRARY_SECTIONS:
            return f"{PUBLIC_LIBRARY_PREFIX}{section}/{ITEM_PLACEHOLDER}"
        return path

    if path.startswith(LIBRARY_FULL_PREFIX):
        tail = path[len(LIBRARY_FULL_PREFIX):]
        content_type, sep, rest = tail.partition("/")
        if sep and rest and content_type:
            return f"{LIBRARY_FULL_PREFIX}{content_type}/{ITEM_PLACEHOLDER}"
        return path

    return path


def is_public_library_item_path(normalized_path: str) -> bool:
    """True for a normalized ``/public/library/{section}/:item`` bucket."""
    return normalized_path.startswith(PUBLIC_LIBRARY_PREFIX) and normalized_path.endswith(
        "/" + ITEM_PLACEHOLDER
    )


# ---------------------------------------------------------------------------
# Shared 429 contract (imported by route_limits — do not fork it)
# ---------------------------------------------------------------------------


def rate_limit_headers(reset_at: int, window_seconds: int, remaining: int = 0) -> dict:
    """Headers attached to a 429 (and to the middleware's allowed responses).

    ``X-RateLimit-Limit`` is intentionally absent: ``main.py`` only lists
    ``X-RateLimit-Remaining`` / ``X-RateLimit-Reset`` in the CORS
    ``expose_headers``, so a third header would be invisible to the browser
    anyway. Adding it means touching main.py + CORS together.
    """
    return {
        "X-RateLimit-Remaining": str(max(0, remaining)),
        "X-RateLimit-Reset": str(reset_at),
        "Retry-After": str(window_seconds),
    }


def rate_limited_response(reset_at: int, window_seconds: int) -> JSONResponse:
    """The middleware's 429 body. ``route_limits`` raises a LunaHTTPException
    with ErrorCode.RATE_LIMITED, which ``luna_exception_handler`` renders into
    this exact same JSON — one shape, two call sites."""
    return JSONResponse(
        status_code=429,
        content={
            "error": {
                "code": "RATE_LIMITED",
                "message": RATE_LIMIT_MESSAGE,
                "status": 429,
            },
            "detail": RATE_LIMIT_MESSAGE,
        },
        headers=rate_limit_headers(reset_at=reset_at, window_seconds=window_seconds),
    )


# ---------------------------------------------------------------------------
# Client IP resolution (cloudflare_navigation_hardening.md step 3.5)
# ---------------------------------------------------------------------------

# The ONE flag that decides whether CF-Connecting-IP may be believed.
#
# ⚠ FLIP THIS TO TRUE AT THE SAME MOMENT THE ORANGE CLOUD IS ENABLED — NEVER
# BEFORE. While rayhanai.com's DNS records are grey-clouded (DNS-only, the state
# as of 2026-07-28) nothing sits in front of Railway, so no proxy writes
# CF-Connecting-IP: the header would be 100% attacker-supplied and trusting it
# would be strictly WORSE than today's leftmost-XFF, handing anyone a fresh
# rate-limit bucket per request for the price of one header. Once every record
# is proxied, Cloudflare overwrites CF-Connecting-IP on ingress and it becomes
# the only trustworthy client identity (leftmost XFF stays forgeable forever,
# because Cloudflare *appends* to a client-supplied X-Forwarded-For rather than
# replacing it).
#
# Read from the environment rather than shared.config.Settings deliberately:
# Settings is lru_cache'd (a flip would need a restart) and this module already
# reads its own knobs straight from os.environ (see _env_int /
# LIBRARY_ITEM_RATE_LIMIT above). Vocabulary matches ask_service._envbool so
# operators only ever learn one set of truthy words.
TRUST_CF_HEADERS_ENV = "TRUST_CF_HEADERS"

CF_CONNECTING_IP_HEADER = "cf-connecting-ip"


def _env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean from the environment; anything unrecognised is False."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def trust_cf_headers() -> bool:
    """Whether Cloudflare-injected headers may be trusted. DEFAULT FALSE.

    Read fresh on every call (same stance as ask_service's kill switches) so the
    cutover is an env-var change, and so tests can toggle it with monkeypatch.
    """
    return _env_bool(TRUST_CF_HEADERS_ENV, default=False)


def _forwarded_or_peer(request: Request) -> Optional[str]:
    """Pre-Cloudflare behaviour: leftmost non-empty X-Forwarded-For hop, else
    the socket peer, else None.

    Leftmost (not rightmost) because Railway is the only proxy today and it
    appends; that also means the value is attacker-controlled — see
    ``resolve_client_ip``. Blank hops are skipped so a header like
    ``", 1.2.3.4"`` cannot collapse every caller into one empty-string bucket.
    """
    for hop in request.headers.get("x-forwarded-for", "").split(","):
        hop = hop.strip()
        if hop:
            return hop
    client = request.client
    return client.host if client else None


def resolve_client_ip(request: Request) -> Optional[str]:
    """Best-effort client IP — the ONE resolver for the whole backend.

    ``TRUST_CF_HEADERS`` unset/false (today, grey cloud):
        identical to the historical behaviour — leftmost ``X-Forwarded-For``
        hop, else the socket peer, else ``None``. ``CF-Connecting-IP`` is
        ignored completely, so an attacker cannot mint buckets by sending it.

    ``TRUST_CF_HEADERS`` true (only once every DNS record is orange-clouded):
        ``CF-Connecting-IP`` wins when present; when it is absent or blank we
        fall back to the same XFF/peer chain, which keeps direct-to-origin
        callers Cloudflare never sees — the Railway healthcheck on
        ``/api/v1/health``, local dev, tests — resolving to something sane
        instead of ``None``.

    Both the rate limiters (via ``client_ip_for_rate_limit``) and Turnstile's
    ``remoteip`` (``public_ask._client_ip``) go through here, so the trust
    boundary moves in exactly one place.
    """
    if trust_cf_headers():
        cf_ip = (request.headers.get(CF_CONNECTING_IP_HEADER) or "").strip()
        if cf_ip:
            return cf_ip
    return _forwarded_or_peer(request)


def client_ip_for_rate_limit(request: Request) -> str:
    """``resolve_client_ip`` narrowed to the string a Redis key needs.

    A caller with no resolvable IP lands in a single shared ``unknown`` bucket —
    deliberately, so unattributable traffic is metered together rather than
    being handed an unmetered path.
    """
    return resolve_client_ip(request) or "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Sliding-window rate limiter backed by Redis sorted sets.
    If Redis is unavailable, requests pass through (fail-open).
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip exempt paths
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        # Skip the editorial blog-post-jobs family — it owns a dedicated
        # two-window limiter (deepsearch_api/ratelimit.py). The global 60/min
        # limiter would otherwise 429 the 61st submission in a minute, but we
        # want bursts up to the hourly cap. Prefix match (not exact) so the
        # /{job_id} poll sub-path is covered too; EXEMPT_PATHS is exact-match.
        if request.url.path.startswith("/internal/blog-post-jobs"):
            return await call_next(request)

        # Get Redis from app state
        redis: Optional[AsyncRedis] = getattr(request.app.state, "redis", None)

        if redis is None:
            # Fail open — no rate limiting if Redis is down.
            # This stays fail-OPEN on purpose: chat/auth/CRUD would rather be
            # briefly unmetered than unavailable. The library reveal family
            # does the opposite (route_limits.RouteRateLimiter falls back to an
            # in-process limiter) because there the limiter IS the security
            # boundary, not a damper.
            return await call_next(request)

        # Collapse dynamic library tails BEFORE the key is built, otherwise
        # every slug is its own bucket and breadth-first scraping is free.
        normalized_path = normalize_rate_limit_path(request.url.path)

        # Determine limits based on path
        if request.url.path.startswith("/api/v1/auth/"):
            max_requests = AUTH_RATE_LIMIT
            window = AUTH_WINDOW_SECONDS
        elif (
            "/messages" in request.url.path
            and request.method == "POST"
        ):
            max_requests = MESSAGE_SEND_RATE_LIMIT
            window = MESSAGE_SEND_WINDOW_SECONDS
        elif (
            request.url.path.endswith("/plans/redeem")
            and request.method == "POST"
        ):
            max_requests = REDEEM_RATE_LIMIT
            window = REDEEM_WINDOW_SECONDS
        elif is_public_library_item_path(normalized_path):
            # One aggregate bucket for every item page of a section.
            max_requests = LIBRARY_ITEM_RATE_LIMIT
            window = LIBRARY_ITEM_WINDOW_SECONDS
        else:
            # NB: /api/v1/library/full/{type}/:item lands here on the default
            # 60/min. That is fine — the authoritative bound on the paid bytes
            # is route_limits.library_rate_limit (20/min, VERIFIED identity).
            # The key below can only use the unverified JWT `sub`, which a
            # forged token can rotate at will (PART 9 trap 11), so the
            # middleware is not trusted to gate entitlement.
            max_requests = DEFAULT_RATE_LIMIT
            window = DEFAULT_WINDOW_SECONDS

        # Build identifier — CF-Connecting-IP once TRUST_CF_HEADERS is on,
        # X-Forwarded-For behind the Railway proxy until then.
        client_ip = client_ip_for_rate_limit(request)

        rate_key_id = client_ip  # default: IP-based

        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer ") and not request.url.path.startswith("/api/v1/auth/"):
            try:
                # Decode WITHOUT verification — just extract 'sub' for rate limiting.
                # Full verification happens in deps.get_current_user().
                # This key is therefore FORGEABLE and must never be the only
                # bound on a sensitive family; see route_limits.py.
                token = auth_header[7:]
                payload = pyjwt.decode(token, options={"verify_signature": False}, algorithms=["HS256", "ES256"])
                user_sub = payload.get("sub")
                if user_sub:
                    rate_key_id = f"user:{user_sub}"
            except Exception:
                pass  # Fall back to IP-based

        key = f"ratelimit:{rate_key_id}:{normalized_path}:{window}"

        try:
            now = time.time()
            window_start = now - window

            pipe = redis.pipeline()
            # Remove entries outside the current window
            pipe.zremrangebyscore(key, 0, window_start)
            # Add current request timestamp. The member carries a random suffix
            # because ZADD dedups on MEMBER: two concurrent requests landing on
            # the same float tick used to collapse into one entry (an
            # undercount). That was mostly harmless when every slug had its own
            # bucket; now that a whole library section shares one bucket, the
            # ISR renderer's concurrent bursts would hide behind it.
            pipe.zadd(key, {f"{now}:{uuid.uuid4().hex}": now})
            # Count requests in window
            pipe.zcard(key)
            # Set key expiry so it auto-cleans
            pipe.expire(key, window)
            results = await pipe.execute()

            current_count = results[2]  # ZCARD result
            remaining = max(0, max_requests - current_count)
            reset_at = int(now + window)

            if current_count > max_requests:
                return rate_limited_response(reset_at=reset_at, window_seconds=window)

            # Proceed with request
            response: Response = await call_next(request)
            if response.status_code == 429:
                # A downstream limiter (route_limits, deepsearch_api) already
                # refused this request and set its OWN budget headers. Do not
                # overwrite them with this middleware's much larger remaining —
                # the client would be told it has budget left on a 429.
                return response
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            response.headers["X-RateLimit-Reset"] = str(reset_at)
            return response

        except Exception as e:
            # Fail open — if Redis errors, allow the request
            logger.warning("Rate limiter error (failing open): %s", e)
            return await call_next(request)
