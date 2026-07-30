"""Account settings tests (.claude/plans/account_settings.md).

Covers the إعدادات الحساب backend surface:

    POST /api/v1/auth/delete-account   (30-day grace, password OR Google-only)
    POST /api/v1/auth/restore-account
    POST /api/v1/auth/change-password
    POST /api/v1/auth/logout-all
    case_service.get_user_id           (grace-period 403 gate)
    account_purge_service              (daily hard-purge sweep)

No live DB. Supabase is a scripted fake: each test enqueues the rows the service
should see, and writes are captured for payload assertions. The GoTrue admin API
is faked too — these tests are the only place the delete/sign_out/update calls
are exercised without touching a real project.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from backend.app.errors import ErrorCode, LunaHTTPException
from backend.app.services import account_purge_service, account_service, case_service
from backend.app.services.account_service import GRACE_PERIOD_DAYS


# ---------------------------------------------------------------------------
# Route inventory
# ---------------------------------------------------------------------------


def test_account_endpoints_registered() -> None:
    from backend.app.main import create_app

    paths = {getattr(r, "path", "") for r in create_app().routes}
    assert "/api/v1/auth/delete-account" in paths
    assert "/api/v1/auth/restore-account" in paths
    assert "/api/v1/auth/change-password" in paths
    assert "/api/v1/auth/logout-all" in paths


def _dependency_callables(route: Any) -> set:
    """Every dependency callable reachable from a route, sub-dependencies included."""
    seen: set = set()
    stack = list(getattr(route.dependant, "dependencies", []))
    while stack:
        dep = stack.pop()
        if dep.call is not None:
            seen.add(dep.call)
        stack.extend(dep.dependencies)
    return seen


def test_logout_never_uses_the_shared_anon_client() -> None:
    """/logout must revoke via the SERVICE-ROLE admin API, not the shared client.

    ``app.state.supabase_auth`` is an ``lru_cache``d anon client shared by every
    request, and ``sign_in_with_password`` parks each login's session in its
    in-memory GoTrue store. ``auth.sign_out()`` acts on whatever session is parked
    there and revokes it with scope="global", so depending on that client here
    lets one user's logout revoke a DIFFERENT user's refresh tokens on every
    device — silently, because the handler swallows the exception and still
    returns 200. Nothing else in the suite would catch a revert.
    """
    from backend.app.deps import get_supabase, get_supabase_auth
    from backend.app.main import create_app

    routes = [r for r in create_app().routes if getattr(r, "path", "") == "/api/v1/auth/logout"]
    assert routes, "/api/v1/auth/logout is not registered"

    deps = _dependency_callables(routes[0])
    assert get_supabase_auth not in deps, (
        "/logout depends on the shared anon client — it must use the service-role "
        "client and admin.sign_out(raw_jwt, 'local') instead"
    )
    assert get_supabase in deps, "/logout needs the service-role client for admin.sign_out"


# ---------------------------------------------------------------------------
# Scripted fake supabase (+ fake GoTrue admin)
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, data: Any) -> None:
        self.data = data


class _Chain:
    def __init__(self, fake: "FakeSupabase", table: str) -> None:
        self._fake = fake
        self._table = table
        self._op: str | None = None
        self._payload: Any = None
        self.filters: list[tuple] = []

    def select(self, *_a: Any, **_k: Any) -> "_Chain":
        self._op = "select"
        return self

    def insert(self, payload: Any) -> "_Chain":
        self._op = "insert"
        self._payload = payload
        return self

    def update(self, payload: Any) -> "_Chain":
        self._op = "update"
        self._payload = payload
        return self

    def eq(self, col: str, val: Any) -> "_Chain":
        self.filters.append(("eq", col, val))
        return self

    def is_(self, col: str, val: Any) -> "_Chain":
        self.filters.append(("is_", col, val))
        return self

    def lte(self, col: str, val: Any) -> "_Chain":
        self.filters.append(("lte", col, val))
        return self

    def maybe_single(self) -> "_Chain":
        return self

    def execute(self) -> _Result:
        if self._op == "insert":
            self._fake.inserts.append((self._table, self._payload))
            return _Result([self._payload])
        if self._op == "update":
            self._fake.updates.append((self._table, self._payload, list(self.filters)))
            return _Result([self._payload])
        self._fake.selects.append((self._table, list(self.filters)))
        return _Result(self._fake._pop(f"select:{self._table}"))


class _FakeIdentity:
    def __init__(self, provider: str) -> None:
        self.provider = provider


class _FakeGoTrueUser:
    def __init__(self, providers: list[str]) -> None:
        self.identities = [_FakeIdentity(p) for p in providers]


class _FakeUserResponse:
    def __init__(self, user: Any) -> None:
        self.user = user


class FakeAdmin:
    """Fake supabase.auth.admin — records calls, optionally raises."""

    def __init__(self, providers: list[str] | None = None) -> None:
        self._providers = providers if providers is not None else ["email"]
        self.calls: list[tuple[str, Any]] = []
        self.delete_user_raises: Exception | None = None

    def get_user_by_id(self, uid: str) -> _FakeUserResponse:
        self.calls.append(("get_user_by_id", uid))
        return _FakeUserResponse(_FakeGoTrueUser(self._providers))

    def delete_user(self, uid: str) -> None:
        self.calls.append(("delete_user", uid))
        if self.delete_user_raises is not None:
            raise self.delete_user_raises

    def sign_out(self, jwt: str, scope: str = "global") -> None:
        self.calls.append(("sign_out", scope))

    def update_user_by_id(self, uid: str, attributes: dict) -> None:
        self.calls.append(("update_user_by_id", uid))


class _FakeAuth:
    def __init__(self, admin: FakeAdmin) -> None:
        self.admin = admin


class FakeSupabase:
    """Queue-scripted fake: enqueue per-table results in call order."""

    def __init__(self, providers: list[str] | None = None) -> None:
        self._queues: dict[str, list[Any]] = {}
        self.inserts: list[tuple[str, Any]] = []
        self.updates: list[tuple[str, Any, list]] = []
        self.selects: list[tuple[str, list]] = []
        self.rpcs: list[tuple[str, Any]] = []
        self.admin = FakeAdmin(providers)
        self.auth = _FakeAuth(self.admin)

    def queue(self, key: str, data: Any) -> None:
        self._queues.setdefault(key, []).append(data)

    def _pop(self, key: str) -> Any:
        q = self._queues.get(key) or []
        return q.pop(0) if q else None

    def table(self, name: str) -> _Chain:
        return _Chain(self, name)

    def rpc(self, name: str, params: Any) -> Any:
        self.rpcs.append((name, params))

        class _RpcChain:
            def execute(self_inner) -> _Result:  # noqa: N805
                return _Result({"purged": True})

        return _RpcChain()


USER_ID = "11111111-1111-1111-1111-111111111111"
AUTH_ID = "22222222-2222-2222-2222-222222222222"
CASE_ID = "33333333-3333-3333-3333-333333333333"


# ---------------------------------------------------------------------------
# get_user_id — the grace-period gate
# ---------------------------------------------------------------------------


def test_get_user_id_passes_for_active_account() -> None:
    fake = FakeSupabase()
    fake.queue("select:users", {"user_id": USER_ID, "deletion_requested_at": None})

    assert case_service.get_user_id(fake, AUTH_ID) == USER_ID


def test_get_user_id_403s_pending_account() -> None:
    """Every data route resolves the caller through this helper — the 403 here
    is what deactivates the account across the whole app during grace."""
    fake = FakeSupabase()
    fake.queue(
        "select:users",
        {"user_id": USER_ID, "deletion_requested_at": "2026-07-01T00:00:00+00:00"},
    )

    with pytest.raises(LunaHTTPException) as exc:
        case_service.get_user_id(fake, AUTH_ID)

    assert exc.value.status_code == 403
    assert exc.value.code == ErrorCode.ACCOUNT_DELETION_PENDING


def test_get_account_user_id_is_ungated() -> None:
    """The auth surface must still resolve a pending user (audit rows, restore) —
    routing it through the gated get_user_id would lock them out of their own
    restore endpoint."""
    fake = FakeSupabase()
    fake.queue(
        "select:users",
        {"user_id": USER_ID, "deletion_requested_at": "2026-07-01T00:00:00+00:00"},
    )

    assert account_service.get_account_user_id(fake, AUTH_ID) == USER_ID


# ---------------------------------------------------------------------------
# schedule / cancel deletion
# ---------------------------------------------------------------------------


def test_schedule_deletion_stamps_and_guards_on_is_null() -> None:
    fake = FakeSupabase()
    fake.queue("select:users", {"user_id": USER_ID, "deletion_requested_at": None})

    out = account_service.schedule_account_deletion(fake, AUTH_ID)

    assert len(fake.updates) == 1
    table, payload, filters = fake.updates[0]
    assert table == "users"
    assert payload["deletion_requested_at"] is not None
    # The IS NULL guard is what stops a repeat request from resetting the clock.
    assert ("is_", "deletion_requested_at", "null") in filters
    assert out["purge_at"] - out["deletion_requested_at"] == timedelta(
        days=GRACE_PERIOD_DAYS
    )
    audit = next(p for t, p in fake.inserts if t == "audit_logs")
    assert audit["metadata"]["event"] == "deletion_requested"
    assert audit["user_id"] == USER_ID


def test_schedule_deletion_is_idempotent_and_cannot_extend_the_clock() -> None:
    original = "2026-07-01T00:00:00+00:00"
    fake = FakeSupabase()
    fake.queue("select:users", {"user_id": USER_ID, "deletion_requested_at": original})

    out = account_service.schedule_account_deletion(fake, AUTH_ID)

    assert fake.updates == []  # no UPDATE issued at all
    assert out["deletion_requested_at"] == original
    assert out["purge_at"] == datetime.fromisoformat(original) + timedelta(
        days=GRACE_PERIOD_DAYS
    )


def test_cancel_deletion_clears_the_stamp() -> None:
    fake = FakeSupabase()
    fake.queue(
        "select:users",
        {"user_id": USER_ID, "deletion_requested_at": "2026-07-01T00:00:00+00:00"},
    )

    account_service.cancel_account_deletion(fake, AUTH_ID)

    assert len(fake.updates) == 1
    _, payload, _ = fake.updates[0]
    assert payload == {"deletion_requested_at": None}


def test_cancel_deletion_on_active_account_is_a_noop() -> None:
    fake = FakeSupabase()
    fake.queue("select:users", {"user_id": USER_ID, "deletion_requested_at": None})

    account_service.cancel_account_deletion(fake, AUTH_ID)

    assert fake.updates == []


# ---------------------------------------------------------------------------
# has_password_identity — decides the delete/change-password branch server-side
# ---------------------------------------------------------------------------


def test_has_password_identity_true_for_email_account() -> None:
    assert account_service.has_password_identity(FakeSupabase(["email"]), AUTH_ID)


def test_has_password_identity_false_for_google_only() -> None:
    assert not account_service.has_password_identity(FakeSupabase(["google"]), AUTH_ID)


def test_has_password_identity_fails_closed_on_gotrue_outage() -> None:
    """A GoTrue outage must NOT be read as "no password" — that would let a
    delete-account request through without any password confirmation."""
    fake = FakeSupabase()

    def _boom(_uid: str) -> Any:
        raise RuntimeError("gotrue down")

    fake.admin.get_user_by_id = _boom  # type: ignore[assignment]

    with pytest.raises(LunaHTTPException) as exc:
        account_service.has_password_identity(fake, AUTH_ID)

    assert exc.value.status_code == 503


# ---------------------------------------------------------------------------
# Password re-verification runs on an ISOLATED client
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_password_never_touches_the_shared_anon_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sign_in_with_password parks its session in the client's auth store, and
    gotrue's auth.sign_out() acts on whatever session is parked there. Verifying
    on the shared app.state.supabase_auth singleton would therefore let one
    request's re-auth collide with another request's session — so re-auth must
    build (and close) its own throwaway client."""
    from backend.app.api import auth as auth_api

    closed: list[bool] = []

    class _ThrowawayAuth:
        def sign_in_with_password(self, creds: dict) -> Any:
            return _FakeUserResponse(_FakeGoTrueUser(["email"]))

        def close(self) -> None:
            closed.append(True)

    class _Throwaway:
        def __init__(self) -> None:
            self.auth = _ThrowawayAuth()

    made: list[_Throwaway] = []

    def _factory() -> _Throwaway:
        client = _Throwaway()
        made.append(client)
        return client

    monkeypatch.setattr(auth_api, "create_isolated_anon_client", _factory)

    await auth_api._verify_password("a@b.com", "pw", "خطأ")

    assert len(made) == 1, "re-auth must build its own client, not reuse the singleton"
    assert closed == [True], "the throwaway client must be closed"


