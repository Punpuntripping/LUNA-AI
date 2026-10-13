"""Native-app password reset (Rayhan mobile).

    POST /api/v1/auth/password/forgot  {"email"}                       -> 200 {"sent": true}
    POST /api/v1/auth/password/reset   {"email","code","new_password"} -> 200 LoginResponse (== /login)

No live GoTrue, DB or Redis — fakes shared with test_email_otp.py / test_signup.py.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from supabase_auth.errors import AuthApiError, AuthRetryableError, AuthSessionMissingError

from backend.app.api import auth as auth_mod
from backend.app.deps import get_redis, get_supabase, get_supabase_auth
from backend.app.errors import LunaHTTPException, luna_exception_handler
from backend.tests.test_email_otp import AUTH_ID, FakeRedis, FakeSupabase
from backend.tests.test_signup import FakeGoTrue as LoginGoTrue
from shared.config import Settings

EMAIL = "reset.user@example.com"
NEW_PASSWORD = "brand-new-pass"
REDIRECT = "https://rayhanai.com/auth/callback?next=%2Freset-password"


def _session_and_user():
    session = SimpleNamespace(
        access_token="access-xyz", refresh_token="refresh-xyz", expires_at=1_900_000_000
    )
    user = SimpleNamespace(
        id=AUTH_ID,
        email=EMAIL,
        user_metadata={},
        created_at="2026-01-01T00:00:00+00:00",
        identities=[SimpleNamespace(provider="google")],  # Google-only account
    )
    return session, user


class FakeRecoveryGoTrue:
    def __init__(self) -> None:
        self.recovers: list = []
        self.verifies: list = []
        self.updates: list = []
        self.sign_outs: list = []
        self.recover_error: Optional[Exception] = None
        self.verify_error: Optional[Exception] = None
        self.update_error: Optional[Exception] = None
        self.verify_returns_session = True
        self.parked = None
        self.closed = 0

    def reset_password_for_email(self, email, options=None):
        self.recovers.append((email, options))
        if self.recover_error:
            raise self.recover_error
        return None  # GoTrue: 200 for known AND unknown addresses

    def verify_otp(self, params):
        self.verifies.append(params)
        if self.verify_error:
            raise self.verify_error
        session, user = _session_and_user()
        if not self.verify_returns_session:
            return SimpleNamespace(session=None, user=None)
        self.parked = session
        return SimpleNamespace(session=session, user=user)

    def update_user(self, attributes):
        self.updates.append(attributes)
        if self.parked is None:
            raise AuthSessionMissingError()
        if self.update_error:
            raise self.update_error
        _, user = _session_and_user()
        return SimpleNamespace(user=user)

    def sign_out(self, options=None):
        self.sign_outs.append(options)
        self.parked = None

    def close(self):
        self.closed += 1


class _ExplodingAuth:
    def __getattr__(self, name):
        raise AssertionError(f"shared supabase_auth.auth.{name} used")


class Harness:
    def __init__(self, monkeypatch, *, shared_auth: Any = None):
        self.gotrue = FakeRecoveryGoTrue()
        self.supabase = FakeSupabase(has_password=False)
        self.redis = FakeRedis()
        self.clients_created = 0

        def _make_client():
            self.clients_created += 1
            return SimpleNamespace(auth=self.gotrue)

        monkeypatch.setattr(auth_mod, "create_isolated_anon_client", _make_client)
        settings = Settings.model_construct(PUBLIC_WEB_URL="https://rayhanai.com")
        monkeypatch.setattr(auth_mod, "get_settings", lambda: settings)

        app = FastAPI()
        app.add_exception_handler(LunaHTTPException, luna_exception_handler)
        app.include_router(auth_mod.router, prefix="/api/v1/auth")
        app.dependency_overrides[get_supabase] = lambda: self.supabase
        app.dependency_overrides[get_supabase_auth] = lambda: (
            shared_auth or SimpleNamespace(auth=_ExplodingAuth())
        )
        app.dependency_overrides[get_redis] = lambda: self.redis
        self.app = app
        self.client = TestClient(app)

    def forgot(self, email: str = EMAIL):
        return self.client.post("/api/v1/auth/password/forgot", json={"email": email})

    def reset(self, code: str = "123456", password: str = NEW_PASSWORD, email: str = EMAIL):
        return self.client.post(
            "/api/v1/auth/password/reset",
            json={"email": email, "code": code, "new_password": password},
        )

    def clear_cooldown(self):
        self.redis.store = {k: v for k, v in self.redis.store.items() if ":cd:" not in k}


def test_routes_registered() -> None:
    from backend.app.main import create_app

    paths = {getattr(r, "path", "") for r in create_app().routes}
    assert "/api/v1/auth/password/forgot" in paths
    assert "/api/v1/auth/password/reset" in paths


# ---------------------------------------------------------------------------
# /password/forgot
# ---------------------------------------------------------------------------


def test_forgot_happy_path_uses_web_redirect(monkeypatch) -> None:
    h = Harness(monkeypatch)
    res = h.forgot("  Reset.User@Example.com ")
    assert res.status_code == 200, res.text
    assert res.json() == {"sent": True}
    assert h.gotrue.recovers == [(EMAIL, {"redirect_to": REDIRECT})]
    assert h.clients_created == 1 and h.gotrue.closed == 1


def test_forgot_unknown_email_still_200(monkeypatch) -> None:
    # GoTrue's /recover returns 200 for an address with no account; the fake
    # does the same, and the route must not add any signal of its own.
    h = Harness(monkeypatch)
    res = h.forgot("nobody@example.com")
    assert res.status_code == 200 and res.json() == {"sent": True}


@pytest.mark.parametrize(
    "err",
    [
        AuthApiError("Error sending recovery email", 500, "unexpected_failure"),
        AuthRetryableError("down", 503),
        TimeoutError(),
        AuthApiError("captcha", 400, "captcha_failed"),
    ],
)
def test_forgot_send_failure_is_503_not_swallowed(monkeypatch, err) -> None:
    h = Harness(monkeypatch)
    h.gotrue.recover_error = err
    res = h.forgot()
    assert res.status_code == 503
    assert res.json()["detail"] == "تعذّر إرسال البريد حالياً، حاول لاحقاً"
    assert h.gotrue.closed == 1
    # No mail went out → cooldown released, an immediate retry is allowed.
    h.gotrue.recover_error = None
    assert h.forgot().status_code == 200


def test_forgot_gotrue_429_arabic(monkeypatch) -> None:
    h = Harness(monkeypatch)
    h.gotrue.recover_error = AuthApiError("rate", 429, "over_email_send_rate_limit")
    res = h.forgot()
    assert res.status_code == 429
    assert res.json()["detail"] == "تم تجاوز الحد المسموح من الطلبات"
    assert h.gotrue.closed == 1


def test_forgot_per_email_cooldown_own_keys(monkeypatch) -> None:
    h = Harness(monkeypatch)
    assert h.forgot().status_code == 200
    res = h.forgot()
    assert res.status_code == 429
    assert res.json()["detail"] == "انتظر قليلاً قبل طلب رمز جديد"
    assert len(h.gotrue.recovers) == 1
    assert any(k.startswith("pwreset:req:cd:") for k in h.redis.store)
    assert not any(k.startswith(("otp:", "signup:")) for k in h.redis.store)
    assert all(EMAIL not in k for k in h.redis.store)


def test_forgot_hourly_cap(monkeypatch) -> None:
    h = Harness(monkeypatch)
    for i in range(auth_mod.OTP_REQUEST_HOURLY_MAX):
        h.clear_cooldown()
        assert h.forgot().status_code == 200, i
    h.clear_cooldown()
    assert h.forgot().status_code == 429


def test_forgot_fails_open_without_redis(monkeypatch) -> None:
    h = Harness(monkeypatch)
    h.app.dependency_overrides[get_redis] = lambda: None
    assert h.forgot().status_code == 200
    assert h.forgot().status_code == 200


# ---------------------------------------------------------------------------
# /password/reset
# ---------------------------------------------------------------------------


def test_reset_happy_path_matches_login_shape(monkeypatch) -> None:
    h = Harness(monkeypatch, shared_auth=SimpleNamespace(auth=LoginGoTrue()))
    res = h.reset(" 123 456 ")
    assert res.status_code == 200, res.text
    assert h.gotrue.verifies == [{"email": EMAIL, "token": "123456", "type": "recovery"}]
    assert h.gotrue.updates == [{"password": NEW_PASSWORD}]
    assert h.gotrue.sign_outs == []
    assert h.clients_created == 1 and h.gotrue.closed == 1

    login = h.client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": NEW_PASSWORD}
    )
    assert login.status_code == 200
    r, l = res.json(), login.json()
    assert set(r) == set(l)
    assert set(r["user"]) == set(l["user"])
    assert r["access_token"] == "access-xyz" and r["refresh_token"] == "refresh-xyz"
    # Google-only account (has_password RPC False before) now has a password.
    assert r["user"]["has_password"] is True


def test_reset_writes_audit_row(monkeypatch) -> None:
    h = Harness(monkeypatch)
    assert h.reset().status_code == 200
    audits = [p for t, p in h.supabase.inserts if t == "audit_logs"]
    assert any(p["metadata"] == {"event": "password_reset"} for p in audits)


def test_reset_wrong_code_401(monkeypatch) -> None:
    h = Harness(monkeypatch)
    h.gotrue.verify_error = AuthApiError("Token has expired or is invalid", 403, "otp_expired")
    res = h.reset("000000")
    assert res.status_code == 401
    body = res.json()
    assert body["detail"] == "الرمز غير صحيح أو منتهي الصلاحية"
    assert body["error"]["code"] == "AUTH_INVALID"
    assert h.gotrue.updates == []
    assert h.gotrue.closed == 1


def test_reset_verify_without_session_is_401(monkeypatch) -> None:
    h = Harness(monkeypatch)
    h.gotrue.verify_returns_session = False
    assert h.reset().status_code == 401
    assert h.gotrue.updates == []


def test_reset_fail_limit_429(monkeypatch) -> None:
    h = Harness(monkeypatch)
    h.gotrue.verify_error = AuthApiError("bad", 403, "otp_expired")
    for _ in range(auth_mod.OTP_VERIFY_FAIL_MAX):
        assert h.reset("000000").status_code == 401
    before = len(h.gotrue.verifies)
    h.gotrue.verify_error = None
    res = h.reset("123456")
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "RATE_LIMITED"
    assert len(h.gotrue.verifies) == before  # GoTrue not even asked
    assert any(k.startswith("pwreset:verify:fails:") for k in h.redis.store)
    assert not any(k.startswith(("otp:verify:", "signup:verify:")) for k in h.redis.store)


def test_reset_gotrue_verify_429(monkeypatch) -> None:
    h = Harness(monkeypatch)
    h.gotrue.verify_error = AuthApiError("too many", 429, "over_request_rate_limit")
    assert h.reset().status_code == 429


def test_reset_success_clears_fails(monkeypatch) -> None:
    h = Harness(monkeypatch)
    h.gotrue.verify_error = AuthApiError("bad", 403, "otp_expired")
    h.reset("000000")
    h.gotrue.verify_error = None
    assert h.reset().status_code == 200
    assert not any(k.startswith("pwreset:verify:fails:") for k in h.redis.store)


@pytest.mark.parametrize("code", ["12345", "12345678901", "12a456", ""])
def test_reset_code_format_422(monkeypatch, code) -> None:
    h = Harness(monkeypatch)
    assert h.reset(code).status_code == 422
    assert h.gotrue.verifies == []


def test_reset_short_password_422_before_gotrue(monkeypatch) -> None:
    h = Harness(monkeypatch)
    assert h.reset(password="short7!").status_code == 422
    assert h.gotrue.verifies == []


def test_reset_weak_password_400_no_session(monkeypatch) -> None:
    h = Harness(monkeypatch)
    h.gotrue.update_error = AuthApiError("Password is too weak", 422, "weak_password")
    res = h.reset()
    assert res.status_code == 400
    body = res.json()
    assert body["detail"] == "كلمة المرور ضعيفة — اختر كلمة مرور أقوى"
    assert "access_token" not in body and "refresh_token" not in body
    assert h.gotrue.sign_outs == [{"scope": "local"}]  # recovery session revoked
    assert h.gotrue.closed == 1


def test_reset_same_password_400(monkeypatch) -> None:
    h = Harness(monkeypatch)
    h.gotrue.update_error = AuthApiError("same", 422, "same_password")
    res = h.reset()
    assert res.status_code == 400
    assert res.json()["detail"] == "كلمة المرور الجديدة يجب أن تختلف عن الحالية"


@pytest.mark.parametrize(
    "err,status",
    [
        (AuthApiError("boom", 500, "unexpected_failure"), 503),
        (AuthRetryableError("down", 503), 503),
        (TimeoutError(), 503),
        (AuthApiError("reauth", 400, "reauthentication_needed"), 400),
    ],
)
def test_reset_update_failure_returns_no_session(monkeypatch, err, status) -> None:
    h = Harness(monkeypatch)
    h.gotrue.update_error = err
    res = h.reset()
    assert res.status_code == status
    body = res.json()
    assert "access_token" not in body and "refresh_token" not in body
    assert h.gotrue.sign_outs == [{"scope": "local"}]
    assert h.gotrue.closed == 1
    # No session → no Redis session, no success audit.
    assert not any(k.startswith("session:") for k in h.redis.store)
    assert not any(t == "audit_logs" for t, _ in h.supabase.inserts)


def test_reset_verify_unavailable_503_and_closed(monkeypatch) -> None:
    h = Harness(monkeypatch)
    h.gotrue.verify_error = AuthRetryableError("down", 503)
    assert h.reset().status_code == 503
    assert h.gotrue.updates == []
    assert h.gotrue.closed == 1
    # An outage is not a wrong guess.
    assert not any(k.startswith("pwreset:verify:fails:") for k in h.redis.store)
