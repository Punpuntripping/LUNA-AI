"""Native-client plumbing (mobile/04 §N3/B6, §S3/B7).

B6 — GET /api/v1/auth/me with a VERIFIED bearer is rate-limited per user, not
     per IP (KSA CGNAT). Every other /auth/* path, and an unverifiable token,
     keep the per-IP key exactly as before.
B7 — X-App-Platform / -Version / -Build / -Update-Id are captured as request
     context + span attributes; the web (no headers) records nothing.
"""
from __future__ import annotations

from typing import Optional

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import shared.auth.jwt as jwt_mod
from backend.app.middleware import request_context as rc
from backend.app.middleware.rate_limit import AUTH_RATE_LIMIT, RateLimitMiddleware
from backend.tests.test_rate_limit_library import FakeRedis
from shared.auth.jwt import TokenExpiredError, TokenInvalidError

GOOD = "good.token.sig"
FORGED = "forged.token.sig"
EXPIRED = "expired.token.sig"


@pytest.fixture(autouse=True)
def _fake_decode(monkeypatch):
    def decode(token: str) -> dict:
        if token == GOOD:
            return {"sub": "user-abc"}
        if token == EXPIRED:
            raise TokenExpiredError()
        raise TokenInvalidError("bad signature")

    monkeypatch.setattr(jwt_mod, "decode_token", decode)


def _rl_app(redis: FakeRedis) -> FastAPI:
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)
    app.state.redis = redis

    @app.api_route("/{full_path:path}", methods=["GET", "POST"])
    async def catch_all(full_path: str):
        return {"ok": True}

    return app


def _keys(redis: FakeRedis, path: str) -> list[str]:
    return [k for k in redis.keys_seen if k.endswith(f":{path}:60")]


def _get(client, path, token: Optional[str] = None, method="get"):
    headers = {"X-Forwarded-For": "5.5.5.5"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return getattr(client, method)(path, headers=headers)


# --- B6 ---------------------------------------------------------------------


def test_me_with_verified_token_is_keyed_per_user() -> None:
    redis = FakeRedis()
    client = TestClient(_rl_app(redis))
    assert _get(client, "/api/v1/auth/me", GOOD).status_code == 200
    assert _keys(redis, "/api/v1/auth/me") == ["ratelimit:user:user-abc:/api/v1/auth/me:60"]


@pytest.mark.parametrize("token", [FORGED, EXPIRED, None, ""])
def test_me_without_verifiable_token_stays_per_ip(token) -> None:
    redis = FakeRedis()
    client = TestClient(_rl_app(redis))
    _get(client, "/api/v1/auth/me", token)
    assert _keys(redis, "/api/v1/auth/me") == ["ratelimit:5.5.5.5:/api/v1/auth/me:60"]


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/api/v1/auth/login"),
        ("post", "/api/v1/auth/refresh"),
        ("post", "/api/v1/auth/signup"),
        ("post", "/api/v1/auth/logout"),
        ("post", "/api/v1/auth/me"),  # only GET is user-keyed
    ],
)
def test_other_auth_paths_stay_per_ip_even_with_valid_token(method, path) -> None:
    redis = FakeRedis()
    client = TestClient(_rl_app(redis))
    _get(client, path, GOOD, method=method)
    assert _keys(redis, path) == [f"ratelimit:5.5.5.5:{path}:60"]


def test_me_per_user_bucket_keeps_auth_limit() -> None:
    redis = FakeRedis()
    client = TestClient(_rl_app(redis))
    codes = [_get(client, "/api/v1/auth/me", GOOD).status_code for _ in range(AUTH_RATE_LIMIT + 1)]
    assert codes[:AUTH_RATE_LIMIT] == [200] * AUTH_RATE_LIMIT
    assert codes[-1] == 429


def test_cgnat_users_on_one_ip_do_not_share_me_bucket(monkeypatch) -> None:
    def decode(token: str) -> dict:
        return {"sub": token}

    monkeypatch.setattr(jwt_mod, "decode_token", decode)
    redis = FakeRedis()
    client = TestClient(_rl_app(redis))
    for i in range(AUTH_RATE_LIMIT + 5):
        assert _get(client, "/api/v1/auth/me", f"user-{i}").status_code == 200


def test_non_auth_paths_unchanged() -> None:
    redis = FakeRedis()
    client = TestClient(_rl_app(redis))
    _get(client, "/api/v1/conversations", FORGED)
    # Historical behaviour: unverified sub decode; a junk token falls back to IP.
    assert _keys(redis, "/api/v1/conversations") == ["ratelimit:5.5.5.5:/api/v1/conversations:60"]


# --- B7 ---------------------------------------------------------------------


def _ctx_app(captured: list) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def mw(request: Request, call_next):
        rc.capture_request_context(request)
        captured.append(rc.get_app_client())
        return await call_next(request)

    @app.get("/x")
    async def x():
        return {"ok": True}

    return app


def test_app_headers_captured(monkeypatch) -> None:
    span_attrs: list = []
    monkeypatch.setattr(rc, "_set_span_attributes", lambda attrs: span_attrs.append(attrs))
    captured: list = []
    client = TestClient(_ctx_app(captured))
    client.get(
        "/x",
        headers={
            "X-App-Platform": "ios",
            "X-App-Version": "1.0.3",
            "X-App-Build": "42",
            "X-App-Update-Id": "embedded",
        },
    )
    expected = {
        "app.platform": "ios",
        "app.version": "1.0.3",
        "app.build": "42",
        "app.update_id": "embedded",
    }
    assert captured == [expected]
    assert span_attrs == [expected]


def test_web_request_records_nothing(monkeypatch) -> None:
    span_attrs: list = []
    monkeypatch.setattr(rc, "_set_span_attributes", lambda attrs: span_attrs.append(attrs))
    captured: list = []
    TestClient(_ctx_app(captured)).get("/x")
    assert captured == [None]
    assert span_attrs == []


def test_app_header_values_are_truncated(monkeypatch) -> None:
    monkeypatch.setattr(rc, "_set_span_attributes", lambda attrs: None)
    captured: list = []
    TestClient(_ctx_app(captured)).get("/x", headers={"X-App-Build": "9" * 500})
    assert captured == [{"app.build": "9" * 64}]


def test_span_attributes_land_on_current_span(monkeypatch) -> None:
    from opentelemetry import trace

    class FakeSpan:
        def __init__(self):
            self.attrs = {}

        def is_recording(self):
            return True

        def set_attributes(self, attrs):
            self.attrs.update(attrs)

    span = FakeSpan()
    monkeypatch.setattr(trace, "get_current_span", lambda: span)
    rc._set_span_attributes({"app.platform": "android"})
    assert span.attrs == {"app.platform": "android"}
