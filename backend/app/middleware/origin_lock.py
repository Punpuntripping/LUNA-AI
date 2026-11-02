"""Origin lock — reject requests that did not arrive through Cloudflare.

Layer 0 of the defence stack (``.claude/plans/defence_in_depth.md`` §2, executed
as step 3.4 of ``.claude/plans/cloudflare_navigation_hardening.md``). Railway
publishes every service on a ``*.up.railway.app`` hostname that answers to
anyone, so every WAF rule, verified-bot gate and edge rate limit configured on
``rayhanai.com`` is exactly one hostname away from being bypassed. Railway
exposes no IP allowlist on public domains, so the boundary is a shared secret:

1. A Cloudflare Transform Rule (``http_request_late_transform``, live in the zone
   since 2026-07-28) injects ``X-Edge-Secret: <32B random>`` on every **proxied**
   request. The zone has been proxied since 2026-08, so the header really does
   reach the origin on traffic that came through the edge.
2. This middleware compares it, and depending on the mode rejects anything
   reaching the origin without it — 403.

⚠ **``EDGE_SECRET`` ARMS THE LOCK, ``ORIGIN_LOCK_MODE`` DECIDES WHAT ARMED MEANS.**
Until the Phase 2 cutover sets them on luna-backend, this middleware is inert.

=============  ====================  ============================================
EDGE_SECRET    ORIGIN_LOCK_MODE      Behaviour
=============  ====================  ============================================
unset/blank    anything              pass-through + the stdlib shape log
set            ``enforce``           compare, 403 on failure, report to Logfire
set            ``observe``           compare, NEVER block, report to Logfire
set            ``off``               pass-through, no compare, no report
set            unset, blank, other   as ``observe``, plus one error-level report
=============  ====================  ============================================

⚠ **ONLY THE WORD ``enforce`` REFUSES A REQUEST.** An unset mode used to enforce,
which meant clearing the variable to turn the lock off started enforcing on the
next redeploy (security review 2026-11-02). An image from before that change still
behaves that way, so it must never receive ``EDGE_SECRET``. The value in the
Cloudflare rule, the value here and the value the frontend compares must all agree
before anything enforces. The ordered cutover is
``.claude/plans/library_guardrails_hardening.md`` Phase 2.

A typo'd or differently-cased mode (``Enforce``) lands on observe the same way, so
a mistake can never start enforcing. ``enforce`` with no secret passes everything,
and that is reported at error level, because the operator believes the lock is on.

``TRUST_CF_HEADERS`` depends on this lock: see ``lock_is_enforcing``.

⚠ **``/api/v1/health`` is exempt and must stay exempt.** ``railway.json`` sets
``healthcheckPath: /api/v1/health``, and Railway's healthcheck probes the
container directly — it never transits Cloudflare, so it can never carry the
header. Without the exemption every single deploy fails its healthcheck and rolls
back. This is the highest-risk line in the file.

**Scope of the lock.** It is a *network-path* assertion, not authentication. It
says "this request came through our edge"; it says nothing about who sent it.
JWT auth, the webhook/service secrets and the entitlement layer all still apply
underneath, unchanged.

**Config.** Both variables are read from the environment at construction (once
per process), the same way ``rate_limit.LIBRARY_ITEM_RATE_LIMIT`` reads its knob,
rather than through ``shared.config.Settings``. If either is ever promoted into
``Settings``, note that ``Settings`` is ``case_sensitive=True`` and uses no
``validation_alias`` anywhere today, so the field name must equal the env var
name exactly — ``EDGE_SECRET``, ``ORIGIN_LOCK_MODE``.
"""
from __future__ import annotations

import hmac
import ipaddress
import logging
import os
import time
from typing import Optional

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from shared.observability import get_logfire

logger = logging.getLogger(__name__)

# Stdlib logging does not reach Logfire (shared/observability.py installs no
# LoggingHandler), so the signals below go through logfire directly. A no-op shim
# when the SDK is missing; an unconfigured SDK is a no-op on its own.
_logfire = get_logfire()

# Env var carrying the value the Cloudflare Transform Rule injects. UNSET =>
# middleware disabled (see the module docstring).
EDGE_SECRET_ENV = "EDGE_SECRET"

# Env var selecting what an armed lock does. See the table in the module docstring.
ORIGIN_LOCK_MODE_ENV = "ORIGIN_LOCK_MODE"
MODE_OFF = "off"
MODE_OBSERVE = "observe"
MODE_ENFORCE = "enforce"
_KNOWN_MODES = frozenset({MODE_OFF, MODE_OBSERVE, MODE_ENFORCE})

