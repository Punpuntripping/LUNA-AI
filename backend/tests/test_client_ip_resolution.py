"""Client-IP resolution and the ``TRUST_CF_HEADERS`` gate.

Covers ``.claude/plans/cloudflare_navigation_hardening.md`` step 3.5.

The plan says "switch the rate limiter to ``CF-Connecting-IP``". Switching
*unconditionally* would be strictly worse than the status quo: every
rayhanai.com DNS record is still grey-clouded, so no proxy sets that header and
it would be 100% attacker-supplied — one header per request buys one fresh
rate-limit bucket. So the switch is gated on ``TRUST_CF_HEADERS`` (default
FALSE), and these tests pin both states:

1. **Flag OFF (today)** — ``CF-Connecting-IP`` is ignored entirely; behaviour is
   byte-for-byte the historical leftmost-XFF-then-socket-peer chain. This is the
   security-load-bearing half: the header must not move a Redis bucket.
2. **Flag ON (the instant the orange cloud is enabled)** — ``CF-Connecting-IP``
   wins, with the old chain kept as the fallback for callers Cloudflare never
   sees (the Railway healthcheck, local dev, tests).
3. **One resolver, two call sites** — the limiter middleware/`route_limits` and
   Turnstile's ``remoteip`` in ``public_ask`` must both move together; they used
   to duplicate the logic.

No Redis and no live app for the unit half; the end-to-end half reuses the fake
async Redis from ``test_rate_limit_library`` so the assertion is on the exact
bucket keys the middleware builds.
"""
from __future__ import annotations

from typing import Dict, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from backend.app.middleware import rate_limit as rl
from backend.app.middleware.rate_limit import (
    RateLimitMiddleware,
    client_ip_for_rate_limit,
    resolve_client_ip,
    trust_cf_headers,
)
from backend.tests.test_rate_limit_library import FakeRedis

CF = "cf-connecting-ip"
XFF = "x-forwarded-for"
PEER = "198.51.100.7"


@pytest.fixture(autouse=True)
def _flag_unset(monkeypatch):
    """Never inherit a developer's shell. Each test sets the flag explicitly."""
    monkeypatch.delenv(rl.TRUST_CF_HEADERS_ENV, raising=False)
    yield


def _request(headers: Optional[Dict[str, str]] = None, peer: Optional[str] = PEER) -> Request:
    """A bare ASGI scope — enough surface for the resolver, no app required."""
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "query_string": b"",
            "headers": raw,
            "client": (peer, 54321) if peer else None,
        }
    )


# ===========================================================================
# 1. The flag itself
# ===========================================================================


def test_flag_defaults_to_false_when_unset() -> None:
    """Ships OFF. This is the whole point: grey cloud => do not trust CF."""
    assert trust_cf_headers() is False


@pytest.mark.parametrize("truthy", ["1", "true", "TRUE", "  yes  ", "on", "On"])
def test_flag_truthy_vocabulary(monkeypatch, truthy: str) -> None:
    monkeypatch.setenv(rl.TRUST_CF_HEADERS_ENV, truthy)
    assert trust_cf_headers() is True


@pytest.mark.parametrize("falsy", ["", "   ", "0", "false", "no", "off", "maybe", "2"])
def test_flag_junk_never_turns_trust_on(monkeypatch, falsy: str) -> None:
    """Anything unrecognised must fail SAFE (off), never on."""
    monkeypatch.setenv(rl.TRUST_CF_HEADERS_ENV, falsy)
    assert trust_cf_headers() is False


def test_flag_is_read_fresh_not_frozen_at_import(monkeypatch) -> None:
    """Deliberately not a module constant / not lru_cached Settings — the flip
    must take effect from the env alone."""
    assert trust_cf_headers() is False
    monkeypatch.setenv(rl.TRUST_CF_HEADERS_ENV, "true")
    assert trust_cf_headers() is True
    monkeypatch.setenv(rl.TRUST_CF_HEADERS_ENV, "false")
    assert trust_cf_headers() is False


# ===========================================================================
# 2. Flag OFF — today's behaviour, unchanged
# ===========================================================================


def test_off_ignores_cf_connecting_ip_completely() -> None:
    """The security-load-bearing assertion. While grey-clouded the header is
    attacker-supplied, so it must not influence the answer at all."""
    req = _request({CF: "1.2.3.4"})
    assert resolve_client_ip(req) == PEER


def test_off_cf_header_loses_to_xff() -> None:
    req = _request({CF: "1.2.3.4", XFF: "203.0.113.9, 10.0.0.1"})
    assert resolve_client_ip(req) == "203.0.113.9"


def test_off_uses_leftmost_xff_hop() -> None:
    req = _request({XFF: "203.0.113.9, 10.0.0.1, 10.0.0.2"})
    assert resolve_client_ip(req) == "203.0.113.9"


def test_off_falls_back_to_socket_peer() -> None:
    assert resolve_client_ip(_request()) == PEER


def test_no_headers_and_no_peer_is_none() -> None:
    assert resolve_client_ip(_request(peer=None)) is None


def test_blank_leading_xff_hop_is_skipped() -> None:
    """``", 203.0.113.9"`` used to resolve to the empty string, collapsing every
    such caller into one shared bucket keyed on ``""``."""
    assert resolve_client_ip(_request({XFF: ", 203.0.113.9"})) == "203.0.113.9"
    assert resolve_client_ip(_request({XFF: "   "})) == PEER


# ===========================================================================
# 3. Flag ON — the cutover state
# ===========================================================================


@pytest.fixture
def _trusted(monkeypatch):
    monkeypatch.setenv(rl.TRUST_CF_HEADERS_ENV, "true")
    yield


