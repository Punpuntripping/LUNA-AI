"""Origin lock middleware — step 3.4 of ``.claude/plans/cloudflare_navigation_hardening.md``.

The five load-bearing behaviours, in the order the brief lists them:

1. **enabled + valid**  — a request carrying Cloudflare's ``X-Edge-Secret`` passes.
2. **enabled + missing** — no header ⇒ 403, Arabic, standard error envelope.
3. **enabled + wrong**  — mismatched value ⇒ 403.
4. **disabled**         — ``EDGE_SECRET`` unset ⇒ everything passes untouched.
                          This is the default, and it is what keeps the middleware
                          inert while every DNS record is still grey-clouded.
5. **health exemption** — ``/api/v1/health`` answers 200 with no header, because
                          Railway's healthcheck probes the container directly and
                          can never carry one. Miss this and every deploy fails.

Plus the two ordering/robustness invariants that are easy to regress: CORS must
stay OUTSIDE the lock (a 403'd preflight breaks the browser app — the OTEL
preflight-500 outage is the precedent), and a junk header must produce a clean
403 rather than a ``compare_digest`` TypeError → 500.

No Redis, no DB, no network: the middleware is exercised through throwaway
FastAPI apps, and the registration test only inspects ``app.user_middleware``.
"""
from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from backend.app.middleware import origin_lock
from backend.app.middleware.origin_lock import (
    EDGE_SECRET_ENV,
    EDGE_SECRET_HEADER,
    EXEMPT_PATHS,
    HOST_CLASSES,
    MODE_ENFORCE,
    MODE_OBSERVE,
    MODE_OFF,
    ORIGIN_LOCK_MESSAGE,
    ORIGIN_LOCK_MODE_ENV,
    OUTCOMES,
    PATH_WINGS,
    SIGNAL_EVENT,
    UA_CLASSES,
    OriginLockMiddleware,
    classify_host,
    classify_path,
    classify_user_agent,
    is_exempt_path,
    lock_is_enforcing,
)
from shared.observability import _PII_EXTRA_PATTERNS, _NoopLogfire

SECRET = "cf-transform-rule-value-32bytes-xyz"
WRONG = "cf-transform-rule-value-32bytes-XYZ"  # one case flip


@pytest.fixture(autouse=True)
def _mode_enforce(monkeypatch):
    """Never inherit a developer's shell. Every test above the mode matrix was
    written against an enforcing lock, and only the word itself enforces."""
    monkeypatch.setenv(ORIGIN_LOCK_MODE_ENV, MODE_ENFORCE)
    yield


# ---------------------------------------------------------------------------
# Throwaway app
# ---------------------------------------------------------------------------


def _build_app(secret: str | None = None, *, from_env: bool = False,
               with_cors: bool = False, mode: str | None = None) -> FastAPI:
    """A minimal app carrying the same routes the real one exposes at the two
    paths that matter: the exempt healthcheck and an ordinary API route.

    ``from_env=True`` omits the ``secret`` kwarg entirely so the middleware reads
    ``EDGE_SECRET`` itself — that is the production wiring (``main.py`` calls
    ``add_middleware(OriginLockMiddleware)`` with no arguments). ``mode=None``
    likewise leaves the mode to the environment.
    """
    app = FastAPI()

    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/v1/cases")
    async def cases():
        return {"items": []}

    @app.post("/internal/summarize-workspace-item")
    async def internal_webhook():
        return {"status": "ok"}

    if from_env:
        app.add_middleware(OriginLockMiddleware, mode=mode)
    else:
        app.add_middleware(OriginLockMiddleware, secret=secret, mode=mode)

    if with_cors:
        # Added LAST => OUTERMOST, exactly as main.py orders it.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["https://rayhanai.com"],
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )
    return app


# ---------------------------------------------------------------------------
# 1. enabled + valid
# ---------------------------------------------------------------------------


def test_enabled_valid_secret_passes():
    client = TestClient(_build_app(SECRET))
    r = client.get("/api/v1/cases", headers={EDGE_SECRET_HEADER: SECRET})
    assert r.status_code == 200
    assert r.json() == {"items": []}


def test_enabled_header_name_is_case_insensitive():
    """Nothing guarantees the on-the-wire casing Cloudflare emits."""
    client = TestClient(_build_app(SECRET))
    r = client.get("/api/v1/cases", headers={"X-Edge-Secret": SECRET})
    assert r.status_code == 200


def test_enabled_duplicate_header_any_match_passes():
    """Cloudflare appends to client-supplied headers on some paths (the reason
    leftmost X-Forwarded-For is untrusted — plan step 3.5). If a forged copy
    survives alongside the edge's real one, taking only the first occurrence
    would 403 legitimate proxied traffic."""
    client = TestClient(_build_app(SECRET))
    r = client.get(
        "/api/v1/cases",
        headers=[(EDGE_SECRET_HEADER, "forged-by-client"), (EDGE_SECRET_HEADER, SECRET)],
    )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# 2. enabled + missing
# ---------------------------------------------------------------------------


def test_enabled_missing_secret_rejected():
    client = TestClient(_build_app(SECRET))
    r = client.get("/api/v1/cases")

    assert r.status_code == 403
    body = r.json()
    # Standard Luna envelope — same shape luna_exception_handler emits.
    assert body["error"]["code"] == "FORBIDDEN"
    assert body["error"]["status"] == 403
    assert body["error"]["message"] == ORIGIN_LOCK_MESSAGE
    assert body["detail"] == ORIGIN_LOCK_MESSAGE
    # Arabic (Absolute Rule #5).
    assert any("؀" <= ch <= "ۿ" for ch in body["detail"])
    # Must never be pinned into the shared edge cache (cache rule 3.10 makes
    # /api/v1/public/library/* cache-eligible).
    assert r.headers["cache-control"] == "private, no-store"


def test_enabled_empty_header_rejected():
    client = TestClient(_build_app(SECRET))
    r = client.get("/api/v1/cases", headers={EDGE_SECRET_HEADER: ""})
    assert r.status_code == 403


def test_enabled_internal_routes_are_not_exempt():
    """/internal/* is service-authed PUBLIC, not a network boundary — it is
    reached over the public internet by Supabase triggers and by marketing, so
    once the zone is proxied it transits Cloudflare like everything else and
    carries the header. It gets no exemption here."""
    client = TestClient(_build_app(SECRET))
    assert client.post("/internal/summarize-workspace-item").status_code == 403
    assert client.post(
        "/internal/summarize-workspace-item",
        headers={EDGE_SECRET_HEADER: SECRET},
    ).status_code == 200