# A bad mode is echoed so the typo is obvious, but only if it is short enough to
# BE a typo. "enforce" and "observe" are 7 characters, and the likeliest long
# value in this variable is the secret itself, pasted into the wrong field.
_MAX_MODE_ECHO = 16

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
# SHAPE OBSERVE — measure the header before trusting it
# (cloudflare_navigation_hardening.md §3.4 step 5, added 2026-08-13)
#
# This is the stdlib log of the DISABLED path, which has no secret to compare
# against. ``ORIGIN_LOCK_MODE=observe`` is a different thing: it runs while ARMED,
# compares for real, and reports through the signals further down.
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

# ============================================
# SIGNALS — what each checked request looked like, in Logfire
# (library_guardrails_hardening.md Phase 1a)
#
# Emitted in ``observe`` and ``enforce``. This is the measurement the cutover is
# gated on, and the input to the later "someone is probing" alert.
#
# ⚠ EVERY ATTRIBUTE IS FROM A CLOSED VOCABULARY OR IS AN INT. Host, path and
# User-Agent are attacker-controlled. Nothing raw reaches the attributes or the
# throttle table, so the table is bounded by construction at
# len(HOST_CLASSES) × len(OUTCOMES) × len(PATH_WINGS) keys, whatever is sent. The
# classes are labels for reading traffic, never a security decision: Host in
# particular can be forged, so ``edge_api`` means "claimed to be", and the secret
# remains the only thing that proves a request came through the edge.
#
# ⚠ THE WORDS MUST SURVIVE THE LOGFIRE SCRUBBER, WHICH ALSO MATCHES VALUES. A value
# CONTAINING a pattern is replaced (``mobile_app`` comes back as
# "[Scrubbed due to 'mobile']"), and the extra patterns in shared/observability.py
# are PII stems such as ``mobile``, ``phone`` and ``email``. That is why the auth
# routes are the ``account`` wing and the app is ``native_app``.
# ``test_vocabulary_survives_the_scrubber`` pins this.
#
# ⚠ THE SECRET IS NEVER AN ATTRIBUTE, nor is its length.
SIGNAL_EVENT = "origin_lock.outcome"

OUTCOME_MATCH = "match"
OUTCOME_MISSING = "missing"  # no header line at all
OUTCOME_MISMATCH = "mismatch"  # at least one line, none equal the secret
OUTCOME_FOLDED = "folded"  # a mismatch with a comma: see the fold note above
OUTCOMES = (OUTCOME_MATCH, OUTCOME_MISSING, OUTCOME_MISMATCH, OUTCOME_FOLDED)

_HOST_API = "edge_api"
_HOST_EDGE_OTHER = "edge_other"
_HOST_RAILWAY_PUBLIC = "railway_public"
_HOST_RAILWAY_PRIVATE = "railway_private"
_HOST_LOCAL = "local"
_HOST_IP_LITERAL = "ip_literal"
_HOST_OTHER = "other"
_HOST_NONE = "none"
HOST_CLASSES = (
    _HOST_API,
    _HOST_EDGE_OTHER,
    _HOST_RAILWAY_PUBLIC,
    _HOST_RAILWAY_PRIVATE,
    _HOST_LOCAL,
    _HOST_IP_LITERAL,
    _HOST_OTHER,
    _HOST_NONE,
)

# Slicing is a cheap guard against a hostile value; no real hostname comes close.
_MAX_HOST_CHARS = 256
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# First match wins, so the specific prefixes must stay above ``/api/v1/``.
# ``/api/v1/health`` never gets here: it is exempt before any signal.
_PATH_WING_PREFIXES = (
    ("/api/v1/public/library", "library"),
    ("/api/v1/library", "library"),
    ("/api/v1/public/blog", "blog"),
    ("/api/v1/blog", "blog"),
    ("/api/v1/public/", "public"),
    ("/api/v1/auth/", "account"),
    ("/api/v1/payments/", "payments"),
    ("/internal/", "internal"),
    ("/api/v1/", "app"),
)
_PATH_OTHER = "other"
PATH_WINGS = tuple(dict.fromkeys(w for _, w in _PATH_WING_PREFIXES)) + (_PATH_OTHER,)