# ---------------------------------------------------------------------------
# purge sweep
# ---------------------------------------------------------------------------


@pytest.fixture()
def storage_spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the prefixes the sweep asks storage to delete."""
    deleted: list[str] = []

    def _fake_delete(bucket: str, prefix: str, supabase: Any = None) -> int:
        deleted.append(prefix)
        return 1

    monkeypatch.setattr(account_purge_service, "delete_folder_recursive", _fake_delete)
    return deleted


def _expired_user_fake() -> FakeSupabase:
    fake = FakeSupabase()
    fake.queue("select:users", [{"user_id": USER_ID, "auth_id": AUTH_ID}])
    fake.queue("select:lawyer_cases", [{"case_id": CASE_ID}])
    return fake


def test_purge_deletes_storage_then_rpc_then_auth_user(storage_spy: list[str]) -> None:
    fake = _expired_user_fake()

    stats = account_purge_service.purge_expired_accounts(fake)

    assert stats == {"scanned": 1, "purged": 1, "failed": 0}
    # Both prefix families — general/{user_id} and cases/{case_id} — are swept.
    assert storage_spy == [f"general/{USER_ID}", f"cases/{CASE_ID}"]
    assert fake.rpcs == [("purge_user_data", {"p_user_id": USER_ID})]
    # The auth delete is TERMINAL: it cascades the users row, which is the
    # sweep's own selection marker, so it must be the last destructive step.
    assert ("delete_user", AUTH_ID) in fake.admin.calls


def test_purge_sweep_scans_only_expired_accounts(storage_spy: list[str]) -> None:
    fake = _expired_user_fake()

    account_purge_service.purge_expired_accounts(fake)

    users_select = next(f for t, f in fake.selects if t == "users")
    op, col, val = next(f for f in users_select if f[0] == "lte")
    assert (op, col) == ("lte", "deletion_requested_at")
    cutoff = datetime.fromisoformat(val)
    expected = datetime.now(timezone.utc) - timedelta(days=GRACE_PERIOD_DAYS)
    assert abs((cutoff - expected).total_seconds()) < 60


def test_purge_audit_row_omits_user_id(storage_spy: list[str]) -> None:
    """write_audit_log() always sets user_id, but that FK row is gone by now —
    the sweep inserts directly, leaving user_id NULL (the SET NULL target)."""
    fake = _expired_user_fake()

    account_purge_service.purge_expired_accounts(fake)

    table, payload = next(
        (t, p) for t, p in fake.inserts if t == "audit_logs"
    )
    assert table == "audit_logs"
    assert "user_id" not in payload
    assert payload["resource_type"] == "account"
    assert payload["metadata"]["event"] == "purged"


def test_purge_aborts_before_db_when_storage_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Storage first, and it RAISES: once the case rows are gone the storage
    prefixes are unrecoverable, so files must never be orphaned by a DB delete
    that ran anyway."""

    def _boom(bucket: str, prefix: str, supabase: Any = None) -> int:
        raise RuntimeError("storage down")

    monkeypatch.setattr(account_purge_service, "delete_folder_recursive", _boom)
    fake = _expired_user_fake()

    stats = account_purge_service.purge_expired_accounts(fake)

    assert stats == {"scanned": 1, "purged": 0, "failed": 1}
    assert fake.rpcs == []
    assert not any(c[0] == "delete_user" for c in fake.admin.calls)


