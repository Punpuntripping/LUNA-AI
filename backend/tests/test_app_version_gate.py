"""426 APP_UPDATE_REQUIRED gate for native clients (mobile/04 §U2, B2).

Web requests (no X-App-Platform) must never be touched; only ios/android with a
parseable X-App-Version strictly below the platform minimum are refused.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from backend.app.errors import ErrorCode
from backend.app.middleware import app_version as gate_mod
from backend.app.middleware.app_version import (
    AppVersionGateMiddleware,
    parse_version,
    update_required,
)
from shared.config import Settings

MSG = "يلزم تحديث التطبيق إلى أحدث إصدار للمتابعة"


@pytest.fixture(autouse=True)
def _minimums(monkeypatch):
    settings = Settings.model_construct(
        APP_MIN_VERSION_IOS="1.4.0", APP_MIN_VERSION_ANDROID="2.0.0"
    )
    monkeypatch.setattr(gate_mod, "get_settings", lambda: settings)
    return settings


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(AppVersionGateMiddleware)

    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/v1/app/config")
    async def config():
        return {"ok": True}

    @app.post("/api/v1/auth/logout")
    async def logout():
        return {"success": True}

    @app.get("/api/v1/conversations")
    async def conversations():
        return {"conversations": []}

    @app.get("/api/v1/stream")
    async def stream():
        async def gen():
            yield b"data: a\n\n"
            yield b"data: b\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


@pytest.fixture()
def client():
    return TestClient(_app())


def _h(platform=None, version=None) -> dict:
    h = {}
    if platform is not None:
        h["X-App-Platform"] = platform
    if version is not None:
        h["X-App-Version"] = version
    return h


# --- parsing ---------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1.2.3", (1, 2, 3)),
        ("1.10.0", (1, 10, 0)),
        ("2", (2, 0, 0)),
        ("1.4", (1, 4, 0)),
        (" v1.2.3 ", (1, 2, 3)),
        ("1.2.3-beta.1", (1, 2, 3)),
        ("1.2.3+45", (1, 2, 3)),
    ],
)
def test_parse_version_ok(raw, expected) -> None:
    assert parse_version(raw) == expected


@pytest.mark.parametrize(
    "raw", [None, "", "  ", "abc", "1..2", "1.2.3.4", "x1.2", "1.2.3 beta", "9" * 40]
)
def test_parse_version_garbage_is_none(raw) -> None:
    assert parse_version(raw) is None


def test_numeric_not_lexicographic() -> None:
    # "1.10.0" > "1.4.0" numerically (lexicographically it would be smaller).
    assert parse_version("1.10.0") > parse_version("1.9.0")
    assert update_required("ios", "1.10.0") is False
    assert update_required("ios", "1.3.10") is True


# --- header absent: never touched -----------------------------------------


def test_no_headers_passes(client) -> None:
    res = client.get("/api/v1/conversations")
    assert res.status_code == 200


def test_version_without_platform_passes(client) -> None:
    res = client.get("/api/v1/conversations", headers=_h(version="0.0.1"))
    assert res.status_code == 200


@pytest.mark.parametrize("platform", ["web", "desktop", "", "iosx"])
def test_unknown_platform_passes(client, platform) -> None:
    res = client.get("/api/v1/conversations", headers=_h(platform, "0.0.1"))
    assert res.status_code == 200


# --- below minimum → 426 ---------------------------------------------------


@pytest.mark.parametrize(
    "platform,version",
    [("ios", "1.3.9"), ("IOS", "1.0.0"), ("android", "1.9.9"), ("Android", "1")],
)
def test_below_minimum_is_426_arabic(client, platform, version) -> None:
    res = client.get("/api/v1/conversations", headers=_h(platform, version))
    assert res.status_code == 426
    body = res.json()
    assert body == {
        "error": {"code": "APP_UPDATE_REQUIRED", "message": MSG, "status": 426},
        "detail": MSG,
    }
    assert ErrorCode.APP_UPDATE_REQUIRED.value == "APP_UPDATE_REQUIRED"


@pytest.mark.parametrize(
    "platform,version",
    [("ios", "1.4.0"), ("ios", "1.10.0"), ("android", "2.0.0"), ("android", "10.0.0")],
)
def test_at_or_above_minimum_passes(client, platform, version) -> None:
    res = client.get("/api/v1/conversations", headers=_h(platform, version))
    assert res.status_code == 200


def test_post_is_gated_too(client) -> None:
    res = client.post("/api/v1/auth/login", headers=_h("ios", "1.0.0"))
    assert res.status_code == 426


# --- garbage tolerated -----------------------------------------------------


@pytest.mark.parametrize("version", [None, "", "garbage", "1.2.3.4", "0.0.1; DROP"])
def test_garbage_or_missing_version_never_blocks(client, version) -> None:
    headers = {"X-App-Platform": "ios"}
    if version is not None:
        headers["X-App-Version"] = version
    res = client.get("/api/v1/conversations", headers=headers)
    assert res.status_code == 200


def test_garbage_minimum_never_blocks(client, monkeypatch) -> None:
    bad = Settings.model_construct(APP_MIN_VERSION_IOS="not-a-version")
    monkeypatch.setattr(gate_mod, "get_settings", lambda: bad)
    res = client.get("/api/v1/conversations", headers=_h("ios", "0.0.1"))
    assert res.status_code == 200


def test_gate_bug_fails_open(client, monkeypatch) -> None:
    def boom():
        raise RuntimeError("settings exploded")

    monkeypatch.setattr(gate_mod, "get_settings", boom)
    res = client.get("/api/v1/conversations", headers=_h("ios", "0.0.1"))
    assert res.status_code == 200


# --- exempt paths ----------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [("get", "/api/v1/app/config"), ("get", "/api/v1/health"), ("post", "/api/v1/auth/logout")],
)
def test_exempt_paths_pass_even_when_outdated(client, method, path) -> None:
    res = getattr(client, method)(path, headers=_h("android", "0.1.0"))
    assert res.status_code == 200


def test_streaming_passthrough_untouched(client) -> None:
    res = client.get("/api/v1/stream", headers=_h("ios", "1.4.0"))
    assert res.status_code == 200
    assert res.text == "data: a\n\ndata: b\n\n"


# --- wiring in the real app -------------------------------------------------


def test_real_app_426_carries_request_id_and_web_untouched(monkeypatch) -> None:
    from backend.app.main import create_app

    client = TestClient(create_app())
    res = client.get(
        "/api/v1/conversations",
        headers={**_h("android", "1.0.0"), "X-Request-ID": "rid-123"},
    )
    assert res.status_code == 426
    assert res.json()["error"]["code"] == "APP_UPDATE_REQUIRED"
    # Innermost middleware → the request-id + security headers still apply.
    assert res.headers["x-request-id"] == "rid-123"
    assert res.headers["x-content-type-options"] == "nosniff"

    # Same request without the native headers is NOT a 426 (it is the normal
    # unauthenticated answer from the route's auth dependency).
    web = client.get("/api/v1/conversations")
    assert web.status_code != 426
