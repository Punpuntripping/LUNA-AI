"""Product analytics beacon — ``POST /api/v1/public/events``.

Phase 0 of ``.claude/plans/product_analytics.md`` (§5.5). Modelled on
``public_ask.py``, which is this codebase's anonymous-POST precedent: the router
declares ``prefix="/api/v1"`` itself and the endpoint is anonymous purely by
OMITTING ``Depends(get_current_user)`` — there is no global auth middleware here,
so leaving the dependency off is what makes a route public.

WHAT MAKES THIS ENDPOINT DIFFERENT FROM EVERY OTHER ONE IN THE BACKEND
----------------------------------------------------------------------
**It always answers 204.** Not "usually": there is no code path that returns a
4xx or 5xx to the caller. Plan §7 T9 — a reader must never see a degraded page
because a tracker failed — and the client is fire-and-forget
(``navigator.sendBeacon``), so an error response would be dropped unread anyway
while costing us a red line in the browser console and a Logfire error span per
visitor. Bad batches, unknown events, bot traffic, a rate-limit refusal and a
dead table are all the same thing to the caller: 204, nothing written.

That is also why the body is parsed BY HAND instead of through a Pydantic body
model. A declared model would hand FastAPI a 422 on malformed JSON, and
``sendBeacon`` sends ``text/plain`` unless the client wraps the payload in a
typed ``Blob`` — reading the raw bytes makes the content type irrelevant and
keeps the 204 promise absolute.

FIVE GUARDS, IN ORDER (cheapest first, all before any DB work)
--------------------------------------------------------------
1. **Verified-bot drop** (§7 T1). Googlebot *renders JavaScript* and the library
   wings are built for crawlers, so it would execute the tracker and pollute
   every metric. ``AnonCtaPopup`` sidesteps this today only because Googlebot
   does not scroll; ``page_view`` has no such natural immunity.
2. **Rate limit**, keyed on the client IP via the shared ``resolve_client_ip``
   trust boundary. The IP is used for this and NOTHING else — it is never passed
   to the service layer and can never reach a row (plan §2).
3. **Body bounds** — size, shape, and ``MAX_BATCH_EVENTS``.
4. **Opportunistic auth** — a bearer token is read if present, never required.
5. **Taxonomy + field sanitation**, in ``analytics_service``.

⚠ THE IP AND THE RAW USER-AGENT NEVER REACH A ROW. The UA is turned into three
buckets by ``analytics_service.classify_client`` and discarded; the IP exists
only as a Redis rate-limit key. Both are stated in the migration's table comment
as the table's privacy contract — this module is where that contract is kept.

⚠ Do NOT add this path to ``origin_lock``'s ``EXEMPT_PATHS`` (§7 T8). The beacon
is browser traffic and must transit the edge like all of it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from supabase import Client as SupabaseClient

from backend.app.deps import get_supabase
from backend.app.errors import LunaHTTPException
from backend.app.middleware.route_limits import RouteRateLimiter
from backend.app.services import analytics_service
from shared.auth.jwt import extract_user
from shared.db.run import run_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["analytics"])

# Auth is OPTIONAL here: auto_error=False means a missing or malformed
# Authorization header yields None instead of a 401.
_bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------

# A whole flush is one JSON object with at most MAX_BATCH_EVENTS entries; 64 KB
# is generous for that and cheap to refuse. Checked against Content-Length AND
# the bytes actually read, because the header is client-supplied.
MAX_BODY_BYTES = 64_000


def _env_int(name: str, default: int) -> int:
    """Read a positive int from the environment, falling back on junk values."""
    try:
        value = int(os.environ.get(name, "").strip() or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


# Per-IP beacon budget. Deliberately the same number as the global middleware's
# DEFAULT_RATE_LIMIT, which already covers this path: a bigger number here would
# be dead code (the middleware would refuse first) and a much smaller one would
# make the two limiters disagree about what "one visitor's worth of traffic"
# means. This limiter earns its place by doing two things the middleware cannot:
# it keys on the IP even for signed-in callers (the middleware keys those off an
# UNVERIFIED JWT `sub` — forgeable, see route_limits.py), and its refusal is
# converted into a silent 204 drop below instead of a 429 the beacon would
# ignore anyway.
ANALYTICS_RATE_LIMIT = _env_int("ANALYTICS_RATE_LIMIT", 60)
ANALYTICS_RATE_WINDOW_SECONDS = 60

# Its own scope => its own bucket: beacon traffic must never eat the library
# reveal family's budget, or a chatty tab could throttle a paying reader.
analytics_rate_limit = RouteRateLimiter(
    scope="analytics",
    limit=ANALYTICS_RATE_LIMIT,
    window_seconds=ANALYTICS_RATE_WINDOW_SECONDS,
)


# ---------------------------------------------------------------------------
# Bot detection (§7 T1)
# ---------------------------------------------------------------------------

VERIFIED_BOT_HEADER = "x-verified-bot"

_TRUTHY_HEADER_VALUES = {"1", "true", "yes", "on"}

# Cheap UA fallback for the crawlers that reach us without the edge header —
# today the zone is only partly proxied and ``X-Verified-Bot`` is absent on
# plenty of real crawler traffic. Substring match on a lowercased UA.
_BOT_UA_TOKENS = (
    "bot",
    "crawl",
    "spider",
    "slurp",
    "headlesschrome",
    "lighthouse",
    "phantomjs",
    "puppeteer",
    "playwright",
)


def is_bot_request(request: Request) -> bool:
    """True when this batch should be thrown away as non-human traffic.

    ⚠ THE TRUST DIRECTION IS INVERTED HERE, AND THAT IS WHY THIS DOES NOT REUSE
    ``public_library.is_verified_crawler``. There, ``X-Verified-Bot`` GRANTS an
    exemption (past the hub depth cap), so it may only be believed once
    ``TRUST_CF_HEADERS`` is on — otherwise anyone could forge their way past a
    control. Here the same header only causes us to DISCARD data: the worst a
    forger achieves is making their own visit invisible in our analytics, which
    costs them everything and us one row. So it is honoured unconditionally, and
    a plain-UA heuristic is honoured too.

    Every value of the header must be truthy (``getlist`` + comma-split), the
    same all-copies rule ``is_verified_crawler`` applies — a client that
    pre-sends ``X-Verified-Bot: 1`` and has the edge append ``0`` should not get
    to pick which copy is read.

    Never raises: a bot check must not be able to break the beacon.
    """
    try:
        claims = request.headers.getlist(VERIFIED_BOT_HEADER)
        values = [
            part.strip().lower()
            for raw in claims
            for part in raw.split(",")
            if part.strip()
        ]
        if values and all(v in _TRUTHY_HEADER_VALUES for v in values):
            return True

        ua = (request.headers.get("user-agent") or "").lower()
        return any(token in ua for token in _BOT_UA_TOKENS)
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NO_CONTENT = 204


def _no_content() -> Response:
    """The ONE response this endpoint ever produces."""
    return Response(status_code=_NO_CONTENT)


async def _read_batch(request: Request) -> Optional[list[Any]]:
    """The event list, or ``None`` when the body is unusable/oversized.

    Reads raw bytes rather than a declared Pydantic body so that (a) malformed
    JSON is a silent drop instead of a 422 and (b) ``sendBeacon``'s ``text/plain``
    content type is irrelevant.

    An oversized batch is REFUSED WHOLE rather than truncated: a client sending
    21 events is a client bug, and silently keeping 20 of them would hide it
    while still corrupting whatever the 21st was part of.
    """
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        logger.info("analytics: body declared %s bytes — dropped", declared)
        return None

    try:
        raw = await request.body()
    except Exception as exc:  # noqa: BLE001 — client disconnect mid-body
        logger.debug("analytics: body read failed: %s", exc)
        return None

    if not raw or len(raw) > MAX_BODY_BYTES:
        return None

    try:
        payload = json.loads(raw)
    except Exception:  # noqa: BLE001
        logger.debug("analytics: unparseable body — dropped")
        return None

    if not isinstance(payload, dict):
        return None

    events = payload.get("events")
    if not isinstance(events, list):
        return None
    if len(events) > analytics_service.MAX_BATCH_EVENTS:
        logger.info(
            "analytics: batch of %d exceeds the max of %d — dropped whole",
            len(events),
            analytics_service.MAX_BATCH_EVENTS,
        )
        return None
    return events


async def _resolve_actor(
    credentials: Optional[HTTPAuthorizationCredentials],
) -> tuple[Optional[str], str]:
    """``(auth_id, user_type)`` — opportunistic, never fatal.

    A token that verifies makes the actor ``authed``; anything else (absent,
    expired, forged, JWKS unreachable) is ``anon``. No 401 and no 503 can escape
    this endpoint, so the auth outage that ``get_current_user_optional``
    deliberately propagates is swallowed here — analytics degrading to anon for
    the duration of a JWKS outage is strictly better than a beacon that errors.
    """
    if credentials is None or not credentials.credentials:
        return None, analytics_service.USER_TYPE_ANON
    try:
        # extract_user's JWKS fetch is sync urllib — keep it off the event loop,
        # same as deps.get_current_user and route_limits.resolve_identity.
        user = await asyncio.to_thread(extract_user, credentials.credentials)
    except Exception as exc:  # noqa: BLE001
        logger.debug("analytics: token not verified (%s) — recording as anon", exc)
        return None, analytics_service.USER_TYPE_ANON
    if user is None or not user.auth_id:
        return None, analytics_service.USER_TYPE_ANON
    return user.auth_id, analytics_service.USER_TYPE_AUTHED


# ---------------------------------------------------------------------------
# POST /public/events — anonymous beacon
# ---------------------------------------------------------------------------


@router.post("/public/events", status_code=_NO_CONTENT)
async def collect_events(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    supabase: SupabaseClient = Depends(get_supabase),
) -> Response:
    """Record a batch of visitor-behaviour events. Always 204.

    Body (any content type — ``sendBeacon`` sends ``text/plain``)::

        {"events": [
          {"event_name": "page_view",
           "session_key": "<sessionStorage key>",
           "path": "/library/labor/regulations",     # optional
           "props": {"gate_kind": "hub_wall"}}       # optional, scalars only
        ]}

    ``event_name`` must be one of the 22 names in the §3/§3b taxonomy; unknown
    names are dropped individually while the rest of the batch is kept. Rows are
    written in the order sent (``event_id`` is the tiebreaker the chat-depth
    queries walk).
    """
    # 1. Bots (§7 T1) — before anything else, including the rate limiter: a
    #    crawler must not consume a human's budget on its way to being ignored.
    if is_bot_request(request):
        return _no_content()

    # 2. Rate limit, keyed on the client IP. The limiter raises a 429
    #    LunaHTTPException on refusal; the beacon contract says 204, so the
    #    refusal is caught and turned into a silent drop. `credentials=None` is
    #    passed deliberately — this budget is per IP even for signed-in callers,
    #    so one account cannot mint a fresh bucket per tab.
    try:
        await analytics_rate_limit(request, None)
    except LunaHTTPException:
        return _no_content()
    except Exception as exc:  # noqa: BLE001 — a limiter fault must not 500
        logger.warning("analytics: rate limiter error (allowing): %s", exc)

    # 3. Body bounds + batch cap.
    events = await _read_batch(request)
    if not events:
        return _no_content()

    # 4. Opportunistic identity. user_type is decided by the TOKEN, not by
    #    whether the users row resolves: the actor was signed in either way, and
    #    that is exactly the distinction the user_type column exists to preserve
    #    (see migration 139's comment).
    auth_id, user_type = await _resolve_actor(credentials)

    user_id: Optional[str] = None
    if auth_id:
        try:
            user_id = await run_db(
                analytics_service.lookup_user_id, supabase, auth_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("analytics: user_id lookup failed: %s", exc)

    # 5. Taxonomy + sanitation. The raw UA dies in classify_client; the IP was
    #    never passed in at all.
    client = analytics_service.classify_client(
        user_agent=request.headers.get("user-agent"),
        ch_ua_mobile=request.headers.get("sec-ch-ua-mobile"),
        ch_ua_platform=request.headers.get("sec-ch-ua-platform"),
        ch_ua=request.headers.get("sec-ch-ua"),
    )

    rows = analytics_service.build_event_rows(
        events, client=client, user_id=user_id, user_type=user_type
    )
    if not rows:
        return _no_content()

    try:
        await run_db(analytics_service.insert_events, supabase, rows)
    except Exception as exc:  # noqa: BLE001 — insert_events already swallows,
        # but the belt stays on: nothing below the beacon may surface an error.
        logger.warning("analytics: batch insert raised: %s", exc)

    return _no_content()