# ---------------------------------------------------------------------------
# 3. enabled + wrong
# ---------------------------------------------------------------------------


def test_enabled_wrong_secret_rejected():
    client = TestClient(_build_app(SECRET))
    r = client.get("/api/v1/cases", headers={EDGE_SECRET_HEADER: WRONG})
    assert r.status_code == 403
    assert r.json()["detail"] == ORIGIN_LOCK_MESSAGE


def test_enabled_prefix_of_secret_rejected():
    client = TestClient(_build_app(SECRET))
    r = client.get("/api/v1/cases", headers={EDGE_SECRET_HEADER: SECRET[:-1]})
    assert r.status_code == 403


def test_enabled_non_ascii_header_is_403_not_500():
    """``hmac.compare_digest`` raises TypeError on non-ASCII ``str``; the value
    comes straight off the wire, so comparing as bytes is what keeps a junk
    header from turning a clean 403 into an unhandled 500.

    Sent as raw BYTES, which is the only way to reproduce the real wire path:
    httpx refuses to encode a non-ASCII ``str`` header itself, while Starlette
    decodes whatever bytes arrive as latin-1 — yielding exactly the non-ASCII
    ``str`` that would blow up a naive comparison.
    """
    client = TestClient(_build_app(SECRET))
    r = client.get("/api/v1/cases", headers={EDGE_SECRET_HEADER: "سرّ".encode("utf-8")})
    assert r.status_code == 403
    assert r.json()["detail"] == ORIGIN_LOCK_MESSAGE


# ---------------------------------------------------------------------------
# 4. disabled (the default)
# ---------------------------------------------------------------------------


def test_disabled_when_env_unset_passes_everything(monkeypatch):
    """THE safe default. Every DNS record is still grey-clouded, so no live
    request carries X-Edge-Secret; arming the lock before the orange cloud flips
    would 403 100% of production traffic."""
    monkeypatch.delenv(EDGE_SECRET_ENV, raising=False)
    client = TestClient(_build_app(from_env=True))
    assert client.get("/api/v1/cases").status_code == 200
    assert client.get("/api/v1/health").status_code == 200
    assert client.post("/internal/summarize-workspace-item").status_code == 200


def test_disabled_when_env_blank(monkeypatch):
    """A Railway variable created but left empty must not half-arm the lock."""
    monkeypatch.setenv(EDGE_SECRET_ENV, "   ")
    app = _build_app(from_env=True)
    client = TestClient(app)
    assert client.get("/api/v1/cases").status_code == 200


def test_enabled_from_env_var(monkeypatch):
    """The production wiring: no kwarg, value read from EDGE_SECRET."""
    monkeypatch.setenv(EDGE_SECRET_ENV, SECRET)
    client = TestClient(_build_app(from_env=True))
    assert client.get("/api/v1/cases").status_code == 403
    assert client.get(
        "/api/v1/cases", headers={EDGE_SECRET_HEADER: SECRET}
    ).status_code == 200


def test_enabled_property_reflects_config(monkeypatch):
    monkeypatch.delenv(EDGE_SECRET_ENV, raising=False)
    assert OriginLockMiddleware(app=None).enabled is False
    assert OriginLockMiddleware(app=None, secret=SECRET).enabled is True
    assert OriginLockMiddleware(app=None, secret="  ").enabled is False


# ---------------------------------------------------------------------------
# 5. health exemption — the line that gates every deploy
# ---------------------------------------------------------------------------


def test_health_exempt_without_secret():
    """railway.json sets healthcheckPath=/api/v1/health and Railway probes the
    container directly, never through Cloudflare. Without this exemption every
    deploy fails its healthcheck and rolls back."""
    client = TestClient(_build_app(SECRET))
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_health_exempt_with_wrong_secret_too():
    """The exemption is unconditional — it must not depend on the header at all."""
    client = TestClient(_build_app(SECRET))
    assert client.get(
        "/api/v1/health", headers={EDGE_SECRET_HEADER: WRONG}
    ).status_code == 200


def test_health_exemption_tolerates_trailing_slash():
    """A healthcheckPath typo'd with a stray slash must still reach the router
    (which 307s it) rather than being 403'd first."""
    assert is_exempt_path("/api/v1/health/") is True
    assert is_exempt_path("/api/v1/health") is True


def test_exempt_set_is_exactly_health():
    """Every entry in this set is a hole straight to the origin. Adding one is a
    deliberate act; this test makes it show up in review."""
    assert EXEMPT_PATHS == frozenset({"/api/v1/health"})


def test_health_prefix_is_not_exempt():
    """Prefix matching would exempt anything a caller can hang off the path."""
    assert is_exempt_path("/api/v1/health/../cases") is False
    assert is_exempt_path("/api/v1/healthz") is False
    client = TestClient(_build_app(SECRET))
    assert client.get("/api/v1/cases").status_code == 403


# ---------------------------------------------------------------------------
# Ordering — CORS must stay outermost
# ---------------------------------------------------------------------------


def test_cors_preflight_is_not_blocked_by_origin_lock():
    """A preflight carries no custom headers, so a lock placed OUTSIDE CORS would
    403 every one of them and break the browser app. CORS outermost answers the
    OPTIONS itself and short-circuits before the lock ever runs — same reason it
    must wrap the OTEL instrumentation (the preflight-500 login outage)."""
    client = TestClient(_build_app(SECRET, with_cors=True))
    r = client.options(
        "/api/v1/cases",
        headers={
            "Origin": "https://rayhanai.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "https://rayhanai.com"


def test_cors_headers_present_on_origin_lock_403():
    """CORS wraps the lock, so its send-hook still decorates the 403 — the
    browser can read the refusal instead of reporting an opaque CORS error."""
    client = TestClient(_build_app(SECRET, with_cors=True))
    r = client.get("/api/v1/cases", headers={"Origin": "https://rayhanai.com"})
    assert r.status_code == 403
    assert r.headers["access-control-allow-origin"] == "https://rayhanai.com"


# ---------------------------------------------------------------------------
# Registration in the real app
# ---------------------------------------------------------------------------


def test_registered_in_main_just_inside_cors():
    """Stack assertion against the real factory: CORS outermost, origin lock
    immediately inside it, everything else within."""
    from backend.app.main import create_app

    app = create_app()
    # user_middleware is stored OUTERMOST-first.
    classes = [m.cls for m in app.user_middleware]
    assert classes[0] is CORSMiddleware, f"CORS must be outermost, got {classes}"
    assert classes[1] is OriginLockMiddleware, (
        f"origin lock must sit just inside CORS, got {classes}"
    )


def test_real_app_health_reachable_with_lock_armed(monkeypatch):
    """End-to-end through the ACTUAL middleware stack: with EDGE_SECRET set and
    no header on the request, /api/v1/health must still answer 200 while an
    ordinary route is refused. This is the deploy gate, asserted on the real app
    rather than on a stand-in.

    TestClient is used WITHOUT its context manager on purpose — that skips
    lifespan, so no Redis/Supabase/scheduler startup is required. The rate
    limiter reads app.state.redis via getattr(..., None) and fails open, so the
    stack still runs end to end.
    """
    from backend.app.main import create_app

    monkeypatch.setenv(EDGE_SECRET_ENV, SECRET)
    client = TestClient(create_app())

    assert client.get("/api/v1/health").status_code == 200
    # ...and the lock really is armed on everything else.
    assert client.get("/api/v1/cases").status_code == 403


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))


