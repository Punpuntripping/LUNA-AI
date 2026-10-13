"""GET /api/v1/app/config — native app version/store config (mobile/04 §U1, B1).

Exercised through the REAL app factory so the test also proves the route is
mounted under /api/v1, needs no auth, and is exempt from the 426 gate.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.api import app_config as app_config_mod
from backend.app.middleware import app_version as gate_mod
from shared.config import Settings


@pytest.fixture()
def client():
    from backend.app.main import create_app

    return TestClient(create_app())


def _patch_settings(monkeypatch, **overrides) -> Settings:
    settings = Settings.model_construct(**overrides)
    monkeypatch.setattr(app_config_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(gate_mod, "get_settings", lambda: settings)
    return settings


def test_defaults_are_1_0_0_and_empty_store_urls() -> None:
    fields = Settings.model_fields
    for name in (
        "APP_MIN_VERSION_IOS",
        "APP_MIN_VERSION_ANDROID",
        "APP_LATEST_VERSION_IOS",
        "APP_LATEST_VERSION_ANDROID",
    ):
        assert fields[name].default == "1.0.0", name
        # validation-alias trap: env var name must equal the field name.
        assert fields[name].validation_alias is None
        assert fields[name].alias is None
    assert fields["APP_STORE_URL_IOS"].default == ""
    assert fields["APP_STORE_URL_ANDROID"].default == ""


def test_env_var_names_equal_field_names(monkeypatch) -> None:
    monkeypatch.setenv("APP_MIN_VERSION_ANDROID", "9.0.0")
    monkeypatch.setenv("APP_STORE_URL_IOS", "https://apps.apple.com/app/id1")
    s = Settings(
        SUPABASE_URL="https://x.supabase.co",
        SUPABASE_ANON_KEY="a",
        SUPABASE_SERVICE_KEY="s",
        SUPABASE_JWT_SECRET="j",
        _env_file=None,
    )
    assert s.APP_MIN_VERSION_ANDROID == "9.0.0"
    assert s.APP_STORE_URL_IOS == "https://apps.apple.com/app/id1"


def test_shape_and_values(client, monkeypatch) -> None:
    _patch_settings(
        monkeypatch,
        APP_MIN_VERSION_IOS="1.1.0",
        APP_MIN_VERSION_ANDROID="1.2.0",
        APP_LATEST_VERSION_IOS="1.3.0",
        APP_LATEST_VERSION_ANDROID="1.4.0",
        APP_STORE_URL_IOS="https://apps.apple.com/app/id123",
        APP_STORE_URL_ANDROID="https://play.google.com/store/apps/details?id=ai.rayhan",
    )
    res = client.get("/api/v1/app/config")
    assert res.status_code == 200
    assert res.json() == {
        "min_supported_version": {"ios": "1.1.0", "android": "1.2.0"},
        "latest_version": {"ios": "1.3.0", "android": "1.4.0"},
        "store_urls": {
            "ios": "https://apps.apple.com/app/id123",
            "android": "https://play.google.com/store/apps/details?id=ai.rayhan",
        },
    }


def test_defaults_served(client, monkeypatch) -> None:
    _patch_settings(monkeypatch)  # model_construct → field defaults
    body = client.get("/api/v1/app/config").json()
    assert body["min_supported_version"] == {"ios": "1.0.0", "android": "1.0.0"}
    assert body["latest_version"] == {"ios": "1.0.0", "android": "1.0.0"}
    assert body["store_urls"] == {"ios": "", "android": ""}


def test_cache_header(client, monkeypatch) -> None:
    _patch_settings(monkeypatch)
    res = client.get("/api/v1/app/config")
    assert res.headers["cache-control"] == "public, max-age=300"


def test_no_auth_needed(client, monkeypatch) -> None:
    _patch_settings(monkeypatch)
    # No Authorization header at all, and a junk one: both 200.
    assert client.get("/api/v1/app/config").status_code == 200
    res = client.get("/api/v1/app/config", headers={"Authorization": "Bearer junk"})
    assert res.status_code == 200


def test_outdated_app_can_still_read_config(client, monkeypatch) -> None:
    _patch_settings(monkeypatch, APP_MIN_VERSION_ANDROID="9.0.0")
    res = client.get(
        "/api/v1/app/config",
        headers={"X-App-Platform": "android", "X-App-Version": "1.0.0"},
    )
    assert res.status_code == 200
    assert res.json()["min_supported_version"]["android"] == "9.0.0"
