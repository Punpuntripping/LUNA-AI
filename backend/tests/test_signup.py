"""Native-app signup + /auth/refresh on a throwaway client (mobile/04 §B3, §B8).

    POST /api/v1/auth/signup          -> 200 {"status":"verification_required","email"}
                                         | 200 LoginResponse (confirmation off)
    POST /api/v1/auth/signup/verify   -> 200 LoginResponse (== /login)
    POST /api/v1/auth/signup/resend   -> 200 {"sent": true}
    POST /api/v1/auth/refresh         -> 200 TokenResponse, per-request client

No live GoTrue, DB or Redis — the fakes are shared with test_email_otp.py.
"""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any, Optional

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from supabase_auth.errors import AuthApiError, AuthRetryableError

from backend.app.api import auth as auth_mod
from backend.app.deps import get_redis, get_supabase, get_supabase_auth
from backend.app.errors import LunaHTTPException, luna_exception_handler
from backend.tests.test_email_otp import AUTH_ID, FakeRedis, FakeSupabase
from shared.config import Settings

EMAIL = "new.user@example.com"
PASSWORD = "s3cret-pass"
NAME = "سارة العتيبي"
TERMS = "2026-06-22"


def _session_and_user(identities: Any = ("email",)):
    session = SimpleNamespace(
        access_token="access-xyz", refresh_token="refresh-xyz", expires_at=1_900_000_000
    )
    user = SimpleNamespace(
        id=AUTH_ID,
        email=EMAIL,
        user_metadata={"full_name_ar": NAME},
        created_at="2026-01-01T00:00:00+00:00",
        identities=None if identities is None else [SimpleNamespace(provider=p) for p in identities],
    )
    return session, user


class FakeGoTrue:
    def __init__(self) -> None:
        self.signups: list = []
        self.verifies: list = []
        self.resends: list = []
        self.signup_error: Optional[Exception] = None
        self.verify_error: Optional[Exception] = None
        self.resend_error: Optional[Exception] = None
        self.signup_identities: Any = ("email",)
        self.signup_returns_session = False
        self.closed = 0

    def sign_up(self, credentials):
        self.signups.append(credentials)
        if self.signup_error:
            raise self.signup_error
        session, user = _session_and_user(self.signup_identities)
        return SimpleNamespace(
            user=user, session=session if self.signup_returns_session else None
        )

    def verify_otp(self, params):
        self.verifies.append(params)
        if self.verify_error:
            raise self.verify_error
        session, user = _session_and_user()
        return SimpleNamespace(session=session, user=user)

    def resend(self, credentials):
        self.resends.append(credentials)
        if self.resend_error:
            raise self.resend_error
        return SimpleNamespace(user=None, session=None)

    def sign_in_with_password(self, credentials):
        session, user = _session_and_user()
        return SimpleNamespace(session=session, user=user)

    def close(self):
        self.closed += 1


class _ExplodingAuth:
    """The shared supabase_auth singleton — signup/refresh must never use it."""

    def __getattr__(self, name):
        raise AssertionError(f"shared supabase_auth.auth.{name} used")


class Harness:
    def __init__(self, monkeypatch, *, shared_auth: Any = None):
        self.gotrue = FakeGoTrue()
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

    def signup(self, headers: Optional[dict] = None, **overrides):
        body = {
            "email": EMAIL,
            "password": PASSWORD,
            "full_name": f"  {NAME}  ",
            "terms_version": TERMS,
        }
        body.update(overrides)
        return self.client.post("/api/v1/auth/signup", json=body, headers=headers or {})

    def verify(self, code: str = "123456", email: str = EMAIL):
        return self.client.post(
            "/api/v1/auth/signup/verify", json={"email": email, "code": code}
        )

    def resend(self, email: str = EMAIL):
        return self.client.post("/api/v1/auth/signup/resend", json={"email": email})

    def clear_cooldown(self):
        self.redis.store = {k: v for k, v in self.redis.store.items() if ":cd:" not in k}


