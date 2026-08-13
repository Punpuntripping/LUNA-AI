"""Origin lock — reject requests that did not arrive through Cloudflare.

Layer 0 of the defence stack (``.claude/plans/defence_in_depth.md`` §2, executed
as step 3.4 of ``.claude/plans/cloudflare_navigation_hardening.md``). Railway
publishes every service on a ``*.up.railway.app`` hostname that answers to
anyone, so every WAF rule, verified-bot gate and edge rate limit configured on
``rayhanai.com`` is exactly one hostname away from being bypassed. Railway
exposes no IP allowlist on public domains, so the boundary is a shared secret:

1. A Cloudflare Transform Rule (``http_request_late_transform``, live in the zone
   since 2026-07-28) injects ``X-Edge-Secret: <32B random>`` on every **proxied**
   request.
2. This middleware rejects anything reaching the origin without it — 403.

⚠ **DEFAULT OFF, and that is load-bearing.** Every DNS record in the zone is
still grey-clouded, so the Transform Rule is inert and **no request in production
today carries the header**. With ``EDGE_SECRET`` unset this middleware is a
straight pass-through; arming it before the orange cloud flips would 403 100% of
production traffic. Order of operations is: set ``EDGE_SECRET`` in Railway to the
value the Transform Rule injects → deploy → *then* flip the orange cloud.

⚠ **``/api/v1/health`` is exempt and must stay exempt.** ``railway.json`` sets
``healthcheckPath: /api/v1/health``, and Railway's healthcheck probes the
container directly — it never transits Cloudflare, so it can never carry the
header. Without the exemption every single deploy fails its healthcheck and rolls
back. This is the highest-risk line in the file.

**Scope of the lock.** It is a *network-path* assertion, not authentication. It
says "this request came through our edge"; it says nothing about who sent it.
JWT auth, the webhook/service secrets and the entitlement layer all still apply
underneath, unchanged.

**Config.** Read from the environment at construction (once per process), the
same way ``rate_limit.LIBRARY_ITEM_RATE_LIMIT`` reads its knob, rather than
through ``shared.config.Settings``. If it is ever promoted into ``Settings``,
note that ``Settings`` is ``case_sensitive=True`` and uses no ``validation_alias``
anywhere today, so the field name must equal the env var name exactly —
``EDGE_SECRET``.
"""
from __future__ import annotations

import hmac
import logging
import os
import time
from typing import Optional

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

# Env var carrying the value the Cloudflare Transform Rule injects. UNSET =>
# middleware disabled (see the module docstring — this is the safe default while
# the zone is grey-clouded).
EDGE_SECRET_ENV = "EDGE_SECRET"

# Header the Transform Rule sets. Matched case-insensitively (Starlette's
# Headers is case-insensitive), so the on-the-wire casing does not matter.
EDGE_SECRET_HEADER = "x-edge-secret"

# Paths served to callers that structurally CANNOT come through Cloudflare.
# Exact match after stripping a trailing slash. Keep this set as small as it can
# possibly be — every entry is a hole straight to the origin.
#
#   /api/v1/health — railway.json healthcheckPath. Railway probes the container
#                    directly; a proxied probe is not an option. Non-negotiable.
#
# Deliberately NOT exempt: /docs, /redoc, /openapi.json (already disabled unless
# DEBUG, and they should be behind the edge when they do exist) and
# /api/v1/_meta/observability (an ops read, reachable through the edge like
# everything else).
EXEMPT_PATHS = frozenset({"/api/v1/health"})

# Arabic 403 (Absolute Rule #5). Deliberately generic: it must not hint that a
# header is missing, or a prober learns the bypass shape from the error body.
ORIGIN_LOCK_MESSAGE = "غير مصرح بالوصول"

# Rejections are logged, but a scripted origin prober must not be able to fill
# the log budget — log the first one, then at most one line per interval with a
# suppressed count attached.
_LOG_THROTTLE_SECONDS = 60.0