_UA_OTHER = "other"
_UA_NONE = "none"
_MAX_UA_CHARS = 256
# First match wins. Crawlers come first because most of them also say "Mozilla".
_UA_RULES = (
    ("crawler", ("bot", "crawl", "spider", "slurp", "externalagent", "externalhit")),
    ("service", ("pg_net", "rayhan-marketing")),
    ("native_app", ("okhttp", "cfnetwork", "dalvik")),
    (
        "script",
        (
            "curl", "wget", "python", "httpx", "aiohttp", "go-http", "java/",
            "axios", "undici", "node", "libwww", "scrapy", "postman", "insomnia",
            "ruby", "php",
        ),
    ),
    ("browser", ("mozilla", "opera")),
)
UA_CLASSES = tuple(c for c, _ in _UA_RULES) + (_UA_OTHER, _UA_NONE)

# At most one line per key per interval, plus one the first time a key is seen,
# so a NEW combination is visible at once rather than after a wait.
_SIGNAL_FLUSH_SECONDS = 60.0

# A line costs about 0.5 ms in the SDK, on the event loop (measured with a batch
# processor). A prober picks its own Host and path, so it can light up every key,
# and flushing them all in one go would stall every live SSE stream for over
# 100 ms each interval. Keys past this cap wait for the next request instead.
_MAX_FLUSH_LINES_PER_REQUEST = 8


def classify_host(raw: Optional[str]) -> str:
    """The Host header as one of ``HOST_CLASSES``. Never returns the input."""
    host = (raw or "").strip().lower()[:_MAX_HOST_CHARS]
    if not host:
        return _HOST_NONE
    if host.startswith("["):  # bracketed IPv6, optionally with a port
        name = host[1:].partition("]")[0]
    else:
        name = host.partition(":")[0]
    name = name.rstrip(".")
    if name == "api.rayhanai.com":
        return _HOST_API
    if name == "rayhanai.com" or name.endswith(".rayhanai.com"):
        return _HOST_EDGE_OTHER
    if name.endswith(".up.railway.app"):
        return _HOST_RAILWAY_PUBLIC
    if name.endswith(".railway.internal"):
        return _HOST_RAILWAY_PRIVATE
    if name in _LOCAL_HOSTS:
        return _HOST_LOCAL
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return _HOST_OTHER
    return _HOST_IP_LITERAL


def classify_path(path: str) -> str:
    """The request path as one of ``PATH_WINGS``. Never returns the input."""
    for prefix, wing in _PATH_WING_PREFIXES:
        if path.startswith(prefix):
            return wing
    return _PATH_OTHER


def classify_user_agent(raw: Optional[str]) -> str:
    """The User-Agent as one of ``UA_CLASSES``. Never returns the input."""
    agent = (raw or "").strip().lower()[:_MAX_UA_CHARS]
    if not agent:
        return _UA_NONE
    for label, needles in _UA_RULES:
        if any(needle in agent for needle in needles):
            return label
    return _UA_OTHER


class _Pending:
    """Requests folded into a key since its last line. Fixed size per key."""

    __slots__ = ("count", "value_count", "ua_class")

    def __init__(self) -> None:
        self.count = 0
        # The most header lines seen in the window: a sample could miss the one
        # request that shows a client-supplied copy surviving next to the edge's.
        self.value_count = 0
        # Taken from the window's first request, so the User-Agent is classified
        # once per window instead of once per request.
        self.ua_class = _UA_NONE


def _mode_echo(raw: str, secret: Optional[bytes]) -> str:
    """The bad mode as it may be reported. See ``_MAX_MODE_ECHO``."""
    if len(raw) > _MAX_MODE_ECHO or raw.encode("utf-8") == secret:
        return "<redacted>"
    return raw


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