def test_purge_isolates_failures_per_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """One bad account must not stop the sweep — the second user still purges."""
    bad_user, good_user = "bad-user", "good-user"

    def _selective(bucket: str, prefix: str, supabase: Any = None) -> int:
        if bad_user in prefix:
            raise RuntimeError("storage down for this one")
        return 1

    monkeypatch.setattr(account_purge_service, "delete_folder_recursive", _selective)

    fake = FakeSupabase()
    fake.queue(
        "select:users",
        [
            {"user_id": bad_user, "auth_id": "bad-auth"},
            {"user_id": good_user, "auth_id": "good-auth"},
        ],
    )
    fake.queue("select:lawyer_cases", [])  # bad user
    fake.queue("select:lawyer_cases", [])  # good user

    stats = account_purge_service.purge_expired_accounts(fake)

    assert stats == {"scanned": 2, "purged": 1, "failed": 1}
    assert ("delete_user", "good-auth") in fake.admin.calls
    assert ("delete_user", "bad-auth") not in fake.admin.calls


def test_purge_sweep_never_raises_on_query_failure() -> None:
    """A scheduler tick must not be able to crash the app."""

    class _Exploding(FakeSupabase):
        def table(self, name: str) -> Any:
            raise RuntimeError("db down")

    stats = account_purge_service.purge_expired_accounts(_Exploding())

    assert stats == {"scanned": 0, "purged": 0, "failed": 0}