# ---------------------------------------------------------------------------
# OBSERVE MODE — measure the header before trusting it (§3.4 step 5)
#
# Arming the lock is the one cutover step whose failure mode is total, and step 5
# says to capture the header's ON-THE-WIRE SHAPE before step 6 sets EDGE_SECRET.
# These pin the two properties that makes that safe: it reports the VALUE COUNT
# (not mere presence — `getlist` only separates distinct header LINES, so a
# comma-FOLDED edge would 403 legitimate traffic and presence cannot see it), and
# it never, ever enforces.
# ---------------------------------------------------------------------------


def test_observe_runs_only_while_disabled_and_never_enforces(caplog):
    """The whole point: a disabled lock now SAYS what it saw, and still forwards
    everything. If this ever starts rejecting, observe mode has become the outage
    it was built to prevent."""
    client = TestClient(_build_app(None))
    with caplog.at_level("INFO"):
        r = client.get("/api/v1/cases")
    assert r.status_code == 200
    assert any("OBSERVE" in rec.message for rec in caplog.records)


def test_observe_reports_the_value_COUNT_not_just_presence(caplog):
    """Two distinct header LINES must be reported as 2 values — that is the shape
    `_header_matches` survives (any-match wins). Reporting only "present" would
    make this world indistinguishable from the folded one below."""
    client = TestClient(_build_app(None))
    with caplog.at_level("INFO"):
        client.get(
            "/api/v1/cases",
            headers=[(EDGE_SECRET_HEADER, "forged-by-client"), (EDGE_SECRET_HEADER, SECRET)],
        )
    line = next(rec.getMessage() for rec in caplog.records if "OBSERVE" in rec.message)
    assert "2 value(s)" in line
    assert "folded=False" in line


def test_observe_flags_a_COMMA_FOLDED_header(caplog):
    """⚠ THE SELF-INFLICTED DoS. One comma-joined line ("forged, real") means no
    single value equals the secret, so arming would 403 our own proxied traffic —
    triggerable by any third party who pre-sends the header. `folded=True` is the
    signal that must block step 6."""
    client = TestClient(_build_app(None))
    with caplog.at_level("INFO"):
        client.get(
            "/api/v1/cases",
            headers={EDGE_SECRET_HEADER: f"forged-by-client, {SECRET}"},
        )
    line = next(rec.getMessage() for rec in caplog.records if "OBSERVE" in rec.message)
    assert "folded=True" in line


def test_observe_never_logs_the_secret_value(caplog):
    """Lengths and a comma flag only. A credential in a log aggregator is a
    credential leaked, and observe mode runs on every request of the disabled
    path — the highest-volume place it could possibly leak from."""
    client = TestClient(_build_app(None))
    with caplog.at_level("INFO"):
        client.get("/api/v1/cases", headers={EDGE_SECRET_HEADER: SECRET})
    for rec in caplog.records:
        assert SECRET not in (rec.getMessage())