# ---------------------------------------------------------------------------
# Routes registered
# ---------------------------------------------------------------------------


def test_routes_registered() -> None:
    from backend.app.main import create_app

    paths = {getattr(r, "path", "") for r in create_app().routes}
    for p in ("/api/v1/auth/signup", "/api/v1/auth/signup/verify", "/api/v1/auth/signup/resend"):
        assert p in paths


# ---------------------------------------------------------------------------
# /signup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "platform,source",
    [("ios", "ios_app"), ("Android", "android_app"), (None, "app"), ("web", "app")],
)
def test_signup_metadata_and_throwaway_client(monkeypatch, platform, source) -> None:
    h = Harness(monkeypatch)
    headers = {"X-App-Platform": platform} if platform else {}
    res = h.signup(headers=headers, email="  New.User@Example.com ")
    assert res.status_code == 200, res.text
    assert res.json() == {"status": "verification_required", "email": EMAIL}

    assert h.gotrue.signups == [
        {
            "email": EMAIL,
            "password": PASSWORD,
            "options": {
                "data": {
                    "full_name_ar": NAME,  # trimmed
                    "terms_version": TERMS,
                    "marketing_opt_in": False,  # default OFF
                    "signup_source": source,
                },
                "email_redirect_to": "https://rayhanai.com/auth/callback",
            },
        }
    ]
    assert h.clients_created == 1 and h.gotrue.closed == 1


def test_signup_marketing_opt_in_true_passes_through(monkeypatch) -> None:
    h = Harness(monkeypatch)
    assert h.signup(marketing_opt_in=True).status_code == 200
    assert h.gotrue.signups[0]["options"]["data"]["marketing_opt_in"] is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"password": "short7!"},
        {"full_name": "   "},
        {"full_name": None},
        {"terms_version": ""},
        {"email": "not-an-email"},
    ],
)
def test_signup_validation_422(monkeypatch, overrides) -> None:
    h = Harness(monkeypatch)
    assert h.signup(**overrides).status_code == 422
    assert h.gotrue.signups == []


def test_signup_requires_terms_version_and_full_name(monkeypatch) -> None:
    h = Harness(monkeypatch)
    res = h.client.post(
        "/api/v1/auth/signup", json={"email": EMAIL, "password": PASSWORD}
    )
    assert res.status_code == 422


def test_signup_duplicate_empty_identities_409(monkeypatch) -> None:
    h = Harness(monkeypatch)
    h.gotrue.signup_identities = ()
    res = h.signup()
    assert res.status_code == 409
    assert res.json()["detail"] == "البريد الإلكتروني مسجل مسبقاً"


@pytest.mark.parametrize("code", ["user_already_exists", "email_exists"])
def test_signup_duplicate_gotrue_error_409(monkeypatch, code) -> None:
    h = Harness(monkeypatch)
    h.gotrue.signup_error = AuthApiError("User already registered", 422, code)
    res = h.signup()
    assert res.status_code == 409
    assert res.json()["error"]["message"] == "البريد الإلكتروني مسجل مسبقاً"


def test_signup_identities_none_is_not_a_duplicate(monkeypatch) -> None:
    h = Harness(monkeypatch)
    h.gotrue.signup_identities = None
    assert h.signup().status_code == 200


def test_signup_gotrue_rate_limit_429_arabic(monkeypatch) -> None:
    h = Harness(monkeypatch)
    h.gotrue.signup_error = AuthApiError(
        "email rate limit exceeded", 429, "over_email_send_rate_limit"
    )
    res = h.signup()
    assert res.status_code == 429
    assert res.json()["detail"] == "تم تجاوز الحد المسموح من الطلبات"


def test_signup_weak_password_400_and_cooldown_released(monkeypatch) -> None:
    h = Harness(monkeypatch)
    h.gotrue.signup_error = AuthApiError("Password is too weak", 422, "weak_password")
    res = h.signup()
    assert res.status_code == 400
    assert res.json()["detail"] == "كلمة المرور ضعيفة — اختر كلمة مرور أقوى"
    # Retrying straight away with a better password is not a 429.
    h.gotrue.signup_error = None
    assert h.signup().status_code == 200


