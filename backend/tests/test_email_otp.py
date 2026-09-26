"""Email OTP login — «الدخول برمز عبر البريد» (.claude/plans/email_otp_login.md).

    POST /api/v1/auth/otp/request   {email}        -> 200 {"sent": true} always
    POST /api/v1/auth/otp/verify    {email, code}  -> 200 LoginResponse (== /login)

No live GoTrue, DB or Redis: GoTrue is a recording fake swapped in for
``create_isolated_anon_client``, Supabase is a tiny scripted fake, Redis is an
in-memory async fake with just the commands the routes use.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from supabase_auth.errors import AuthApiError, AuthRetryableError

from backend.app.api import auth as auth_mod
from backend.app.deps import get_redis, get_supabase, get_supabase_auth
from backend.app.errors import LunaHTTPException, luna_exception_handler
from backend.app.services import analytics_service
from shared.config import Settings

ALLOWED = "dev@rayhanai.com"
OUTSIDER = "stranger@example.com"
AUTH_ID = "11111111-1111-1111-1111-111111111111"
USER_ID = "22222222-2222-2222-2222-222222222222"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeRedis:
    """Async in-memory subset: set(nx, ex), get, incr, expire, delete."""

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    async def get(self, key):
        v = self.store.get(key)
        return None if v is None else str(v)

    async def incr(self, key):
        self.store[key] = int(self.store.get(key, 0)) + 1
        return self.store[key]

    async def expire(self, key, seconds):
        self.ttls[key] = seconds
        return True

    async def delete(self, key):
        self.ttls.pop(key, None)
        return 1 if self.store.pop(key, None) is not None else 0


class _Query:
    def __init__(self, db: "FakeSupabase", table: str) -> None:
        self.db, self.table, self.op, self.payload = db, table, "select", None

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def maybe_single(self):
        self.op = "maybe_single"
        return self

    def insert(self, payload, **_k):
        self.op, self.payload = "insert", payload
        return self

    def execute(self):
        if self.op == "insert":
            self.db.inserts.append((self.table, self.payload))
            return SimpleNamespace(data=[self.payload])
        row = self.db.users_row
        if self.op == "maybe_single":
            return SimpleNamespace(data=row)
        return SimpleNamespace(data=[row] if row else [])


class FakeSupabase:
    def __init__(self, *, has_password: Any = False) -> None:
        self.users_row: Optional[dict] = {
            "user_id": USER_ID,
            "deletion_requested_at": None,
            "profession_group": "lawyer",
            "profession_label": None,
            "full_name_ar": "محمد أحمد",
            "preferred_name": None,
        }
        self.has_password = has_password
        self.inserts: list = []
        self.rpc_calls: list = []

    def table(self, name):
        return _Query(self, name)

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        value = self.has_password

        class _R:
            def execute(_self):
                if isinstance(value, Exception):
                    raise value
                return SimpleNamespace(data=value)

        return _R()


def _session_and_user():
    session = SimpleNamespace(
        access_token="access-xyz", refresh_token="refresh-xyz", expires_at=1_900_000_000
    )
    user = SimpleNamespace(
        id=AUTH_ID,
        email=ALLOWED,
        user_metadata={},
        created_at="2026-01-01T00:00:00+00:00",
    )
    return session, user


class FakeGoTrueAuth:
    def __init__(self) -> None:
        self.otp_requests: list = []
        self.verifies: list = []
        self.otp_error: Optional[Exception] = None
        self.verify_error: Optional[Exception] = None
        self.closed = 0

    def sign_in_with_otp(self, credentials):
        self.otp_requests.append(credentials)
        if self.otp_error:
            raise self.otp_error
        return SimpleNamespace(user=None, session=None)

    def verify_otp(self, params):
        self.verifies.append(params)
        if self.verify_error:
            raise self.verify_error
        session, user = _session_and_user()
        return SimpleNamespace(session=session, user=user)

    def sign_in_with_password(self, credentials):
        session, user = _session_and_user()
        return SimpleNamespace(session=session, user=user)

    def close(self):
        self.closed += 1


class Harness:
    def __init__(self, monkeypatch, *, allow: str = ALLOWED, has_password: Any = False):
        self.gotrue = FakeGoTrueAuth()
        self.supabase = FakeSupabase(has_password=has_password)
        self.redis = FakeRedis()
        self.clients_created = 0

        def _make_client():
            self.clients_created += 1
            return SimpleNamespace(auth=self.gotrue)

        monkeypatch.setattr(auth_mod, "create_isolated_anon_client", _make_client)
        settings = Settings.model_construct(EMAIL_OTP_ALLOWED_EMAILS=allow)
        monkeypatch.setattr(auth_mod, "get_settings", lambda: settings)

        app = FastAPI()
        app.add_exception_handler(LunaHTTPException, luna_exception_handler)
        app.include_router(auth_mod.router, prefix="/api/v1/auth")
        app.dependency_overrides[get_supabase] = lambda: self.supabase
        # /login's shared anon client — the OTP routes must never touch it.
        app.dependency_overrides[get_supabase_auth] = lambda: SimpleNamespace(auth=self.gotrue)
        app.dependency_overrides[get_redis] = lambda: self.redis
        self.client = TestClient(app)

    def request(self, email: str):
        return self.client.post("/api/v1/auth/otp/request", json={"email": email})

    def verify(self, email: str, code: str = "123456"):
        return self.client.post(
            "/api/v1/auth/otp/verify", json={"email": email, "code": code}
        )


# ---------------------------------------------------------------------------
# Config + taxonomy
# ---------------------------------------------------------------------------


def test_allowlist_parses_lowercase_and_skips_blanks() -> None:
    s = Settings.model_construct(EMAIL_OTP_ALLOWED_EMAILS=" Dev@RayhanAI.com, ,b@x.io,")
    assert s.email_otp_allowlist == frozenset({"dev@rayhanai.com", "b@x.io"})


@pytest.mark.parametrize("raw", [None, "", " , "])
def test_allowlist_empty_means_off(raw) -> None:
    assert Settings.model_construct(EMAIL_OTP_ALLOWED_EMAILS=raw).email_otp_allowlist == frozenset()


def test_otp_events_are_storable() -> None:
    assert {"otp_requested", "otp_verified", "otp_failed"} <= analytics_service.EVENT_NAMES


def test_routes_registered_on_auth_router() -> None:
    from backend.app.main import create_app

    paths = {getattr(r, "path", "") for r in create_app().routes}
    assert "/api/v1/auth/otp/request" in paths
    assert "/api/v1/auth/otp/verify" in paths
    assert "/api/v1/auth/login" in paths


# ---------------------------------------------------------------------------
# /otp/request
# ---------------------------------------------------------------------------


def test_request_allowlisted_sends_without_signup(monkeypatch) -> None:
    h = Harness(monkeypatch)
    res = h.request("  DEV@RayhanAI.com ")
    assert res.status_code == 200
    assert res.json() == {"sent": True}
    assert h.gotrue.otp_requests == [
        {"email": ALLOWED, "options": {"should_create_user": False}}
    ]
    assert h.gotrue.closed == 1  # throwaway client closed


def test_request_not_allowlisted_is_identical_and_never_calls_gotrue(monkeypatch) -> None:
    h = Harness(monkeypatch)
    allowed = h.request(ALLOWED)
    outsider = h.request(OUTSIDER)
    assert outsider.status_code == allowed.status_code == 200
    assert outsider.json() == allowed.json() == {"sent": True}
    assert [c["email"] for c in h.gotrue.otp_requests] == [ALLOWED]


def test_request_feature_off_when_allowlist_unset(monkeypatch) -> None:
    h = Harness(monkeypatch, allow="")
    res = h.request(ALLOWED)
    assert res.status_code == 200 and res.json() == {"sent": True}
    assert h.gotrue.otp_requests == []
    assert h.clients_created == 0


def test_request_unknown_user_gotrue_error_still_200(monkeypatch) -> None:
    h = Harness(monkeypatch)
    h.gotrue.otp_error = AuthApiError("Signups not allowed for otp", 422, "otp_disabled")
    res = h.request(ALLOWED)
    assert res.status_code == 200
    assert res.json() == {"sent": True}


def test_request_gotrue_unavailable_is_503(monkeypatch) -> None:
    h = Harness(monkeypatch)
    h.gotrue.otp_error = AuthRetryableError("upstream down", 503)
    assert h.request(ALLOWED).status_code == 503


def test_request_cooldown_429_arabic(monkeypatch) -> None:
    h = Harness(monkeypatch)
    assert h.request(ALLOWED).status_code == 200
    res = h.request(ALLOWED)
    assert res.status_code == 429
    assert res.json()["detail"] == "انتظر قليلاً قبل طلب رمز جديد"
    assert len(h.gotrue.otp_requests) == 1


def test_request_rate_limit_applies_to_outsiders_too(monkeypatch) -> None:
    """Otherwise only listed emails could ever 429 — a membership oracle."""
    h = Harness(monkeypatch)
    assert h.request(OUTSIDER).status_code == 200
    assert h.request(OUTSIDER).status_code == 429


def test_request_hourly_cap(monkeypatch) -> None:
    h = Harness(monkeypatch)
    for i in range(auth_mod.OTP_REQUEST_HOURLY_MAX):
        # Simulate the 60s cooldown expiring between requests.
        h.redis.store = {k: v for k, v in h.redis.store.items() if ":cd:" not in k}
        assert h.request(ALLOWED).status_code == 200, i
    h.redis.store = {k: v for k, v in h.redis.store.items() if ":cd:" not in k}
    res = h.request(ALLOWED)
    assert res.status_code == 429
    assert res.json()["detail"] == "انتظر قليلاً قبل طلب رمز جديد"


def test_request_redis_keys_never_hold_the_raw_email(monkeypatch) -> None:
    h = Harness(monkeypatch)
    h.request(ALLOWED)
    assert h.redis.store and all(ALLOWED not in k for k in h.redis.store)


def test_request_fails_open_without_redis(monkeypatch) -> None:
    h = Harness(monkeypatch)
    h.client.app.dependency_overrides[get_redis] = lambda: None
    assert h.request(ALLOWED).status_code == 200
    assert h.request(ALLOWED).status_code == 200


def test_request_malformed_email_422(monkeypatch) -> None:
    h = Harness(monkeypatch)
    assert h.request("not-an-email").status_code == 422


# ---------------------------------------------------------------------------
# /otp/verify
# ---------------------------------------------------------------------------


def test_verify_success_matches_login_shape(monkeypatch) -> None:
    h = Harness(monkeypatch, has_password=False)
    otp = h.verify(ALLOWED.upper(), "123456")
    assert otp.status_code == 200
    assert h.gotrue.verifies == [{"email": ALLOWED, "token": "123456", "type": "email"}]

    login = h.client.post(
        "/api/v1/auth/login", json={"email": ALLOWED, "password": "pw"}
    )
    assert login.status_code == 200

    o, l = otp.json(), login.json()
    assert set(o) == set(l)
    assert set(o["user"]) == set(l["user"])
    assert o["access_token"] == "access-xyz" and o["refresh_token"] == "refresh-xyz"
    assert o["user"]["full_name_ar"] == "محمد أحمد"
    # Everything but has_password is identical between the two grants.
    assert {k: v for k, v in o["user"].items() if k != "has_password"} == {
        k: v for k, v in l["user"].items() if k != "has_password"
    }


@pytest.mark.parametrize("rpc_value", [True, False])
def test_verify_resolves_has_password_via_rpc(monkeypatch, rpc_value) -> None:
    h = Harness(monkeypatch, has_password=rpc_value)
    res = h.verify(ALLOWED)
    assert res.status_code == 200
    assert res.json()["user"]["has_password"] is rpc_value
    assert ("user_has_password", {"p_auth_id": AUTH_ID}) in h.supabase.rpc_calls


def test_verify_has_password_degrades_to_false(monkeypatch) -> None:
    h = Harness(monkeypatch, has_password=RuntimeError("rpc down"))
    res = h.verify(ALLOWED)
    assert res.status_code == 200
    assert res.json()["user"]["has_password"] is False


def test_login_still_reports_has_password_true(monkeypatch) -> None:
    h = Harness(monkeypatch, has_password=False)
    res = h.client.post("/api/v1/auth/login", json={"email": ALLOWED, "password": "pw"})
    assert res.status_code == 200
    assert res.json()["user"]["has_password"] is True
    assert h.supabase.rpc_calls == []  # password grant needs no RPC


def test_verify_bad_code_401_arabic(monkeypatch) -> None:
    h = Harness(monkeypatch)
    h.gotrue.verify_error = AuthApiError("Token has expired or is invalid", 403, "otp_expired")
    res = h.verify(ALLOWED, "000000")
    assert res.status_code == 401
    assert res.json()["detail"] == "الرمز غير صحيح أو منتهي الصلاحية"


def test_verify_not_allowlisted_401_without_gotrue(monkeypatch) -> None:
    h = Harness(monkeypatch)
    res = h.verify(OUTSIDER)
    assert res.status_code == 401
    assert res.json()["detail"] == "الرمز غير صحيح أو منتهي الصلاحية"
    assert h.gotrue.verifies == []


def test_verify_five_failures_then_429(monkeypatch) -> None:
    h = Harness(monkeypatch)
    h.gotrue.verify_error = AuthApiError("Token has expired or is invalid", 403, "otp_expired")
    for _ in range(auth_mod.OTP_VERIFY_FAIL_MAX):
        assert h.verify(ALLOWED, "000000").status_code == 401
    calls_before = len(h.gotrue.verifies)
    res = h.verify(ALLOWED, "000000")
    assert res.status_code == 429
    assert len(h.gotrue.verifies) == calls_before  # locked before GoTrue
    # Even the right code is refused while locked.
    h.gotrue.verify_error = None
    assert h.verify(ALLOWED, "123456").status_code == 429


def test_verify_success_resets_fail_counter(monkeypatch) -> None:
    h = Harness(monkeypatch)
    h.gotrue.verify_error = AuthApiError("bad", 403, "otp_expired")
    for _ in range(auth_mod.OTP_VERIFY_FAIL_MAX - 1):
        h.verify(ALLOWED, "000000")
    h.gotrue.verify_error = None
    assert h.verify(ALLOWED).status_code == 200
    assert not any(k.startswith("otp:verify:fails:") for k in h.redis.store)


def test_verify_accepts_longer_codes_and_rejects_junk(monkeypatch) -> None:
    h = Harness(monkeypatch)
    assert h.verify(ALLOWED, "12345678").status_code == 200
    assert h.verify(ALLOWED, "12345").status_code == 422
    assert h.verify(ALLOWED, "12345a").status_code == 422
    assert h.verify(ALLOWED, "12345678901").status_code == 422


def test_verify_gotrue_unavailable_is_503(monkeypatch) -> None:
    h = Harness(monkeypatch)
    h.gotrue.verify_error = TimeoutError()
    assert h.verify(ALLOWED).status_code == 503


def test_verify_writes_audit_row(monkeypatch) -> None:
    h = Harness(monkeypatch)
    assert h.verify(ALLOWED).status_code == 200
    audits = [p for t, p in h.supabase.inserts if t == "audit_logs"]
    assert any(p["metadata"] == {"event": "auth.otp.verified"} for p in audits)