def test_on_prefers_cf_connecting_ip(_trusted) -> None:
    req = _request({CF: "1.2.3.4", XFF: "203.0.113.9, 10.0.0.1"})
    assert resolve_client_ip(req) == "1.2.3.4"


def test_on_falls_back_to_xff_when_cf_absent(_trusted) -> None:
    """Direct-to-origin callers Cloudflare never sees — the Railway healthcheck
    on /api/v1/health, local dev — must still resolve."""
    assert resolve_client_ip(_request({XFF: "203.0.113.9"})) == "203.0.113.9"


def test_on_falls_back_when_cf_is_blank(_trusted) -> None:
    assert resolve_client_ip(_request({CF: "   ", XFF: "203.0.113.9"})) == "203.0.113.9"
    assert resolve_client_ip(_request({CF: ""})) == PEER


def test_on_falls_back_to_socket_peer(_trusted) -> None:
    assert resolve_client_ip(_request()) == PEER


def test_on_trims_whitespace_around_cf_value(_trusted) -> None:
    assert resolve_client_ip(_request({CF: "  1.2.3.4  "})) == "1.2.3.4"


# ===========================================================================
# 4. The rate-limit narrowing
# ===========================================================================


def test_rate_limit_helper_never_returns_none() -> None:
    """A Redis key cannot be None; unattributable callers share one bucket."""
    assert client_ip_for_rate_limit(_request(peer=None)) == "unknown"


def test_rate_limit_helper_matches_the_resolver() -> None:
    req = _request({XFF: "203.0.113.9"})
    assert client_ip_for_rate_limit(req) == resolve_client_ip(req) == "203.0.113.9"


# ===========================================================================
# 5. End-to-end through the middleware — the bucket keys
# ===========================================================================


def _middleware_app(redis: FakeRedis) -> FastAPI:
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)
    app.state.redis = redis

    @app.get("/api/v1/conversations")
    async def convos() -> Dict[str, bool]:
        return {"ok": True}

    return app


def test_off_forged_cf_header_cannot_mint_fresh_buckets() -> None:
    """Grey cloud: 5 different CF-Connecting-IP values from one caller must all
    land in ONE bucket. If this ever fails, the flag was turned on too early."""
    redis = FakeRedis()
    client = TestClient(_middleware_app(redis))

    for i in range(5):
        r = client.get(
            "/api/v1/conversations",
            headers={CF: f"1.2.3.{i}", XFF: "203.0.113.9"},
        )
        assert r.status_code == 200, r.text

    assert len(set(redis.keys_seen)) == 1, redis.keys_seen
    assert "203.0.113.9" in redis.keys_seen[0]
    assert "1.2.3." not in redis.keys_seen[0]


def test_on_cf_header_drives_the_bucket(_trusted) -> None:
    """Orange cloud: Cloudflare overwrites CF-Connecting-IP on ingress, so
    distinct values are distinct callers and a forged XFF no longer matters."""
    redis = FakeRedis()
    client = TestClient(_middleware_app(redis))

    for i in range(3):
        # Same forged XFF every time — under the flag it must be ignored.
        r = client.get(
            "/api/v1/conversations",
            headers={CF: f"1.2.3.{i}", XFF: "203.0.113.9"},
        )
        assert r.status_code == 200, r.text

    keys = set(redis.keys_seen)
    assert len(keys) == 3, keys
    assert all("203.0.113.9" not in k for k in keys), keys


def test_on_forged_xff_cannot_escape_the_cf_bucket(_trusted) -> None:
    """The trap the plan names: Cloudflare APPENDS to a client-supplied XFF, so
    rotating XFF must buy nothing once CF-Connecting-IP is authoritative."""
    redis = FakeRedis()
    client = TestClient(_middleware_app(redis))

    for i in range(5):
        r = client.get(
            "/api/v1/conversations",
            headers={CF: "1.2.3.4", XFF: f"10.0.0.{i}"},
        )
        assert r.status_code == 200, r.text

    assert len(set(redis.keys_seen)) == 1, redis.keys_seen
    assert "1.2.3.4" in redis.keys_seen[0]


# ===========================================================================
# 6. One resolver, two call sites
# ===========================================================================


def test_public_ask_shares_the_same_resolver() -> None:
    """Turnstile's ``remoteip`` used to duplicate the XFF logic, so the cutover
    would have had to be made twice — and half-done trust is a bug either way."""
    from backend.app.api import public_ask

    assert public_ask.resolve_client_ip is resolve_client_ip


def test_public_ask_client_ip_tracks_the_flag(monkeypatch) -> None:
    from backend.app.api import public_ask

    req = _request({CF: "1.2.3.4", XFF: "203.0.113.9"})
    assert public_ask._client_ip(req) == "203.0.113.9"      # flag off

    monkeypatch.setenv(rl.TRUST_CF_HEADERS_ENV, "true")
    assert public_ask._client_ip(req) == "1.2.3.4"           # flag on


def test_public_ask_client_ip_still_optional() -> None:
    """``verify_turnstile`` accepts ``remoteip=None``; the contract is unchanged."""
    from backend.app.api import public_ask

    assert public_ask._client_ip(_request(peer=None)) is None


def test_route_limits_uses_the_shared_helper() -> None:
    """``route_limits`` imports ``client_ip_for_rate_limit`` by name — keep the
    export stable or the fail-CLOSED library limiter silently forks its trust."""
    from backend.app.middleware import route_limits

    assert route_limits.client_ip_for_rate_limit is client_ip_for_rate_limit