def test_observe_is_silent_once_ARMED(caplog):
    """An armed lock has nothing to observe — it enforces, and `_log_rejection`
    owns the logging. Leaving observe on would double every rejection line."""
    client = TestClient(_build_app(SECRET))
    with caplog.at_level("INFO"):
        client.get("/api/v1/cases", headers={EDGE_SECRET_HEADER: SECRET})
    assert not any("OBSERVE" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# ORIGIN_LOCK_MODE and the Logfire signal
# (.claude/plans/library_guardrails_hardening.md Phase 1a)
# ---------------------------------------------------------------------------

SIGNAL_ATTRIBUTES = {
    "host_class",
    "outcome",
    "value_count",
    "path_wing",
    "ua_class",
    "enforced",
    "count",
}
FLUSH = origin_lock._SIGNAL_FLUSH_SECONDS

# Every way of saying "armed and enforcing" / the modes that exist at all.
ENFORCING_MODES = (None, "enforce")
ARMED_MODES = (None, "enforce", "observe", "off", "typo")


class _FakeLogfire:
    """Stands in for the module-level ``_logfire`` and records every call."""

    def __init__(self, *, raises: bool = False) -> None:
        self.raises = raises
        self.calls: list[tuple[str, str, dict]] = []

    def _record(self, level: str, event: str, attrs: dict) -> None:
        if self.raises:
            raise RuntimeError("telemetry is down")
        self.calls.append((level, event, attrs))

    def info(self, event: str, **attrs) -> None:
        self._record("info", event, attrs)

    def warning(self, event: str, **attrs) -> None:
        self._record("warning", event, attrs)

    def error(self, event: str, **attrs) -> None:
        self._record("error", event, attrs)

    @property
    def signals(self) -> list[dict]:
        return [attrs for _, event, attrs in self.calls if event == SIGNAL_EVENT]

    @property
    def errors(self) -> list[dict]:
        return [attrs for level, _, attrs in self.calls if level == "error"]


@pytest.fixture
def fire(monkeypatch) -> _FakeLogfire:
    fake = _FakeLogfire()
    monkeypatch.setattr(origin_lock, "_logfire", fake)
    return fake


@pytest.fixture
def clock(monkeypatch) -> SimpleNamespace:
    """Replaces the ``time`` name in origin_lock only, so asyncio and anyio keep
    the real clock while the throttle runs on this one."""
    fake = SimpleNamespace(now=1000.0)
    fake.monotonic = lambda: fake.now
    monkeypatch.setattr(origin_lock, "time", fake)
    return fake


async def _ok_asgi(scope, receive, send) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _locked_client(mode: str | None = MODE_OBSERVE) -> tuple[OriginLockMiddleware, TestClient]:
    """The middleware itself, for the tests that need to look inside it."""
    middleware = OriginLockMiddleware(_ok_asgi, secret=SECRET, mode=mode)
    return middleware, TestClient(middleware)


# -- off ----------------------------------------------------------------------


def test_off_passes_everything_without_comparing(fire, monkeypatch):
    def compared(*_args, **_kwargs):
        raise AssertionError("mode=off must not compare")

    monkeypatch.setattr(OriginLockMiddleware, "_header_matches", compared)
    client = TestClient(_build_app(SECRET, mode="off"))

    assert client.get("/api/v1/cases").status_code == 200
    assert client.get("/api/v1/cases", headers={EDGE_SECRET_HEADER: WRONG}).status_code == 200
    assert fire.calls == []


# -- observe ------------------------------------------------------------------


def test_observe_match_passes_and_reports(fire):
    client = TestClient(_build_app(SECRET, mode="observe"))
    r = client.get("/api/v1/cases", headers={EDGE_SECRET_HEADER: SECRET})

    assert r.status_code == 200
    assert len(fire.signals) == 1
    signal = fire.signals[0]
    assert set(signal) == SIGNAL_ATTRIBUTES
    assert signal["outcome"] == "match"
    assert signal["value_count"] == 1
    assert signal["enforced"] is False
    assert signal["count"] == 1


def test_observe_missing_passes_and_reports(fire):
    """THE measurement the old code could not make: this request WOULD have been
    refused, it says so, and it still gets through."""
    client = TestClient(_build_app(SECRET, mode="observe"))
    r = client.get("/api/v1/cases")

    assert r.status_code == 200
    assert fire.signals[0]["outcome"] == "missing"
    assert fire.signals[0]["value_count"] == 0
    assert fire.signals[0]["enforced"] is False


def test_observe_mismatch_passes_and_reports(fire):
    client = TestClient(_build_app(SECRET, mode="observe"))
    r = client.get("/api/v1/cases", headers={EDGE_SECRET_HEADER: WRONG})

    assert r.status_code == 200
    assert fire.signals[0]["outcome"] == "mismatch"
    assert fire.signals[0]["value_count"] == 1


def test_observe_comma_folded_header_reports_folded(fire):
    """The gate on enforce (trap 6): one comma-joined line means no single value
    equals the secret, so enforcing would refuse our own traffic."""
    client = TestClient(_build_app(SECRET, mode="observe"))
    r = client.get("/api/v1/cases", headers={EDGE_SECRET_HEADER: f"forged, {SECRET}"})

    assert r.status_code == 200
    assert fire.signals[0]["outcome"] == "folded"
    assert fire.signals[0]["value_count"] == 1


def test_observe_two_lines_with_the_real_one_is_a_match(fire):
    """Two lines is the shape the any-match compare survives, and a comma on the
    forged line must not turn a real match into ``folded``."""
    client = TestClient(_build_app(SECRET, mode="observe"))
    client.get(
        "/api/v1/cases",
        headers=[(EDGE_SECRET_HEADER, "forged, also-forged"), (EDGE_SECRET_HEADER, SECRET)],
    )
    assert fire.signals[0]["outcome"] == "match"
    assert fire.signals[0]["value_count"] == 2


def test_observe_empty_header_line_is_a_mismatch_not_missing(fire):
    """``missing`` means no line at all, so that it reads true next to
    ``value_count`` and the cutover gate on it stays meaningful."""
    client = TestClient(_build_app(SECRET, mode="observe"))
    client.get("/api/v1/cases", headers={EDGE_SECRET_HEADER: ""})

    assert fire.signals[0]["outcome"] == "mismatch"
    assert fire.signals[0]["value_count"] == 1


def test_observe_is_not_enabled_but_is_reported_as_observe():
    middleware = OriginLockMiddleware(app=None, secret=SECRET, mode="observe")
    assert middleware.enabled is False
    assert middleware.mode == MODE_OBSERVE


# -- enforce ------------------------------------------------------------------


@pytest.mark.parametrize("mode", ENFORCING_MODES)
def test_enforce_refuses_and_reports(fire, mode):
    client = TestClient(_build_app(SECRET, mode=mode))
    r = client.get("/api/v1/cases", headers={EDGE_SECRET_HEADER: WRONG})

    assert r.status_code == 403
    assert fire.signals[0]["outcome"] == "mismatch"
    assert fire.signals[0]["enforced"] is True


@pytest.mark.parametrize("mode", ENFORCING_MODES)
def test_enforce_refusal_body_and_cache_header_are_unchanged(fire, mode):
    client = TestClient(_build_app(SECRET, mode=mode))
    r = client.get("/api/v1/cases")

    assert r.status_code == 403
    assert r.json() == {
        "error": {"code": "FORBIDDEN", "message": ORIGIN_LOCK_MESSAGE, "status": 403},
        "detail": ORIGIN_LOCK_MESSAGE,
    }
    assert r.headers["cache-control"] == "private, no-store"
    assert fire.signals[0]["outcome"] == "missing"


@pytest.mark.parametrize("mode", ENFORCING_MODES)
def test_enforce_folded_header_is_refused_as_today(fire, mode):
    client = TestClient(_build_app(SECRET, mode=mode))
    r = client.get("/api/v1/cases", headers={EDGE_SECRET_HEADER: f"forged, {SECRET}"})

    assert r.status_code == 403
    assert fire.signals[0]["outcome"] == "folded"


@pytest.mark.parametrize("mode", ENFORCING_MODES)
def test_enforce_match_passes_and_reports(fire, mode):
    client = TestClient(_build_app(SECRET, mode=mode))
    r = client.get("/api/v1/cases", headers={EDGE_SECRET_HEADER: SECRET})

    assert r.status_code == 200
    assert fire.signals[0]["outcome"] == "match"
    assert fire.signals[0]["enforced"] is True


def test_mode_is_read_from_the_environment(fire, monkeypatch):
    """The production wiring: neither kwarg, both values from Railway."""
    monkeypatch.setenv(EDGE_SECRET_ENV, SECRET)
    monkeypatch.setenv(ORIGIN_LOCK_MODE_ENV, "observe")

    client = TestClient(_build_app(from_env=True))

    assert client.get("/api/v1/cases").status_code == 200
    assert fire.signals[0]["enforced"] is False


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_mode_observes_and_says_so(fire, blank):
    """Clearing the variable is a natural way to turn the lock off, so the next
    redeploy must not start refusing real traffic."""
    middleware = OriginLockMiddleware(app=None, secret=SECRET, mode=blank)
    assert middleware.mode == MODE_OBSERVE
    assert middleware.enabled is False
    assert len(fire.errors) == 1


def test_deleted_mode_observes_and_never_refuses(fire, monkeypatch):
    monkeypatch.setenv(EDGE_SECRET_ENV, SECRET)
    monkeypatch.delenv(ORIGIN_LOCK_MODE_ENV, raising=False)

    client = TestClient(_build_app(from_env=True))

    assert client.get("/api/v1/cases").status_code == 200
    assert fire.signals[0]["enforced"] is False
    assert len(fire.errors) == 1


@pytest.mark.parametrize(
    "secret,mode,expected",
    [
        (SECRET, "enforce", True),
        (SECRET, " enforce ", True),
        (SECRET, "observe", False),
        (SECRET, "off", False),
        (SECRET, "ENFORCE", False),
        (SECRET, "", False),
        (SECRET, None, False),
        ("", "enforce", False),
        ("   ", "enforce", False),
        (None, "enforce", False),
    ],
)
def test_lock_is_enforcing_agrees_with_the_middleware(monkeypatch, secret, mode, expected):
    """``trust_cf_headers`` rests on this, so it must never say yes while the
    middleware would let a secret-less request through."""
    for name, value in ((EDGE_SECRET_ENV, secret), (ORIGIN_LOCK_MODE_ENV, mode)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    assert lock_is_enforcing() is expected
    assert OriginLockMiddleware(app=None).enabled is expected


def test_mode_is_stripped_like_the_secret_is(fire):
    assert OriginLockMiddleware(app=None, secret=SECRET, mode=" observe ").mode == MODE_OBSERVE
    assert fire.errors == []


# -- unknown mode -------------------------------------------------------------


@pytest.mark.parametrize(
    "typo", ["enforcee", "enforc", "ENFORCE", "Enforce", "OFF", "true", "1", "on", "block"]
)
def test_unknown_mode_observes_and_never_enforces(fire, caplog, typo):
    """A typo must never start enforcing. Case is deliberately not folded, which
    matches the frontend lock: only the exact word blocks."""
    client = TestClient(_build_app(SECRET, mode=typo))
    with caplog.at_level("ERROR"):
        r = client.get("/api/v1/cases")

    assert r.status_code == 200
    assert fire.signals[0]["outcome"] == "missing"
    assert fire.signals[0]["enforced"] is False

    errors = [rec for rec in caplog.records if rec.levelname == "ERROR"]
    assert len(errors) == 1
    assert typo in errors[0].getMessage()
    assert fire.errors == [{"configured_value": typo}]


def test_secret_pasted_into_the_mode_variable_is_not_echoed(fire, caplog):
    """The likeliest long value in that variable is the secret itself, swapped
    with EDGE_SECRET in the dashboard. Reporting the bad value must not leak it."""
    with caplog.at_level("INFO"):
        middleware = OriginLockMiddleware(app=None, secret=SECRET, mode=SECRET)

    assert middleware.mode == MODE_OBSERVE
    assert fire.errors == [{"configured_value": "<redacted>"}]
    assert SECRET not in caplog.text
    assert SECRET not in repr(fire.calls)


def test_short_secret_pasted_into_the_mode_variable_is_not_echoed(fire, caplog):
    """The length cap alone would pass a secret this short straight through."""
    with caplog.at_level("INFO"):
        OriginLockMiddleware(app=None, secret="shortvalue", mode="shortvalue")

    assert fire.errors == [{"configured_value": "<redacted>"}]
    assert "shortvalue" not in caplog.text


# -- health -------------------------------------------------------------------


@pytest.mark.parametrize("mode", ARMED_MODES)
@pytest.mark.parametrize("headers", [{}, {EDGE_SECRET_HEADER: WRONG}])
def test_health_is_exempt_and_silent_in_every_mode(fire, mode, headers):
    client = TestClient(_build_app(SECRET, mode=mode))

    r = client.get("/api/v1/health", headers=headers)

    assert r.status_code == 200
    assert fire.signals == []


@pytest.mark.parametrize("mode", ARMED_MODES)
def test_health_with_a_trailing_slash_is_silent_in_every_mode(fire, mode):
    client = TestClient(_build_app(SECRET, mode=mode))
    client.get("/api/v1/health/", follow_redirects=False)
    assert fire.signals == []


def test_health_does_not_flush_folded_requests(fire, clock):
    """Exempt means no signal at all, including someone else's deferred one."""
    _, client = _locked_client()
    client.get("/api/v1/cases")
    client.get("/api/v1/cases")
    clock.now += FLUSH
    client.get("/api/v1/health")
    assert [s["count"] for s in fire.signals] == [1]


# -- secret unset -------------------------------------------------------------


@pytest.mark.parametrize("mode", [None, "", "off", "observe", "typo"])
def test_secret_unset_is_todays_behaviour_whatever_the_mode(fire, caplog, monkeypatch, mode):
    monkeypatch.delenv(EDGE_SECRET_ENV, raising=False)
    monkeypatch.delenv(ORIGIN_LOCK_MODE_ENV, raising=False)
    client = TestClient(_build_app(from_env=True, mode=mode))

    with caplog.at_level("INFO"):
        assert client.get("/api/v1/cases").status_code == 200
        assert client.get(
            "/api/v1/cases", headers={EDGE_SECRET_HEADER: WRONG}
        ).status_code == 200

    # Pass-through, the existing stdlib shape line, and nothing in Logfire.
    assert any("OBSERVE" in rec.message for rec in caplog.records)
    assert fire.calls == []
    assert not any(rec.levelname == "ERROR" for rec in caplog.records)


def test_enforce_with_no_secret_passes_but_reports_a_disarmed_lock(fire, caplog, monkeypatch):
    """The operator believes the lock is on, and a quiet Logfire looks like success."""
    monkeypatch.delenv(EDGE_SECRET_ENV, raising=False)

    with caplog.at_level("ERROR"):
        client = TestClient(_build_app(from_env=True, mode=MODE_ENFORCE))

    assert client.get("/api/v1/cases").status_code == 200
    assert sum("the lock is OFF" in rec.message for rec in caplog.records) == 1
    assert len(fire.errors) == 1


def test_a_raising_logfire_cannot_stop_the_boot_on_a_disarmed_lock(monkeypatch):
    monkeypatch.delenv(EDGE_SECRET_ENV, raising=False)
    monkeypatch.setattr(origin_lock, "_logfire", _FakeLogfire(raises=True))

    assert OriginLockMiddleware(app=None, mode=MODE_ENFORCE).enabled is False


@pytest.mark.parametrize("mode", [None, "off", "observe", "typo"])
def test_secret_unset_health_passes_whatever_the_mode(fire, monkeypatch, mode):
    monkeypatch.delenv(EDGE_SECRET_ENV, raising=False)
    monkeypatch.delenv(ORIGIN_LOCK_MODE_ENV, raising=False)
    client = TestClient(_build_app(from_env=True, mode=mode))

    assert client.get("/api/v1/health").status_code == 200
    assert fire.calls == []


@pytest.mark.parametrize("mode", [None, "off", "observe", "enforce", "typo"])
def test_secret_unset_is_never_enabled(monkeypatch, mode):
    monkeypatch.delenv(EDGE_SECRET_ENV, raising=False)
    middleware = OriginLockMiddleware(app=None, mode=mode)
    assert middleware.enabled is False
    assert middleware.mode == MODE_OFF


# -- throttling ---------------------------------------------------------------


def test_first_sighting_reports_at_once_and_the_rest_fold_into_count(fire, clock):
    _, client = _locked_client()

    for _ in range(5):
        client.get("/api/v1/cases")
    assert [s["count"] for s in fire.signals] == [1]

    clock.now += FLUSH
    client.get("/api/v1/cases")

    # The first line, then the four folded requests plus the one that flushed.
    assert [s["count"] for s in fire.signals] == [1, 5]


def test_count_sums_to_the_real_request_total(fire, clock):
    """What makes ``sum(count)`` a true volume for a later alert."""
    _, client = _locked_client()
    total = 0
    for burst in (7, 1, 12):
        for _ in range(burst):
            client.get("/api/v1/cases")
            total += 1
        clock.now += FLUSH
    client.get("/api/v1/cases")
    total += 1

    assert sum(s["count"] for s in fire.signals) == total


def test_a_quiet_window_emits_nothing(fire, clock):
    _, client = _locked_client()
    client.get("/api/v1/cases")
    clock.now += FLUSH
    client.get("/api/v1/cases", headers={EDGE_SECRET_HEADER: SECRET})
    assert len([s for s in fire.signals if s["outcome"] == "missing"]) == 1


def test_a_burst_that_stops_is_flushed_by_any_other_request(fire, clock):
    """The prober sends 100 and leaves. Nothing else ever lands on that key, so a
    per-key flush would strand 99 of them and the alert would read 1."""
    _, client = _locked_client()

    for _ in range(100):
        client.get("/api/v1/cases")
    clock.now += FLUSH
    client.get("/api/v1/public/library/regulations", headers={EDGE_SECRET_HEADER: SECRET})

    probes = [s for s in fire.signals if s["path_wing"] == "app"]
    assert sum(s["count"] for s in probes) == 100


def test_flush_is_capped_per_request_and_drains_without_losing_count(fire, clock):
    """A prober picks its own Host and path, so it can light up every key. One
    request must not pay for all of them: a line costs real time on the event
    loop, and that stall lands on every live SSE stream."""
    middleware, client = _locked_client()
    cap = origin_lock._MAX_FLUSH_LINES_PER_REQUEST
    keys = [
        (host, path)
        for host in ("api.rayhanai.com", "x.up.railway.app", "x.railway.internal", "1.2.3.4")
        for path in (
            "/api/v1/cases", "/internal/x", "/.env", "/api/v1/auth/login", "/api/v1/payments/x",
        )
    ]
    assert len(keys) > cap * 2

    total = 0
    for host, path in keys:
        for _ in range(3):
            client.get(path, headers={"host": host})
            total += 1
    assert len(fire.signals) == len(keys)  # one first-sighting line each

    clock.now += FLUSH
    before = len(fire.signals)
    client.get("/api/v1/cases", headers={"host": "api.rayhanai.com"})
    total += 1
    assert len(fire.signals) - before == cap

    while middleware._flush_queue:
        before = len(fire.signals)
        client.get("/api/v1/health")  # exempt, so this alone must not drain it
        assert len(fire.signals) == before
        client.get("/api/v1/cases", headers={"host": "api.rayhanai.com"})
        total += 1
        assert len(fire.signals) - before <= cap

    clock.now += FLUSH
    client.get("/api/v1/cases", headers={"host": "api.rayhanai.com"})
    total += 1
    assert sum(s["count"] for s in fire.signals) == total


def test_a_draining_key_gets_one_line_per_interval(fire, clock):
    """The queue is a snapshot, so a key that stays busy during a long drain is
    not re-emitted ahead of the keys still waiting."""
    middleware, client = _locked_client()
    for wing_path in ("/api/v1/cases", "/internal/x", "/.env"):
        for host in ("api.rayhanai.com", "x.up.railway.app", "1.2.3.4", "x.railway.internal"):
            client.get(wing_path, headers={"host": host})
            client.get(wing_path, headers={"host": host})

    clock.now += FLUSH
    before = len(fire.signals)
    while True:
        client.get("/api/v1/cases", headers={"host": "api.rayhanai.com"})
        if not middleware._flush_queue:
            break

    flushed = fire.signals[before:]
    keys = [(s["host_class"], s["outcome"], s["path_wing"]) for s in flushed]
    assert len(keys) == len(set(keys))


def test_outcome_host_and_wing_each_get_their_own_line(fire):
    _, client = _locked_client()
    client.get("/api/v1/cases")
    client.get("/api/v1/cases", headers={EDGE_SECRET_HEADER: WRONG})
    client.get("/api/v1/cases", headers={"host": "api.rayhanai.com"})
    client.get("/api/v1/public/library/regulations")

    assert len(fire.signals) == 4


def test_folded_line_keeps_the_most_header_lines_seen(fire, clock):
    """One forged-and-real pair in a window of ordinary requests must show."""
    _, client = _locked_client()
    client.get("/api/v1/cases", headers={EDGE_SECRET_HEADER: SECRET})
    client.get(
        "/api/v1/cases",
        headers=[(EDGE_SECRET_HEADER, "forged"), (EDGE_SECRET_HEADER, SECRET)],
    )
    clock.now += FLUSH
    client.get("/api/v1/cases", headers={EDGE_SECRET_HEADER: SECRET})

    assert fire.signals[-1]["value_count"] == 2
    assert fire.signals[-1]["count"] == 2


def test_table_is_bounded_whatever_is_sent(fire, clock):
    """Host, path and User-Agent are the attacker's. A thousand distinct values
    of each must neither grow the table nor reach an attribute."""
    middleware, client = _locked_client()
    for n in range(1000):
        client.get(
            f"/probe-{n}" if n % 2 else f"/api/v1/public/library/{n}",
            headers={"host": f"attacker-{n}.example", "user-agent": f"agent-{n}"},
        )
    clock.now += FLUSH
    client.get("/api/v1/cases")

    limit = len(HOST_CLASSES) * len(OUTCOMES) * len(PATH_WINGS)
    assert len(middleware._pending) <= limit
    assert len(middleware._pending) <= 3  # only two wings and one host class in play
    for signal in fire.signals:
        assert signal["host_class"] in HOST_CLASSES
        assert signal["outcome"] in OUTCOMES
        assert signal["path_wing"] in PATH_WINGS
        assert signal["ua_class"] in UA_CLASSES


# -- what is emitted ----------------------------------------------------------


@pytest.mark.parametrize("mode", ["observe", "enforce"])
def test_secret_never_appears_in_any_emitted_attribute(fire, clock, mode):
    _, client = _locked_client(mode)
    for headers in (
        {EDGE_SECRET_HEADER: SECRET},
        {EDGE_SECRET_HEADER: WRONG},
        {EDGE_SECRET_HEADER: f"forged, {SECRET}"},
        [(EDGE_SECRET_HEADER, "forged"), (EDGE_SECRET_HEADER, SECRET)],
    ):
        client.get("/api/v1/cases", headers=headers)
        client.get("/api/v1/cases", headers=headers)
    clock.now += FLUSH
    client.get("/api/v1/cases")

    assert fire.signals
    emitted = repr(fire.calls)
    assert SECRET not in emitted
    assert WRONG not in emitted
    assert "forged" not in emitted
    # Exactly the agreed attributes, so no length or fragment can ride along.
    for signal in fire.signals:
        assert set(signal) == SIGNAL_ATTRIBUTES


def test_raw_host_path_and_user_agent_never_reach_an_attribute(fire):
    _, client = _locked_client()
    client.get(
        "/internal/secret-looking-path",
        headers={"host": "evil.example", "user-agent": "evil-agent/1.0"},
    )
    emitted = repr(fire.calls)
    assert "evil" not in emitted
    assert "secret-looking-path" not in emitted


def test_real_shapes_get_the_expected_classes(fire):
    _, client = _locked_client()
    client.get(
        "/api/v1/public/library/regulations",
        headers={
            "host": "api.rayhanai.com",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/130.0",
        },
    )
    signal = fire.signals[0]
    assert signal["host_class"] == "edge_api"
    assert signal["path_wing"] == "library"
    assert signal["ua_class"] == "browser"


# -- telemetry must not hurt a request ----------------------------------------


@pytest.mark.parametrize(
    ("mode", "headers", "status"),
    [
        ("observe", {}, 200),
        ("observe", {EDGE_SECRET_HEADER: SECRET}, 200),
        ("enforce", {EDGE_SECRET_HEADER: SECRET}, 200),
        ("enforce", {}, 403),
        ("enforce", {EDGE_SECRET_HEADER: WRONG}, 403),
    ],
)
def test_raising_logfire_changes_nothing_about_the_response(monkeypatch, mode, headers, status):
    monkeypatch.setattr(origin_lock, "_logfire", _FakeLogfire(raises=True))
    client = TestClient(_build_app(SECRET, mode=mode))

    assert client.get("/api/v1/cases", headers=headers).status_code == status
    assert client.get("/api/v1/cases", headers=headers).status_code == status


def test_raising_logfire_on_a_flush_does_not_break_the_request(monkeypatch, clock):
    fake = _FakeLogfire()
    monkeypatch.setattr(origin_lock, "_logfire", fake)
    _, client = _locked_client()
    client.get("/api/v1/cases")
    client.get("/api/v1/cases")

    fake.raises = True
    clock.now += FLUSH
    assert client.get("/api/v1/cases").status_code == 200


def test_raising_logfire_is_reported_once_and_without_detail(monkeypatch, caplog):
    monkeypatch.setattr(origin_lock, "_logfire", _FakeLogfire(raises=True))
    client = TestClient(_build_app(SECRET, mode="observe"))

    with caplog.at_level("WARNING"):
        client.get("/api/v1/cases")
        client.get("/api/v1/cases", headers={EDGE_SECRET_HEADER: WRONG})
        client.get("/api/v1/public/library/regulations")

    lines = [rec.getMessage() for rec in caplog.records if "signal failed" in rec.getMessage()]
    assert lines == ["Origin lock signal failed, continuing without it: RuntimeError"]


def test_raising_logfire_while_reporting_a_bad_mode_does_not_stop_the_boot(monkeypatch):
    monkeypatch.setattr(origin_lock, "_logfire", _FakeLogfire(raises=True))
    assert OriginLockMiddleware(app=None, secret=SECRET, mode="typo").mode == MODE_OBSERVE


def test_works_with_the_no_op_shim_when_logfire_is_not_installed(monkeypatch):
    monkeypatch.setattr(origin_lock, "_logfire", _NoopLogfire())
    client = TestClient(_build_app(SECRET, mode="observe"))
    assert client.get("/api/v1/cases").status_code == 200


def test_works_with_the_real_sdk_unconfigured():
    """No stub at all: whatever state logfire is in here, a request gets through."""
    client = TestClient(_build_app(SECRET, mode="observe"))
    assert client.get("/api/v1/cases").status_code == 200


# -- structure ----------------------------------------------------------------


@pytest.mark.parametrize("mode", ["observe", "off", "typo"])
def test_real_app_never_refuses_with_the_lock_in_a_non_enforcing_mode(monkeypatch, mode):
    """The production wiring through the ACTUAL stack: both values come from the
    environment and ``main.py`` passes no arguments. The route's own auth still
    answers, so the assertion is that the lock's refusal is not what came back.

    TestClient is used without its context manager for the same reason as
    ``test_real_app_health_reachable_with_lock_armed``: no lifespan, no Redis.
    """
    from backend.app.main import create_app

    monkeypatch.setenv(EDGE_SECRET_ENV, SECRET)
    monkeypatch.setenv(ORIGIN_LOCK_MODE_ENV, mode)
    client = TestClient(create_app())

    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/cases").json().get("detail") != ORIGIN_LOCK_MESSAGE


def test_stays_a_pure_asgi_middleware():
    """SSE depends on this: ``BaseHTTPMiddleware`` would wrap every response."""
    assert not issubclass(OriginLockMiddleware, BaseHTTPMiddleware)


@pytest.mark.parametrize("mode", ["observe", "enforce"])
def test_receive_and_send_reach_the_app_unwrapped(mode):
    """Any wrapper here would sit on the SSE path. Observed through identity."""
    seen = {}

    async def inner(scope, receive, send):
        seen["receive"], seen["send"] = receive, send

    async def receive():
        return {"type": "http.request"}

    async def send(_message):
        return None

    scope = {
        "type": "http",
        "path": "/api/v1/cases",
        "method": "GET",
        "headers": [(EDGE_SECRET_HEADER.encode(), SECRET.encode())],
    }
    middleware = OriginLockMiddleware(inner, secret=SECRET, mode=mode)
    asyncio.run(middleware(scope, receive, send))

    assert seen["receive"] is receive
    assert seen["send"] is send


@pytest.mark.parametrize("mode", ["observe", "enforce"])
def test_non_http_scopes_pass_through_without_a_signal(fire, mode):
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    middleware = OriginLockMiddleware(inner, secret=SECRET, mode=mode)
    asyncio.run(middleware({"type": "lifespan"}, None, None))

    assert seen == ["lifespan"]
    assert fire.calls == []


# -- closed vocabularies ------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("api.rayhanai.com", "edge_api"),
        ("API.RayhanAI.com:443", "edge_api"),
        ("api.rayhanai.com.", "edge_api"),
        ("rayhanai.com", "edge_other"),
        ("www.rayhanai.com", "edge_other"),
        ("luna-backend-production-35ba.up.railway.app", "railway_public"),
        ("luna-backend.railway.internal:8080", "railway_private"),
        ("localhost:8000", "local"),
        ("127.0.0.1:8000", "local"),
        ("[::1]:8000", "local"),
        ("203.0.113.9", "ip_literal"),
        ("[2001:db8::1]:443", "ip_literal"),
        ("testserver", "other"),
        ("api.rayhanai.com.evil.example", "other"),
        ("evilrayhanai.com", "other"),
        ("up.railway.app.evil.example", "other"),
        ("[not-closed", "other"),
        ("", "none"),
        ("   ", "none"),
        (None, "none"),
    ],
)
def test_classify_host(raw, expected):
    assert classify_host(raw) == expected