def test_signup_other_4xx_generic_400(monkeypatch) -> None:
    h = Harness(monkeypatch)
    h.gotrue.signup_error = AuthApiError("Signups not allowed", 403, "signup_disabled")
    res = h.signup()
    assert res.status_code == 400
    assert res.json()["detail"] == "فشل إنشاء الحساب"


@pytest.mark.parametrize(
    "err", [AuthRetryableError("down", 503), TimeoutError(), AuthApiError("boom", 500, None)]
)
def test_signup_gotrue_unavailable_503(monkeypatch, err) -> None:
    h = Harness(monkeypatch)
    h.gotrue.signup_error = err
    assert h.signup().status_code == 503
    assert h.gotrue.closed == 1


def test_signup_per_email_cooldown(monkeypatch) -> None:
    h = Harness(monkeypatch)
    assert h.signup().status_code == 200
    res = h.signup()
    assert res.status_code == 429
    assert res.json()["detail"] == "انتظر قليلاً قبل طلب رمز جديد"
    assert len(h.gotrue.signups) == 1
    # Own key family — never the login-OTP budget.
    assert any(k.startswith("signup:req:cd:") for k in h.redis.store)
    assert not any(k.startswith("otp:") for k in h.redis.store)
    assert all(EMAIL not in k for k in h.redis.store)


def test_signup_hourly_cap(monkeypatch) -> None:
    h = Harness(monkeypatch)
    for i in range(auth_mod.OTP_REQUEST_HOURLY_MAX):
        h.clear_cooldown()
        assert h.signup().status_code == 200, i
    h.clear_cooldown()
    assert h.signup().status_code == 429


def test_signup_fails_open_without_redis(monkeypatch) -> None:
    h = Harness(monkeypatch)
    h.app.dependency_overrides[get_redis] = lambda: None
    assert h.signup().status_code == 200
    assert h.signup().status_code == 200


def test_signup_with_session_returns_login_response(monkeypatch) -> None:
    h = Harness(monkeypatch, shared_auth=SimpleNamespace(auth=FakeGoTrue()))
    h.gotrue.signup_returns_session = True
    res = h.signup()
    assert res.status_code == 200
    login = h.client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert login.status_code == 200
    body = res.json()
    assert body == login.json()
    assert body["user"]["has_password"] is True
    assert body["access_token"] == "access-xyz"


# ---------------------------------------------------------------------------
# /signup/verify
# ---------------------------------------------------------------------------


def test_verify_matches_login_shape(monkeypatch) -> None:
    gotrue_for_login = FakeGoTrue()
    h = Harness(monkeypatch, shared_auth=SimpleNamespace(auth=gotrue_for_login))
    res = h.verify(" 123 456 ")
    assert res.status_code == 200, res.text
    assert h.gotrue.verifies == [{"email": EMAIL, "token": "123456", "type": "signup"}]
    assert h.gotrue.closed == 1

    login = h.client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert login.status_code == 200
    v, l = res.json(), login.json()
    assert set(v) == set(l)
    assert v["user"] == l["user"]  # identical, has_password included
    assert v["user"]["has_password"] is True
    assert v["user"]["full_name_ar"] == "محمد أحمد"  # from the users row


def test_verify_bad_code_401_arabic(monkeypatch) -> None:
    h = Harness(monkeypatch)
    h.gotrue.verify_error = AuthApiError("Token has expired or is invalid", 403, "otp_expired")
    res = h.verify("000000")
    assert res.status_code == 401
    assert res.json()["detail"] == "الرمز غير صحيح أو منتهي الصلاحية"
    assert res.json()["error"]["code"] == "AUTH_INVALID"