def lock_is_enforcing() -> bool:
    """Is this process's environment refusing secret-less requests right now?

    Read fresh on every call, like ``rate_limit.trust_cf_headers``, which depends
    on it: ``CF-Connecting-IP`` and ``X-Verified-Bot`` only mean anything while
    every request is proven to have come through Cloudflare. Parsed the same way
    as ``OriginLockMiddleware.__init__``, so the two cannot disagree.
    """
    secret = os.environ.get(EDGE_SECRET_ENV, "").strip()
    mode = os.environ.get(ORIGIN_LOCK_MODE_ENV, "").strip()
    return bool(secret) and mode == MODE_ENFORCE


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

    Disabled (``EDGE_SECRET`` unset) never compares and never emits a signal. The
    armed modes pay for the compare plus a dict update per request; everything
    that talks to Logfire is throttled and cannot raise into the request.
    """

    def __init__(
        self,
        app: ASGIApp,
        secret: Optional[str] = None,
        mode: Optional[str] = None,
    ) -> None:
        self.app = app
        # Explicit arg wins (tests, and it keeps the dependency visible); env is
        # the production path. Blank/whitespace is treated as unset — a Railway
        # variable left empty must not half-arm the lock.
        raw = secret if secret is not None else os.environ.get(EDGE_SECRET_ENV, "")
        cleaned = (raw or "").strip()
        self._expected: Optional[bytes] = cleaned.encode("utf-8") if cleaned else None

        # Same contract as the secret: explicit arg first, then env, blank = unset.
        raw_mode = mode if mode is not None else os.environ.get(ORIGIN_LOCK_MODE_ENV, "")
        cleaned_mode = (raw_mode or "").strip()
        if self._expected is None:
            # Nothing to compare against, so the mode has no effect at all and a
            # bad value is not reported until a secret makes it matter. The one
            # exception is ``enforce``: that operator believes the lock is on.
            self._mode = MODE_OFF
            if cleaned_mode == MODE_ENFORCE:
                self._report_disarmed()
        elif cleaned_mode in _KNOWN_MODES:
            self._mode = cleaned_mode
        else:
            # Unset, blank or a typo. Only the word itself may start refusing, so
            # clearing this variable can never lock real traffic out on a redeploy.
            self._mode = MODE_OBSERVE
            self._report_bad_mode(cleaned_mode)

        self._last_log_at = 0.0
        self._suppressed = 0
        # Observe mode (disabled path only): shapes already reported, and when the
        # last line was emitted. Keyed by SHAPE rather than time so a NEW shape —
        # the forged-header probe step 5 calls for — is reported the moment it
        # appears instead of waiting out a throttle window. Bounded by
        # construction: the key space is (small value count) × (folded bool).
        self._observed_shapes: set[tuple[int, bool]] = set()
        self._last_observe_at = 0.0
        # Signals (armed modes only). One entry per (host_class, outcome,
        # path_wing), bounded by construction — see the SIGNALS block above. The
        # event loop runs this without an await in the middle, so no lock.
        self._pending: dict[tuple[str, str, str], _Pending] = {}
        self._flush_queue: list[tuple[str, str, str]] = []
        self._last_flush_at = time.monotonic()
        self._signal_failed = False

        if self._expected is None:
            logger.info(
                "Origin lock DISABLED (%s unset) — every request passes through.",
                EDGE_SECRET_ENV,
            )
        elif self._mode == MODE_ENFORCE:
            logger.info(
                "Origin lock ARMED — requests without %s are rejected 403 "
                "(exempt: %s)",
                EDGE_SECRET_HEADER,
                ", ".join(sorted(EXEMPT_PATHS)),
            )
        elif self._mode == MODE_OBSERVE:
            logger.info(
                "Origin lock measuring — %s is compared and reported, never "
                "rejected (mode=observe)",
                EDGE_SECRET_HEADER,
            )
        else:
            logger.info(
                "Origin lock switched off by %s=off — every request passes through.",
                ORIGIN_LOCK_MODE_ENV,
            )

    @property
    def enabled(self) -> bool:
        """Whether this lock can reject a request. Read by tests and by anything
        that wants to report the live posture without touching the environment
        again. False while measuring: ``observe`` compares but never blocks."""
        return self._expected is not None and self._mode == MODE_ENFORCE

    @property
    def mode(self) -> str:
        """The mode in effect after parsing: ``off``, ``observe`` or ``enforce``.
        ``off`` while ``EDGE_SECRET`` is unset, whatever the variable says."""
        return self._mode

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Disabled, or lifespan/websocket traffic → forward untouched. The
        # disabled check is first so the off state is as close to free as it gets.
        if self._expected is None or scope["type"] != "http":
            if self._expected is None and scope["type"] == "http":
                self._observe(scope)
            await self.app(scope, receive, send)
            return

        if self._mode == MODE_OFF or is_exempt_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        matched = self._header_matches(headers)
        self._signal(scope, headers, matched)

        if not matched and self._mode == MODE_ENFORCE:
            self._log_rejection(scope)
            await origin_locked_response()(scope, receive, send)
            return

        await self.app(scope, receive, send)

    # -- internals ---------------------------------------------------------

    def _report_bad_mode(self, raw: str) -> None:
        """One error-level report per process. Stdlib for Railway's log view and
        Logfire for the dashboards, because neither reaches the other."""
        shown = _mode_echo(raw, self._expected)
        logger.error(
            "Missing or unrecognised %s=%r — treating it as observe, so nothing is enforced. "
            "Valid values: %s.",
            ORIGIN_LOCK_MODE_ENV,
            shown,
            ", ".join(sorted(_KNOWN_MODES)),
        )
        try:
            _logfire.error("origin_lock.bad_mode", configured_value=shown)
        except Exception as exc:  # noqa: BLE001 — telemetry must never stop a boot
            logger.warning("Origin lock bad-mode report failed: %s", type(exc).__name__)

    def _report_disarmed(self) -> None:
        """``enforce`` with no secret passes every request, and the quiet that
        follows looks like a healthy night. One error-level report per process."""
        logger.error(
            "%s=%s but %s is unset — the lock is OFF and every request passes.",
            ORIGIN_LOCK_MODE_ENV,
            MODE_ENFORCE,
            EDGE_SECRET_ENV,
        )
        try:
            _logfire.error("origin_lock.disarmed")
        except Exception as exc:  # noqa: BLE001 — telemetry must never stop a boot
            logger.warning("Origin lock disarmed report failed: %s", type(exc).__name__)

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

    def _signal(self, scope: Scope, headers: Headers, matched: bool) -> None:
        """Fold this request into its key, emitting whatever is due.

        A key seen for the first time reports immediately, with ``count=1``. After
        that its requests accumulate and go out on the next flush, so the ``count``
        of every line for a key adds up to the requests that key really saw.

        Never raises. A telemetry failure is worth one stdlib line and no more; it
        must not fail or slow the request that happened to trigger it.
        """
        try:
            values = headers.getlist(EDGE_SECRET_HEADER)
            if matched:
                outcome = OUTCOME_MATCH
            elif not values:
                outcome = OUTCOME_MISSING
            elif any("," in value for value in values):
                outcome = OUTCOME_FOLDED
            else:
                outcome = OUTCOME_MISMATCH

            key = (
                classify_host(headers.get("host")),
                outcome,
                classify_path(scope.get("path", "")),
            )
            pending = self._pending.get(key)
            if pending is None:
                self._pending[key] = _Pending()
                self._emit(
                    key,
                    classify_user_agent(headers.get("user-agent")),
                    len(values),
                    1,
                )
            else:
                if pending.count == 0:
                    pending.ua_class = classify_user_agent(headers.get("user-agent"))
                pending.count += 1
                pending.value_count = max(pending.value_count, len(values))

            self._flush_due()
        except Exception as exc:  # noqa: BLE001 — see the docstring
            if not self._signal_failed:
                self._signal_failed = True
                # The class only: an exception message is not worth the risk of
                # echoing a header value into a log.
                logger.warning(
                    "Origin lock signal failed, continuing without it: %s",
                    type(exc).__name__,
                )

    def _flush_due(self) -> None:
        """Emit every key with folded requests, at most once per interval.

        Driven by whichever request arrives, on any key. Per-key flushing would
        strand a burst: a prober sending 1,000 requests and stopping leaves its
        key with no later request to carry them, and the alert summing ``count``
        would read 1. The cost of having no timer is that the tail of a window can
        wait for the next request, and is lost if the process exits first.

        The keys due are queued when the interval ends and drained a few per
        request (see ``_MAX_FLUSH_LINES_PER_REQUEST``), so one key still gets one
        line per interval however long the drain takes.
        """
        if not self._flush_queue:
            now = time.monotonic()
            if now - self._last_flush_at < _SIGNAL_FLUSH_SECONDS:
                return
            self._last_flush_at = now
            self._flush_queue = [k for k, p in self._pending.items() if p.count]

        for _ in range(min(len(self._flush_queue), _MAX_FLUSH_LINES_PER_REQUEST)):
            key = self._flush_queue.pop()
            pending = self._pending[key]
            count, pending.count = pending.count, 0
            value_count, pending.value_count = pending.value_count, 0
            self._emit(key, pending.ua_class, value_count, count)

    def _emit(
        self,
        key: tuple[str, str, str],
        ua_class: str,
        value_count: int,
        count: int,
    ) -> None:
        host_class, outcome, path_wing = key
        _logfire.info(
            SIGNAL_EVENT,
            host_class=host_class,
            outcome=outcome,
            value_count=value_count,
            path_wing=path_wing,
            ua_class=ua_class,
            enforced=self._mode == MODE_ENFORCE,
            count=count,
        )

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
    "HOST_CLASSES",
    "MODE_ENFORCE",
    "MODE_OBSERVE",
    "MODE_OFF",
    "ORIGIN_LOCK_MESSAGE",
    "ORIGIN_LOCK_MODE_ENV",
    "OUTCOMES",
    "OriginLockMiddleware",
    "PATH_WINGS",
    "SIGNAL_EVENT",
    "UA_CLASSES",
    "classify_host",
    "classify_path",
    "classify_user_agent",
    "is_exempt_path",
    "lock_is_enforcing",
    "origin_locked_response",
]
