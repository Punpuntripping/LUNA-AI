"""Web push «إجابتك جاهزة» — routes + sender (pwa_step1.md §1D, migration 165).

No live DB and no real push service: the Supabase client is an in-memory fake
of the ``push_subscriptions`` + ``users`` tables, and ``pywebpush`` is replaced
in ``sys.modules`` by a scripted fake that records every call.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types
from types import SimpleNamespace
from typing import Any, Optional

import pytest
from pydantic import ValidationError

from backend.app.api import push as push_api
from backend.app.errors import LunaHTTPException
from backend.app.services import push_service
from shared.auth.jwt import AuthUser

USER_A = "aaaaaaaa-0000-0000-0000-000000000001"
USER_B = "bbbbbbbb-0000-0000-0000-000000000002"
AUTH_A = "11111111-1111-1111-1111-111111111111"
AUTH_B = "22222222-2222-2222-2222-222222222222"
CONV = "cccccccc-0000-0000-0000-000000000003"


# ---------------------------------------------------------------------------
# Fake Supabase (just enough of the PostgREST builder surface)
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, data: Any) -> None:
        self.data = data


class _Query:
    def __init__(self, db: "FakeSupabase", table: str) -> None:
        self.db = db
        self.table = table
        self.op: Optional[str] = None
        self.payload: Any = None
        self.on_conflict: Optional[str] = None
        self.filters: list[tuple[str, Any]] = []
        self.single = False

    def select(self, *_cols: str) -> "_Query":
        self.op = "select"
        return self

    def upsert(self, payload: dict, on_conflict: str = "") -> "_Query":
        self.op, self.payload, self.on_conflict = "upsert", payload, on_conflict
        return self

    def update(self, payload: dict) -> "_Query":
        self.op, self.payload = "update", payload
        return self

    def delete(self) -> "_Query":
        self.op = "delete"
        return self

    def eq(self, col: str, val: Any) -> "_Query":
        self.filters.append((col, val))
        return self

    def maybe_single(self) -> "_Query":
        self.single = True
        return self

    def _match(self, row: dict) -> bool:
        return all(row.get(c) == v for c, v in self.filters)

    def execute(self) -> Optional[_Result]:
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "select":
            hits = [dict(r) for r in rows if self._match(r)]
            if self.single:
                return _Result(hits[0]) if hits else None
            return _Result(hits)
        if self.op == "upsert":
            key = self.on_conflict
            for r in rows:
                if r.get(key) == self.payload[key]:
                    r.update(self.payload)
                    return _Result([dict(r)])
            row = {"id": f"sub-{len(rows) + 1}-{self.payload['endpoint'][-4:]}",
                   "last_success_at": None, **self.payload}
            rows.append(row)
            return _Result([dict(row)])
        if self.op == "update":
            hits = [r for r in rows if self._match(r)]
            for r in hits:
                r.update(self.payload)
            return _Result([dict(r) for r in hits])
        if self.op == "delete":
            hits = [r for r in rows if self._match(r)]
            self.db.tables[self.table] = [r for r in rows if not self._match(r)]
            return _Result([dict(r) for r in hits])
        raise AssertionError(f"unexpected op {self.op}")


class FakeSupabase:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {
            "users": [
                {"user_id": USER_A, "auth_id": AUTH_A, "deletion_requested_at": None},
                {"user_id": USER_B, "auth_id": AUTH_B, "deletion_requested_at": None},
            ],
            "push_subscriptions": [],
        }

    def table(self, name: str) -> _Query:
        return _Query(self, name)

    @property
    def subs(self) -> list[dict]:
        return self.tables["push_subscriptions"]


def _user(auth_id: str) -> AuthUser:
    return AuthUser(auth_id=auth_id, email="x@example.com", role="authenticated")


def _sub(endpoint: str, user_id: str = USER_A, failure_count: int = 0) -> dict:
    return {
        "id": f"id-{endpoint[-6:]}",
        "user_id": user_id,
        "endpoint": endpoint,
        "p256dh": "BPkey",
        "auth": "authsecret",
        "user_agent": None,
        "last_success_at": None,
        "failure_count": failure_count,
    }


def _req(ua: str = "Mozilla/5.0 (iPhone)") -> Any:
    return SimpleNamespace(headers={"user-agent": ua})


# ---------------------------------------------------------------------------
# Fixtures: settings + fake pywebpush
# ---------------------------------------------------------------------------


@pytest.fixture
def vapid_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        push_service, "get_settings",
        lambda: SimpleNamespace(
            VAPID_PUBLIC_KEY="BPUBLICKEY", VAPID_PRIVATE_KEY="privkey",
            VAPID_SUBJECT="mailto:support@rayhanai.com",
        ),
    )


@pytest.fixture
def vapid_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        push_service, "get_settings",
        lambda: SimpleNamespace(VAPID_PUBLIC_KEY=None, VAPID_PRIVATE_KEY=None, VAPID_SUBJECT=None),
    )


class _FakeWebPushException(Exception):
    def __init__(self, msg: str, response: Any = None) -> None:
        super().__init__(msg)
        self.response = response


@pytest.fixture
def fake_webpush(monkeypatch: pytest.MonkeyPatch):
    """Install a fake ``pywebpush`` module. ``state.status_by_endpoint`` scripts
    per-endpoint failures (status code int, or "boom" for a non-HTTP error)."""
    state = SimpleNamespace(calls=[], status_by_endpoint={})

    def webpush(*, subscription_info, data, vapid_private_key, vapid_claims, **kw):
        state.calls.append({
            "subscription_info": subscription_info, "data": data,
            "vapid_private_key": vapid_private_key, "vapid_claims": dict(vapid_claims), **kw,
        })
        status = state.status_by_endpoint.get(subscription_info["endpoint"])
        if status == "boom":
            raise ConnectionError("network down")
        if status is not None:
            raise _FakeWebPushException(
                f"Push failed: {status}", response=SimpleNamespace(status_code=status),
            )
        return SimpleNamespace(status_code=201)

    mod = types.ModuleType("pywebpush")
    mod.webpush = webpush  # type: ignore[attr-defined]
    mod.WebPushException = _FakeWebPushException  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pywebpush", mod)
    return state


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def test_vapid_public_key_served_when_configured(vapid_on) -> None:
    out = _run(push_api.get_vapid_public_key(current_user=_user(AUTH_A)))
    assert out == {"public_key": "BPUBLICKEY"}


def test_vapid_public_key_503_arabic_when_unconfigured(vapid_off) -> None:
    with pytest.raises(LunaHTTPException) as ei:
        _run(push_api.get_vapid_public_key(current_user=_user(AUTH_A)))
    assert ei.value.status_code == 503
    assert ei.value.detail == push_api.MSG_PUSH_UNCONFIGURED
    assert any("؀" <= ch <= "ۿ" for ch in ei.value.detail)


def test_subscribe_inserts_then_upserts_on_endpoint() -> None:
    db = FakeSupabase()
    ep = "https://fcm.googleapis.com/fcm/send/abc123"
    body = push_api.PushSubscribeRequest(endpoint=ep, keys={"p256dh": "k1", "auth": "a1"})
    resp = _run(push_api.subscribe(request=_req(), body=body,
                                   current_user=_user(AUTH_A), supabase=db))
    assert resp.status_code == 204
    assert len(db.subs) == 1
    row = db.subs[0]
    assert (row["user_id"], row["p256dh"], row["auth"]) == (USER_A, "k1", "a1")
    assert row["user_agent"] == "Mozilla/5.0 (iPhone)"

    # Same endpoint again (key rotation, and a different account on the same
    # device): ONE row, refreshed keys, re-owned, failure counter reset.
    db.subs[0]["failure_count"] = 3
    body2 = push_api.PushSubscribeRequest(endpoint=ep, keys={"p256dh": "k2", "auth": "a2"})
    _run(push_api.subscribe(request=_req(), body=body2,
                            current_user=_user(AUTH_B), supabase=db))
    assert len(db.subs) == 1
    assert (db.subs[0]["user_id"], db.subs[0]["p256dh"], db.subs[0]["failure_count"]) == (USER_B, "k2", 0)


def test_subscribe_rejects_non_https_endpoint() -> None:
    with pytest.raises(ValidationError):
        push_api.PushSubscribeRequest(
            endpoint="http://169.254.169.254/latest", keys={"p256dh": "k", "auth": "a"},
        )


def test_unsubscribe_deletes_only_own_rows() -> None:
    db = FakeSupabase()
    mine = "https://web.push.apple.com/mine-0001"
    theirs = "https://web.push.apple.com/theirs-02"
    db.subs.extend([_sub(mine, USER_A), _sub(theirs, USER_B)])

    # A tries to delete B's endpoint → untouched, still 204 (no disclosure).
    resp = _run(push_api.unsubscribe(body=push_api.PushUnsubscribeRequest(endpoint=theirs),
                                     current_user=_user(AUTH_A), supabase=db))
    assert resp.status_code == 204
    assert {r["endpoint"] for r in db.subs} == {mine, theirs}

    _run(push_api.unsubscribe(body=push_api.PushUnsubscribeRequest(endpoint=mine),
                              current_user=_user(AUTH_A), supabase=db))
    assert [r["endpoint"] for r in db.subs] == [theirs]


# ---------------------------------------------------------------------------
# Sender
# ---------------------------------------------------------------------------


def test_success_sends_content_free_payload_and_stamps_row(vapid_on, fake_webpush) -> None:
    db = FakeSupabase()
    db.subs.extend([_sub("https://fcm.googleapis.com/fcm/send/dev-01", failure_count=2),
                    _sub("https://web.push.apple.com/dev-02"),
                    _sub("https://fcm.googleapis.com/fcm/send/other", USER_B)])

    _run(push_service.notify_turn_ready(
        USER_A, CONV, title="دعوى إخلاء ضد شركة الرياض للتطوير", supabase=db,
    ))

    assert len(fake_webpush.calls) == 2  # only USER_A's devices
    for call in fake_webpush.calls:
        payload = json.loads(call["data"])
        assert payload == {
            "title": "ريحان",
            "body": "إجابتك جاهزة",
            "url": f"/chat/{CONV}",
            "tag": CONV,
        }
        # the title passed in never leaks without the explicit opt-in flag
        assert "إخلاء" not in call["data"]
        assert call["vapid_private_key"] == "privkey"
        assert call["vapid_claims"] == {"sub": "mailto:support@rayhanai.com"}
    a_rows = [r for r in db.subs if r["user_id"] == USER_A]
    assert all(r["last_success_at"] and r["failure_count"] == 0 for r in a_rows)


def test_title_only_with_explicit_opt_in_and_truncated() -> None:
    p = push_service.build_turn_ready_payload(CONV, title="ع" * 100, include_title=True)
    assert p["body"].startswith("إجابتك جاهزة — ")
    assert len(p["body"]) <= len("إجابتك جاهزة — ") + 41
    assert push_service.build_turn_ready_payload(CONV, title="سر")["body"] == "إجابتك جاهزة"


@pytest.mark.parametrize("status", [404, 410])
def test_gone_subscription_is_pruned(vapid_on, fake_webpush, status: int) -> None:
    db = FakeSupabase()
    gone = "https://fcm.googleapis.com/fcm/send/gone-1"
    live = "https://fcm.googleapis.com/fcm/send/live-1"
    db.subs.extend([_sub(gone), _sub(live)])
    fake_webpush.status_by_endpoint[gone] = status

    _run(push_service.notify_turn_ready(USER_A, CONV, supabase=db))

    assert [r["endpoint"] for r in db.subs] == [live]


def test_other_errors_increment_failure_count(vapid_on, fake_webpush) -> None:
    db = FakeSupabase()
    ep = "https://fcm.googleapis.com/fcm/send/flaky1"
    db.subs.append(_sub(ep, failure_count=1))
    fake_webpush.status_by_endpoint[ep] = 500

    _run(push_service.notify_turn_ready(USER_A, CONV, supabase=db))
    assert db.subs[0]["failure_count"] == 2

    fake_webpush.status_by_endpoint[ep] = "boom"  # non-HTTP failure counts too
    _run(push_service.notify_turn_ready(USER_A, CONV, supabase=db))
    assert db.subs[0]["failure_count"] == 3


def test_fifth_failure_deletes_subscription(vapid_on, fake_webpush) -> None:
    db = FakeSupabase()
    ep = "https://fcm.googleapis.com/fcm/send/dying1"
    db.subs.append(_sub(ep, failure_count=4))
    fake_webpush.status_by_endpoint[ep] = 429

    _run(push_service.notify_turn_ready(USER_A, CONV, supabase=db))
    assert db.subs == []


def test_unconfigured_is_a_noop(vapid_off, fake_webpush) -> None:
    db = FakeSupabase()
    db.subs.append(_sub("https://fcm.googleapis.com/fcm/send/dev-01"))

    _run(push_service.notify_turn_ready(USER_A, CONV, supabase=db))
    assert fake_webpush.calls == []

    async def _schedule() -> None:
        push_service.schedule_turn_ready(USER_A, CONV, supabase=db)
        assert push_service._background_tasks == set()

    _run(_schedule())


def test_never_raises_even_if_db_breaks(vapid_on, fake_webpush) -> None:
    class Broken:
        def table(self, _name: str):
            raise RuntimeError("db down")

    _run(push_service.notify_turn_ready(USER_A, CONV, supabase=Broken()))  # no raise


def test_schedule_is_fire_and_forget(vapid_on, fake_webpush) -> None:
    db = FakeSupabase()
    db.subs.append(_sub("https://fcm.googleapis.com/fcm/send/dev-01"))

    async def _go() -> None:
        push_service.schedule_turn_ready(USER_A, CONV, supabase=db)
        assert len(push_service._background_tasks) == 1  # strong ref held
        await asyncio.gather(*list(push_service._background_tasks))

    _run(_go())
    assert len(fake_webpush.calls) == 1
    assert push_service._background_tasks == set()