@pytest.mark.parametrize("code", ["12345", "123456789", "12a456", ""])
def test_verify_code_format_422(monkeypatch, code) -> None:
    h = Harness(monkeypatch)
    assert h.verify(code).status_code == 422
    assert h.gotrue.verifies == []


def test_verify_accepts_8_digits(monkeypatch) -> None:
    h = Harness(monkeypatch)
    assert h.verify("12345678").status_code == 200


def test_verify_lockout_after_fail_max(monkeypatch) -> None:
    h = Harness(monkeypatch)
    h.gotrue.verify_error = AuthApiError("bad", 403, "otp_expired")
    for _ in range(auth_mod.OTP_VERIFY_FAIL_MAX):
        assert h.verify("000000").status_code == 401
    before = len(h.gotrue.verifies)
    h.gotrue.verify_error = None
    res = h.verify("123456")
    assert res.status_code == 429
    assert len(h.gotrue.verifies) == before
    assert any(k.startswith("signup:verify:fails:") for k in h.redis.store)
    assert not any(k.startswith("otp:verify:") for k in h.redis.store)


def test_verify_success_clears_fails(monkeypatch) -> None:
    h = Harness(monkeypatch)
    h.gotrue.verify_error = AuthApiError("bad", 403, "otp_expired")
    h.verify("000000")
    h.gotrue.verify_error = None
    assert h.verify().status_code == 200
    assert not any(k.startswith("signup:verify:fails:") for k in h.redis.store)


def test_verify_gotrue_unavailable_503(monkeypatch) -> None:
    h = Harness(monkeypatch)
    h.gotrue.verify_error = TimeoutError()
    assert h.verify().status_code == 503


def test_verify_writes_audit_row(monkeypatch) -> None:
    h = Harness(monkeypatch)
    assert h.verify().status_code == 200
    audits = [p for t, p in h.supabase.inserts if t == "audit_logs"]
    assert any(p["metadata"] == {"event": "auth.signup.verified"} for p in audits)


# ---------------------------------------------------------------------------
# /signup/resend
# ---------------------------------------------------------------------------


def test_resend_sends_signup_type(monkeypatch) -> None:
    h = Harness(monkeypatch)
    res = h.resend("  NEW.user@example.com")
    assert res.status_code == 200 and res.json() == {"sent": True}
    assert h.gotrue.resends == [
        {
            "type": "signup",
            "email": EMAIL,
            "options": {"email_redirect_to": "https://rayhanai.com/auth/callback"},
        }
    ]
    assert h.gotrue.closed == 1


@pytest.mark.parametrize(
    "err",
    [
        AuthApiError("User not found", 404, "user_not_found"),
        AuthApiError("Email already confirmed", 422, "email_conflict"),
        AuthApiError("rate limited", 429, "over_email_send_rate_limit"),
    ],
)
def test_resend_never_enumerates(monkeypatch, err) -> None:
    h = Harness(monkeypatch)
    h.gotrue.resend_error = err
    res = h.resend()
    assert res.status_code == 200 and res.json() == {"sent": True}


def test_resend_cooldown_shared_with_signup(monkeypatch) -> None:
    h = Harness(monkeypatch)
    assert h.signup().status_code == 200
    assert h.resend().status_code == 429
    h.clear_cooldown()
    assert h.resend().status_code == 200


def test_resend_gotrue_unavailable_503(monkeypatch) -> None:
    h = Harness(monkeypatch)
    h.gotrue.resend_error = AuthRetryableError("down", 503)
    assert h.resend().status_code == 503


# ---------------------------------------------------------------------------
# /refresh — per-request throwaway client (B8)
# ---------------------------------------------------------------------------


