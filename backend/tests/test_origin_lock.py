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

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from backend.app.middleware.origin_lock import (
    EDGE_SECRET_ENV,
    EDGE_SECRET_HEADER,
    EXEMPT_PATHS,
    ORIGIN_LOCK_MESSAGE,
    OriginLockMiddleware,
    is_exempt_path,
)

SECRET = "cf-transform-rule-value-32bytes-xyz"
WRONG = "cf-transform-rule-value-32bytes-XYZ"  # one case flip


# ---------------------------------------------------------------------------
# Throwaway app
# ---------------------------------------------------------------------------


def _build_app(secret: str | None = None, *, from_env: bool = False,
               with_cors: bool = False) -> FastAPI:
    """A minimal app carrying the same routes the real one exposes at the two
    paths that matter: the exempt healthcheck and an ordinary API route.

    ``from_env=True`` omits the ``secret`` kwarg entirely so the middleware reads
    ``EDGE_SECRET`` itself — that is the production wiring (``main.py`` calls
    ``add_middleware(OriginLockMiddleware)`` with no arguments).
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
        app.add_middleware(OriginLockMiddleware)
    else:
        app.add_middleware(OriginLockMiddleware, secret=secret)

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