def test_classify_host_copes_with_a_huge_value():
    assert classify_host("x" * 100_000) == "other"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/api/v1/public/library/regulations", "library"),
        ("/api/v1/library/mine", "library"),
        ("/api/v1/public/blogs/some-slug", "blog"),
        ("/api/v1/public/blog", "blog"),
        ("/api/v1/blogs", "blog"),
        ("/api/v1/public/ask", "public"),
        ("/api/v1/public/events", "public"),
        ("/api/v1/auth/login", "account"),
        ("/api/v1/payments/webhook", "payments"),
        ("/internal/summarize-workspace-item", "internal"),
        ("/api/v1/cases", "app"),
        ("/api/v1/conversations/x/messages", "app"),
        ("/api/v1/_meta/observability", "app"),
        ("/.env", "other"),
        ("/wp-admin/setup.php", "other"),
        ("/", "other"),
        ("", "other"),
    ],
)
def test_classify_path(path, expected):
    assert classify_path(path) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0 Safari/537.36", "browser"),
        ("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", "crawler"),
        ("meta-externalagent/1.1 (+https://developers.facebook.com/docs/sharing/webmasters/crawler)", "crawler"),
        ("facebookexternalhit/1.1", "crawler"),
        ("pg_net/0.19.5", "service"),
        ("rayhan-marketing-dashboard/0.1", "service"),
        ("okhttp/4.12.0", "native_app"),
        ("Rayhan/12 CFNetwork/1568.100.1 Darwin/24.0.0", "native_app"),
        ("curl/8.7.1", "script"),
        ("python-requests/2.32.3", "script"),
        ("python-httpx/0.27.0", "script"),
        ("node", "script"),
        ("something-else/1.0", "other"),
        ("", "none"),
        (None, "none"),
    ],
)
def test_classify_user_agent(raw, expected):
    assert classify_user_agent(raw) == expected