class _ParkingAuth:
    """Mimics gotrue's refresh_session: SAVES the new session on the client,
    then reads it back. On a shared client, concurrent calls clobber each
    other's parked session — exactly the hazard B8 removes."""

    def __init__(self, barrier: Optional[threading.Barrier] = None, error=None) -> None:
        self.parked = None
        self.barrier = barrier
        self.error = error
        self.closed = 0
        self.calls: list = []

    def refresh_session(self, refresh_token):
        self.calls.append(refresh_token)
        if self.error:
            raise self.error
        self.parked = SimpleNamespace(
            access_token=f"access-for-{refresh_token}",
            refresh_token=f"rotated-{refresh_token}",
        )
        if self.barrier is not None:
            self.barrier.wait(timeout=4)  # all N calls in flight at once
        return SimpleNamespace(session=self.parked, user=None)

    def close(self):
        self.closed += 1


def _refresh_app(monkeypatch, factory) -> FastAPI:
    monkeypatch.setattr(auth_mod, "create_isolated_anon_client", factory)
    app = FastAPI()
    app.add_exception_handler(LunaHTTPException, luna_exception_handler)
    app.include_router(auth_mod.router, prefix="/api/v1/auth")
    app.dependency_overrides[get_supabase_auth] = lambda: SimpleNamespace(
        auth=_ExplodingAuth()
    )
    return app


def test_refresh_uses_throwaway_client_and_closes_it(monkeypatch) -> None:
    created: list[_ParkingAuth] = []

    def factory():
        a = _ParkingAuth()
        created.append(a)
        return SimpleNamespace(auth=a)

    client = TestClient(_refresh_app(monkeypatch, factory))
    res = client.post("/api/v1/auth/refresh", json={"refresh_token": "rt-1"})
    assert res.status_code == 200
    assert res.json() == {"access_token": "access-for-rt-1", "refresh_token": "rotated-rt-1"}
    assert len(created) == 1 and created[0].closed == 1


def test_refresh_concurrent_requests_never_cross_sessions(monkeypatch) -> None:
    n = 5
    barrier = threading.Barrier(n)
    created: list[_ParkingAuth] = []
    lock = threading.Lock()

    def factory():
        a = _ParkingAuth(barrier=barrier)
        with lock:
            created.append(a)
        return SimpleNamespace(auth=a)

    app = _refresh_app(monkeypatch, factory)

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await asyncio.gather(
                *[
                    c.post("/api/v1/auth/refresh", json={"refresh_token": f"rt-{i}"})
                    for i in range(n)
                ]
            )

    responses = asyncio.run(run())
    for i, res in enumerate(responses):
        assert res.status_code == 200, res.text
        assert res.json() == {
            "access_token": f"access-for-rt-{i}",
            "refresh_token": f"rotated-rt-{i}",
        }
    assert len(created) == n  # one client per request
    assert all(a.closed == 1 and len(a.calls) == 1 for a in created)


def test_shared_client_would_cross_sessions() -> None:
    """Control: the same fake on ONE shared client does mix sessions up, so the
    concurrency test above is actually able to catch a regression."""
    n = 5
    shared = _ParkingAuth(barrier=threading.Barrier(n))
    results: list = [None] * n

    def worker(i):
        results[i] = shared.refresh_session(f"rt-{i}").session.access_token

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(results)) == 1  # everyone got the last parked session


@pytest.mark.parametrize(
    "err,status,code",
    [
        (AuthApiError("Invalid Refresh Token", 400, "refresh_token_not_found"), 401, "AUTH_EXPIRED"),
        (AuthRetryableError("down", 503), 503, "SERVICE_UNAVAILABLE"),
        (TimeoutError(), 503, "SERVICE_UNAVAILABLE"),
    ],
)
def test_refresh_error_mapping_unchanged(monkeypatch, err, status, code) -> None:
    created: list[_ParkingAuth] = []

    def factory():
        a = _ParkingAuth(error=err)
        created.append(a)
        return SimpleNamespace(auth=a)

    client = TestClient(_refresh_app(monkeypatch, factory))
    res = client.post("/api/v1/auth/refresh", json={"refresh_token": "rt"})
    assert res.status_code == status
    assert res.json()["error"]["code"] == code
    assert created[0].closed == 1  # closed on the error path too