# ============================================
# OBSERVE MODE — measure the header before trusting it
# (cloudflare_navigation_hardening.md §3.4 step 5, added 2026-08-13)
#
# Arming this lock is the one cutover step whose failure mode is TOTAL: a wrong
# assumption about the header and 100% of production 403s, with rollback costing
# an env-var change plus a redeploy. Step 5 of the plan therefore says to verify
# the header "actually arrives at the origin — and capture its on-the-wire shape
# while a client also sends the header" BEFORE step 6 sets ``EDGE_SECRET``.
#
# That was previously unobservable: with the lock disabled the middleware
# forwarded blind and logged nothing, so the only way to learn the shape was to
# arm it and find out. This closes that gap — while DISABLED, it reports what it
# would have matched against, and never enforces.
#
# ⚠ WHAT WE ARE ACTUALLY MEASURING IS THE *VALUE COUNT*, NOT PRESENCE.
# ``_header_matches`` uses ``getlist``, which only separates DISTINCT header
# LINES. Cloudflare appending its real secret alongside a client-supplied forgery
# yields two lines → the real one still matches → 200. But if the edge ever FOLDS
# them into one comma-joined line ("forged, real"), no single value equals the
# secret and the origin 403s its OWN legitimate traffic — a self-inflicted DoS
# any third party could trigger by pre-sending the header. Presence alone cannot
# distinguish those two worlds; the count and a comma can.
#
# ⚠ THE SECRET VALUE IS NEVER LOGGED. Lengths and a comma flag only — enough to
# tell a full secret from a truncated one, and an appended header from a folded
# one, without putting the credential in a log aggregator.
_OBSERVE_THROTTLE_SECONDS = 300.0


def origin_locked_response() -> JSONResponse:
    """The 403 body. Same envelope ``luna_exception_handler`` produces, built by
    hand because this middleware sits OUTSIDE ``ExceptionMiddleware`` — a raised
    ``LunaHTTPException`` up here would never reach the handler and would surface
    as a bare 500.

    ``no-store`` matters: cache rule 3.10 makes ``/api/v1/public/library/*``
    cache-eligible at the edge, and a 403 pinned into that shared cache would
    serve a refusal to every visitor of a real page.
    """
    return JSONResponse(
        status_code=403,
        content={
            "error": {
                "code": "FORBIDDEN",
                "message": ORIGIN_LOCK_MESSAGE,
                "status": 403,
            },
            "detail": ORIGIN_LOCK_MESSAGE,  # backward compatibility
        },
        headers={"Cache-Control": "private, no-store"},
    )


def is_exempt_path(path: str) -> bool:
    """True for a path that may reach the origin without the edge secret.

    The trailing slash is stripped before the lookup: FastAPI would answer
    ``/api/v1/health/`` with a 307 to ``/api/v1/health``, and a healthcheck
    configured with the stray slash must not be 403'd before it ever gets there.
    Cheap insurance on the one exemption that gates every deploy.
    """
    return (path.rstrip("/") or "/") in EXEMPT_PATHS