def test_vocabularies_are_exactly_these():
    """Each word is a key in saved queries and alerts. Renaming one, or adding to
    the set that sizes the throttle table, should show up in review."""
    assert set(OUTCOMES) == {"match", "missing", "mismatch", "folded"}
    assert set(HOST_CLASSES) == {
        "edge_api", "edge_other", "railway_public", "railway_private",
        "local", "ip_literal", "other", "none",
    }
    assert set(PATH_WINGS) == {
        "library", "blog", "public", "account", "payments", "internal", "app", "other",
    }
    assert set(UA_CLASSES) == {
        "browser", "crawler", "service", "native_app", "script", "other", "none",
    }
    for vocabulary in (OUTCOMES, HOST_CLASSES, PATH_WINGS, UA_CLASSES):
        assert len(set(vocabulary)) == len(vocabulary)


def test_vocabulary_survives_the_scrubber():
    """Logfire's scrubber matches VALUES as well as keys: a value containing a
    pattern is replaced with "[Scrubbed due to ...]". The extra patterns in
    shared/observability.py are PII stems, so ``mobile_app`` would have come back
    redacted and silently broken every query filtering on it."""
    scrubbing = pytest.importorskip("logfire._internal.scrubbing")
    patterns = [*scrubbing.DEFAULT_PATTERNS, *_PII_EXTRA_PATTERNS]
    scrubbed = re.compile("|".join(patterns), re.IGNORECASE)

    words = [
        SIGNAL_EVENT,
        "origin_lock.bad_mode",
        "configured_value",
        *SIGNAL_ATTRIBUTES,
        *OUTCOMES,
        *HOST_CLASSES,
        *PATH_WINGS,
        *UA_CLASSES,
    ]
    assert [word for word in words if scrubbed.search(word)] == []