class OriginLockMiddleware:
    """Pure-ASGI gate on the presence of Cloudflare's ``X-Edge-Secret``.

    Raw ASGI rather than ``BaseHTTPMiddleware`` on purpose. This middleware only
    ever reads request headers and then either short-circuits or forwards the
    call untouched — it never inspects or rewrites a response. ``BaseHTTPMiddleware``
    would wrap every response in its streaming bridge for no benefit, adding an
    anyio task-group hop to the SSE path that Wave 7A spent a wave hardening.

    Disabled (``EDGE_SECRET`` unset) costs one ``is None`` check per request.
    """

    def __init__(self, app: ASGIApp, secret: Optional[str] = None) -> None:
        self.app = app
        # Explicit arg wins (tests, and it keeps the dependency visible); env is
        # the production path. Blank/whitespace is treated as unset — a Railway
        # variable left empty must not half-arm the lock.
        raw = secret if secret is not None else os.environ.get(EDGE_SECRET_ENV, "")
        cleaned = (raw or "").strip()
        self._expected: Optional[bytes] = cleaned.encode("utf-8") if cleaned else None
        self._last_log_at = 0.0
        self._suppressed = 0
        # Observe mode (disabled path only): shapes already reported, and when the
        # last line was emitted. Keyed by SHAPE rather than time so a NEW shape —
        # the forged-header probe step 5 calls for — is reported the moment it
        # appears instead of waiting out a throttle window. Bounded by
        # construction: the key space is (small value count) × (folded bool).
        self._observed_shapes: set[tuple[int, bool]] = set()
        self._last_observe_at = 0.0

        if self._expected is None:
            logger.info(
                "Origin lock DISABLED (%s unset) — every request passes through. "
                "Expected while the Cloudflare zone is grey-clouded.",
                EDGE_SECRET_ENV,
            )
        else:
            logger.info(
                "Origin lock ARMED — requests without %s are rejected 403 "
                "(exempt: %s)",
                EDGE_SECRET_HEADER,
                ", ".join(sorted(EXEMPT_PATHS)),
            )

    @property
    def enabled(self) -> bool:
        """Whether the lock is armed. Read by tests and by anything that wants to
        report the live posture without touching the environment again."""
        return self._expected is not None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Disabled, or lifespan/websocket traffic → forward untouched. The
        # disabled check is first so the off state is as close to free as it gets.
        if self._expected is None or scope["type"] != "http":
            if self._expected is None and scope["type"] == "http":
                self._observe(scope)
            await self.app(scope, receive, send)
            return

        if is_exempt_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        if not self._header_matches(Headers(scope=scope)):
            self._log_rejection(scope)
            await origin_locked_response()(scope, receive, send)
            return

        await self.app(scope, receive, send)

    # -- internals ---------------------------------------------------------

    def _header_matches(self, headers: Headers) -> bool:
        """Constant-time match against every copy of the header.

        ``getlist`` rather than ``get`` because Cloudflare *appends* to
        client-supplied headers on some paths (the same behaviour that makes
        leftmost X-Forwarded-For untrustworthy — plan step 3.5). If a forged
        value from the client survives alongside the edge's real one, taking only
        the first occurrence would 403 legitimate proxied traffic. Accepting any
        match weakens nothing: an attacker still has to produce the secret.

        Compared as bytes — ``compare_digest`` raises TypeError on non-ASCII
        ``str``, and this value comes straight off the wire, so a junk header
        would otherwise turn a clean 403 into a 500.
        """
        matched = False
        for value in headers.getlist(EDGE_SECRET_HEADER):
            # No early break: run every comparison so total work does not depend
            # on which copy matched.
            if hmac.compare_digest(value.strip().encode("utf-8"), self._expected or b""):
                matched = True
        return matched

    def _observe(self, scope: Scope) -> None:
        """Report the shape of ``X-Edge-Secret`` while the lock is DISABLED.

        Runs on the hot disabled path, so it is ordered cheapest-first: a float
        compare rejects the common case before anything touches the headers, and
        only a request in an unseen shape (or one past the throttle) pays for
        building ``Headers`` and formatting a line.

        Never enforces, never raises, and never logs the secret — see the block
        comment above for why the VALUE COUNT and the comma flag are the two
        things worth measuring.
        """
        now = time.monotonic()
        throttled = now - self._last_observe_at < _OBSERVE_THROTTLE_SECONDS
        values = [v.strip() for v in Headers(scope=scope).getlist(EDGE_SECRET_HEADER)]
        # A comma inside a value is the FOLD signature: the edge joined its own
        # header with a client-supplied one instead of appending a second line.
        folded = any("," in v for v in values)
        shape = (len(values), folded)
        if shape in self._observed_shapes and throttled:
            return
        self._observed_shapes.add(shape)
        self._last_observe_at = now
        logger.info(
            "Origin lock OBSERVE (not enforcing): %s %s — %d value(s) of %s, "
            "lengths=%s, folded=%s. Arming is safe only while a legitimate "
            "request yields >=1 value and folded=False.",
            scope.get("method", "?"),
            scope.get("path", "?"),
            len(values),
            EDGE_SECRET_HEADER,
            [len(v) for v in values],
            folded,
        )

    def _log_rejection(self, scope: Scope) -> None:
        """Throttled WARNING. During cutover these lines are the signal that
        something legitimate is reaching the origin off-edge (an ISR fetch, a
        webhook, a monitor) — so they must be visible, but never floodable."""
        self._suppressed += 1
        now = time.monotonic()
        if now - self._last_log_at < _LOG_THROTTLE_SECONDS:
            return
        client = scope.get("client")
        logger.warning(
            "Origin lock rejected %s %s from %s (%d rejection(s) since last log) "
            "— request did not arrive through Cloudflare",
            scope.get("method", "?"),
            scope.get("path", "?"),
            client[0] if client else "unknown",
            self._suppressed,
        )
        self._last_log_at = now
        self._suppressed = 0


__all__ = [
    "EDGE_SECRET_ENV",
    "EDGE_SECRET_HEADER",
    "EXEMPT_PATHS",
    "ORIGIN_LOCK_MESSAGE",
    "OriginLockMiddleware",
    "is_exempt_path",
    "origin_locked_response",
]
