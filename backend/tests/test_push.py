"""Push «إجابتك جاهزة» — web (VAPID) and app (Expo) channels.

Web: routes + sender (pwa_step1.md §1D, migration 165).
App: routes + sender + the schedule_turn_ready split (mobile/06_phase3_push.md
§3 B, migration 185) — the second half of this file.

No live DB and no real push service: the Supabase client is an in-memory fake
of the ``push_subscriptions`` / ``app_push_tokens`` + ``users`` tables,
``pywebpush`` is replaced in ``sys.modules`` by a scripted fake that records
every call, and Expo is an ``httpx.MockTransport`` installed by replacing
``app_push_service._new_http_client``. Logfire is replaced by a recorder in the
app tests so each can assert the device token never reaches a log.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import sys
import threading
import types
from types import SimpleNamespace
from typing import Any, Optional

import httpx
import pytest
from pydantic import ValidationError

from backend.app.api import push as push_api
from backend.app.errors import LunaHTTPException
from backend.app.services import app_push_service, push_service
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
        self.in_filters: list[tuple[str, set]] = []
        self.orders: list[tuple[str, bool, Optional[bool]]] = []
        self.limit_n: Optional[int] = None
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

    def in_(self, col: str, vals: Any) -> "_Query":
        self.in_filters.append((col, set(vals)))
        return self

    def order(self, col: str, *, desc: bool = False, nullsfirst: Optional[bool] = None) -> "_Query":
        self.orders.append((col, desc, nullsfirst))
        return self

    def limit(self, n: int) -> "_Query":
        self.limit_n = n
        return self

    def maybe_single(self) -> "_Query":
        self.single = True
        return self

    def _match(self, row: dict) -> bool:
        return (all(row.get(c) == v for c, v in self.filters)
                and all(row.get(c) in vs for c, vs in self.in_filters))

    def _sorted(self, hits: list[dict]) -> list[dict]:
        # PostgREST semantics: first .order() is the primary key. Apply keys
        # last-to-first with a stable sort. Postgres default: NULLS FIRST on DESC.
        for col, desc, nullsfirst in reversed(self.orders):
            nulls = [r for r in hits if r.get(col) is None]
            vals = sorted((r for r in hits if r.get(col) is not None),
                          key=lambda r: r[col], reverse=desc)
            nf = desc if nullsfirst is None else nullsfirst
            hits = nulls + vals if nf else vals + nulls
        return hits

    def execute(self) -> Optional[_Result]:
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "select":
            hits = self._sorted([dict(r) for r in rows if self._match(r)])
            if self.limit_n is not None:
                hits = hits[: self.limit_n]
            if self.single:
                return _Result(hits[0]) if hits else None
            return _Result(hits)
        if self.op == "upsert":
            key = self.on_conflict
            for r in rows:
                if r.get(key) == self.payload[key]:
                    r.update(self.payload)
                    return _Result([dict(r)])
            self.db.seq += 1
            row = {"id": f"sub-{self.db.seq}-{str(self.payload[key])[-4:]}",
                   "created_at": f"2026-09-25T12:00:{self.db.seq:02d}+00:00",
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
        self.seq = 0
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


def _sub(
    endpoint: str,
    user_id: str = USER_A,
    failure_count: int = 0,
    *,
    created_at: str = "2026-01-01T00:00:00+00:00",
    last_success_at: Optional[str] = None,
) -> dict:
    return {
        "id": f"id-{endpoint[-6:]}",
        "user_id": user_id,
        "endpoint": endpoint,
        "p256dh": "BPkey",
        "auth": "authsecret",
        "user_agent": None,
        "created_at": created_at,
        "last_success_at": last_success_at,
        "failure_count": failure_count,
    }


def _req(ua: str = "Mozilla/5.0 (iPhone)") -> Any:
    return SimpleNamespace(headers={"user-agent": ua})


# ---------------------------------------------------------------------------
# Fixtures: settings + fake pywebpush
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _expo_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # schedule_turn_ready also consults the app (Expo) half. Pin it OFF so the
    # web tests never depend on a developer's .env (the app tests below
    # override it with expo_on / expo_off).
    monkeypatch.setattr(
        app_push_service, "get_settings",
        lambda: SimpleNamespace(EXPO_PUSH_ENABLED=False, EXPO_ACCESS_TOKEN=None),
    )


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
            "vapid_private_key": vapid_private_key, "vapid_claims": dict(vapid_claims),
            "thread": threading.current_thread().name, **kw,
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


@pytest.mark.parametrize("endpoint", [
    "https://attacker.example/slow-tarpit-endpoint",
    "https://fcm.googleapis.com.evil.example/fcm/send/x",   # suffix spoof
    "https://fcm.googleapis.com@evil.example/fcm/send/x",   # userinfo trick
    "https://evilpush.apple.com/abc123",                    # not a .push.apple.com subdomain
    "https://notify.windows.com.evil.example/w/?token=x",
    "https://fcm.googleapis.com:8443/fcm/send/abc",         # non-default port
    "https://10.0.0.5/internal-service",
    "https://redis.railway.internal/x",
    "http://fcm.googleapis.com/fcm/send/abc",               # not https
])
def test_subscribe_rejects_non_allowlisted_host(endpoint: str) -> None:
    with pytest.raises(ValidationError):
        push_api.PushSubscribeRequest(endpoint=endpoint, keys={"p256dh": "k", "auth": "a"})


@pytest.mark.parametrize("endpoint", [
    "https://fcm.googleapis.com/fcm/send/dXk1abc:APA91bH",                 # Chrome / Android
    "https://updates.push.services.mozilla.com/wpush/v2/gAAAAABk",        # Firefox
    "https://web.push.apple.com/QGuQyavXutnMH0",                           # Safari / iOS
    "https://wns2-par02p.notify.windows.com/w/?token=BQYAAAB",             # Edge
    "https://FCM.GoogleAPIs.com/fcm/send/upper-case-host",
    "https://fcm.googleapis.com:443/fcm/send/explicit-port",
])
def test_subscribe_accepts_real_push_hosts(endpoint: str) -> None:
    body = push_api.PushSubscribeRequest(endpoint=endpoint, keys={"p256dh": "k", "auth": "a"})
    assert body.endpoint == endpoint


def test_unsubscribe_stays_lenient_for_legacy_rows() -> None:
    # A row stored before the allowlist must still be deletable by its owner.
    db = FakeSupabase()
    legacy = "https://attacker.example/legacy-row"
    db.subs.append(_sub(legacy, USER_A))
    body = push_api.PushUnsubscribeRequest(endpoint=legacy)
    _run(push_api.unsubscribe(body=body, current_user=_user(AUTH_A), supabase=db))
    assert db.subs == []
    with pytest.raises(ValidationError):
        push_api.PushUnsubscribeRequest(endpoint="http://169.254.169.254/latest")


def test_subscribe_trims_to_cap_keeping_most_recently_active() -> None:
    db = FakeSupabase()
    fcm = "https://fcm.googleapis.com/fcm/send/"
    # rank key = last_success_at or created_at
    db.subs.extend([
        _sub(fcm + "old-never-ok", created_at="2026-01-01T00:00:00+00:00"),          # stalest → trimmed
        _sub(fcm + "old-but-used", created_at="2026-01-02T00:00:00+00:00",
             last_success_at="2026-09-20T00:00:00.123456+00:00"),                    # kept (recent success)
        _sub(fcm + "mid-00000003", created_at="2026-05-01T00:00:00+00:00"),
        _sub(fcm + "mid-00000004", created_at="2026-06-01T00:00:00+00:00"),
        _sub(fcm + "mid-00000005", created_at="2026-07-01T00:00:00+00:00"),
        _sub(fcm + "user-b-dev01", USER_B, created_at="2025-01-01T00:00:00+00:00"),   # other user untouched
    ])
    new_ep = fcm + "brand-new-dev"
    body = push_api.PushSubscribeRequest(endpoint=new_ep, keys={"p256dh": "k", "auth": "a"})
    _run(push_api.subscribe(request=_req(), body=body, current_user=_user(AUTH_A), supabase=db))

    a_eps = {r["endpoint"] for r in db.subs if r["user_id"] == USER_A}
    assert len(a_eps) == push_service.MAX_SUBS_PER_USER == 5
    assert fcm + "old-never-ok" not in a_eps
    assert {new_ep, fcm + "old-but-used"} <= a_eps
    assert any(r["user_id"] == USER_B for r in db.subs)


def test_resubscribing_a_stale_endpoint_keeps_it_over_the_cap() -> None:
    # 6 rows already (e.g. a subscribe race) and the user re-subscribes the
    # stalest one: it just proved it is live, so something else goes.
    db = FakeSupabase()
    fcm = "https://fcm.googleapis.com/fcm/send/"
    stale = fcm + "stalest-0001"
    db.subs.append(_sub(stale, created_at="2026-01-01T00:00:00+00:00"))
    for i in range(2, 7):
        db.subs.append(_sub(fcm + f"dev-{i:08d}", created_at=f"2026-0{i}-01T00:00:00+00:00"))

    body = push_api.PushSubscribeRequest(endpoint=stale, keys={"p256dh": "k", "auth": "a"})
    _run(push_api.subscribe(request=_req(), body=body, current_user=_user(AUTH_A), supabase=db))

    eps = {r["endpoint"] for r in db.subs}
    assert len(eps) == 5
    assert stale in eps
    assert fcm + "dev-00000002" not in eps  # the next-stalest was trimmed instead


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


def test_send_never_exceeds_cap_and_prefers_most_recently_active(vapid_on, fake_webpush) -> None:
    # 8 rows (bypassing the subscribe trim, as a race could): only the 5 most
    # recently active are contacted — last_success_at DESC NULLS LAST, then created_at.
    db = FakeSupabase()
    fcm = "https://fcm.googleapis.com/fcm/send/"
    for i in range(1, 4):
        db.subs.append(_sub(fcm + f"used-{i:07d}", created_at="2026-01-01T00:00:00+00:00",
                            last_success_at=f"2026-09-0{i}T00:00:00+00:00"))
    for i in range(1, 6):
        db.subs.append(_sub(fcm + f"never-{i:06d}", created_at=f"2026-0{i}-01T00:00:00+00:00"))

    _run(push_service.notify_turn_ready(USER_A, CONV, supabase=db))

    sent = {c["subscription_info"]["endpoint"] for c in fake_webpush.calls}
    assert len(sent) == push_service.MAX_SUBS_PER_USER
    assert sent == {fcm + "used-0000001", fcm + "used-0000002", fcm + "used-0000003",
                    fcm + "never-000005", fcm + "never-000004"}


def test_send_skips_stored_non_allowlisted_endpoints(vapid_on, fake_webpush) -> None:
    # Rows written before the allowlist existed are never contacted.
    db = FakeSupabase()
    good = "https://web.push.apple.com/good-device"
    db.subs.extend([
        _sub("https://attacker.example/tarpit-1"),
        _sub("http://169.254.169.254/latest/meta"),
        _sub(good),
    ])
    _run(push_service.notify_turn_ready(USER_A, CONV, supabase=db))
    assert [c["subscription_info"]["endpoint"] for c in fake_webpush.calls] == [good]


def test_sends_run_on_dedicated_push_pool_not_default_executor(vapid_on, fake_webpush) -> None:
    db = FakeSupabase()
    db.subs.extend([_sub("https://fcm.googleapis.com/fcm/send/dev-01"),
                    _sub("https://web.push.apple.com/dev-02")])
    _run(push_service.notify_turn_ready(USER_A, CONV, supabase=db))
    assert len(fake_webpush.calls) == 2
    assert all(c["thread"].startswith("luna-push") for c in fake_webpush.calls)
    assert all(c["timeout"] == 10 for c in fake_webpush.calls)
    assert push_service._PUSH_EXECUTOR._max_workers == 4


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


# ===========================================================================
# APP PUSH (Expo) — mobile/06_phase3_push.md §3 B, migration 185
# ===========================================================================

TOK_1 = "ExponentPushToken[aaaaaaaaaaaaaaaaaaaaa1]"
TOK_2 = "ExponentPushToken[bbbbbbbbbbbbbbbbbbbbb2]"
TOK_B = "ExponentPushToken[ccccccccccccccccccccB1]"

FIXED_KEYS = {"to", "title", "body", "data", "channelId", "priority", "sound", "ttl"}


# ---------------------------------------------------------------------------
# Helpers + fixtures
# ---------------------------------------------------------------------------


def _tok(
    token: str,
    user_id: str = USER_A,
    failure_count: int = 0,
    *,
    platform: str = "android",
    created_at: str = "2026-01-01T00:00:00+00:00",
    last_success_at: Optional[str] = None,
) -> dict:
    return {
        "id": f"id-{token[-8:-1]}",
        "user_id": user_id,
        "token": token,
        "platform": platform,
        "app_version": "1.0.0",
        "created_at": created_at,
        "last_success_at": last_success_at,
        "failure_count": failure_count,
    }


def _rows(db: FakeSupabase) -> list[dict]:
    return db.tables.setdefault(app_push_service.TABLE, [])


def _expo_settings(monkeypatch: pytest.MonkeyPatch, *, enabled: bool,
                   access_token: Optional[str] = "expo-access-secret") -> None:
    monkeypatch.setattr(
        app_push_service, "get_settings",
        lambda: SimpleNamespace(EXPO_PUSH_ENABLED=enabled, EXPO_ACCESS_TOKEN=access_token),
    )


@pytest.fixture
def expo_on(monkeypatch: pytest.MonkeyPatch) -> None:
    _expo_settings(monkeypatch, enabled=True)


@pytest.fixture
def expo_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _expo_settings(monkeypatch, enabled=False)


@pytest.fixture
def expo(monkeypatch: pytest.MonkeyPatch):
    """Mock Expo push API. By default every message gets an ok ticket.

    Script it with:
      state.ticket_by_token[token] = {...}        per-message ticket override
      state.response = httpx.Response(...)        whole-request override
      state.raise_exc = httpx.ConnectError(...)   network failure
    """
    state = SimpleNamespace(requests=[], ticket_by_token={}, response=None, raise_exc=None)

    def handler(request: httpx.Request) -> httpx.Response:
        messages = json.loads(request.content.decode("utf-8"))
        state.requests.append({
            "method": request.method, "url": str(request.url),
            "headers": dict(request.headers), "messages": messages,
        })
        if state.raise_exc is not None:
            raise state.raise_exc
        if state.response is not None:
            return state.response
        tickets = [
            state.ticket_by_token.get(m["to"], {"status": "ok", "id": f"0193-ticket-{i}"})
            for i, m in enumerate(messages)
        ]
        return httpx.Response(200, json={"data": tickets})

    monkeypatch.setattr(
        app_push_service, "_new_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return state


@pytest.fixture
def logspy(monkeypatch: pytest.MonkeyPatch):
    """Record app_push_service Logfire events: list of (level, event, fields)."""
    events: list[tuple[str, str, dict]] = []

    class _Spy:
        def __getattr__(self, level: str):
            def _record(event: str, **fields: Any) -> None:
                events.append((level, event, fields))
            return _record

    monkeypatch.setattr(app_push_service, "_logfire", _Spy())
    return events


def _assert_no_token_logged(events: list, caplog: pytest.LogCaptureFixture, *tokens: str) -> None:
    blob = repr(events) + caplog.text
    for t in tokens:
        assert t not in blob
        assert t[len("ExponentPushToken["):-1] not in blob  # nor the bare device id


def _dnr_ticket(token: str) -> dict:
    # Real Expo error tickets echo the token in BOTH message and details.
    return {
        "status": "error",
        "message": f'"{token}" is not a registered push notification recipient',
        "details": {"error": "DeviceNotRegistered", "expoPushToken": token},
    }


def _err_ticket(code: str, token: str) -> dict:
    return {"status": "error", "message": f"failed for {token}",
            "details": {"error": code, "expoPushToken": token}}


# ---------------------------------------------------------------------------
# Token validation (request model)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token", [
    "ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]",   # what getExpoPushTokenAsync returns
    "ExpoPushToken[xxxxxxxxxxxxxxxxxxxxxx]",       # newer prefix
    "ExponentPushToken[abc-DEF_123.:+/=]",
    "ExponentPushToken[a]",
    "ExponentPushToken[" + "a" * 200 + "]",
])
def test_token_regex_accepts_expo_tokens(token: str) -> None:
    assert app_push_service.is_valid_expo_push_token(token)
    body = push_api.AppPushTokenRequest(token=token, platform="android")
    assert body.token == token


@pytest.mark.parametrize("token", [
    "",
    "ExponentPushToken[]",
    "ExponentPushToken[abc",
    "ExponentPushTokenabc]",
    "exponentpushtoken[abc]",
    "ExponentPushToken[ab c]",
    "ExponentPushToken[ab\tc]",
    "ExponentPushToken[ab]c]",
    "ExponentPushToken[abc]extra",
    "xExpoPushToken[abc]",
    "ExponentPushToken[" + "a" * 201 + "]",
    "https://fcm.googleapis.com/fcm/send/abc",     # a web endpoint
    "dGVzdC1mY20tZGV2aWNlLXRva2Vu",                # a raw FCM device token
    "4a9a5c1e-1f5b-4c8e-9a1d-2b3c4d5e6f70",        # bare UUID (SDK accepts; contract does not)
])
def test_token_regex_rejects_everything_else(token: str) -> None:
    assert not app_push_service.is_valid_expo_push_token(token)
    with pytest.raises(ValidationError):
        push_api.AppPushTokenRequest(token=token, platform="android")


def test_token_trailing_newline_rejected_by_regex_but_request_strips() -> None:
    assert not app_push_service.is_valid_expo_push_token(TOK_1 + "\n")
    assert push_api.AppPushTokenRequest(token=f"  {TOK_1}\n", platform="ios").token == TOK_1


@pytest.mark.parametrize("bad", [
    {"platform": "web"},
    {"platform": "Android"},
    {"platform": None},
    {"app_version": "1" * 33},
])
def test_request_rejects_bad_platform_and_long_version(bad: dict) -> None:
    with pytest.raises(ValidationError):
        push_api.AppPushTokenRequest(**{"token": TOK_1, "platform": "android", **bad})


def test_request_app_version_optional_and_blank_is_none() -> None:
    assert push_api.AppPushTokenRequest(token=TOK_1, platform="ios").app_version is None
    assert push_api.AppPushTokenRequest(token=TOK_1, platform="ios", app_version="  ").app_version is None
    assert push_api.AppPushTokenRequest(token=TOK_1, platform="ios",
                                        app_version=" 1.2.3 ").app_version == "1.2.3"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def test_register_works_with_sending_off_and_inserts_row(expo_off) -> None:
    db = FakeSupabase()
    body = push_api.AppPushTokenRequest(token=TOK_1, platform="android", app_version="1.0.3")
    resp = _run(push_api.register_app_token(body=body, current_user=_user(AUTH_A), supabase=db))

    assert resp.status_code == 204
    assert len(_rows(db)) == 1
    row = _rows(db)[0]
    assert (row["user_id"], row["token"], row["platform"], row["app_version"], row["failure_count"]) \
        == (USER_A, TOK_1, "android", "1.0.3", 0)


def test_register_same_token_reowns_and_refreshes(expo_off) -> None:
    db = FakeSupabase()
    _run(push_api.register_app_token(
        body=push_api.AppPushTokenRequest(token=TOK_1, platform="android", app_version="1.0.0"),
        current_user=_user(AUTH_A), supabase=db,
    ))
    _rows(db)[0]["failure_count"] = 3

    # Same phone, different account (sign-out → sign-in as B), newer app build.
    _run(push_api.register_app_token(
        body=push_api.AppPushTokenRequest(token=TOK_1, platform="ios", app_version="1.1.0"),
        current_user=_user(AUTH_B), supabase=db,
    ))
    assert len(_rows(db)) == 1
    row = _rows(db)[0]
    assert (row["user_id"], row["platform"], row["app_version"], row["failure_count"]) \
        == (USER_B, "ios", "1.1.0", 0)

    # Re-registering without app_version clears it (refreshed, not kept stale).
    _run(push_api.register_app_token(
        body=push_api.AppPushTokenRequest(token=TOK_1, platform="ios"),
        current_user=_user(AUTH_B), supabase=db,
    ))
    assert _rows(db)[0]["app_version"] is None


def test_register_trims_to_cap_keeping_new_and_most_recently_active(expo_off) -> None:
    db = FakeSupabase()
    _rows(db).extend([
        _tok("ExponentPushToken[old-never-ok]", created_at="2026-01-01T00:00:00+00:00"),  # trimmed
        _tok("ExponentPushToken[old-but-used]", created_at="2026-01-02T00:00:00+00:00",
             last_success_at="2026-09-20T00:00:00.123456+00:00"),                        # kept
        _tok("ExponentPushToken[mid-00000003]", created_at="2026-05-01T00:00:00+00:00"),
        _tok("ExponentPushToken[mid-00000004]", created_at="2026-06-01T00:00:00+00:00"),
        _tok("ExponentPushToken[mid-00000005]", created_at="2026-07-01T00:00:00+00:00"),
        _tok(TOK_B, USER_B, created_at="2025-01-01T00:00:00+00:00"),                     # other user
    ])
    new = "ExponentPushToken[brand-new-dev]"
    _run(push_api.register_app_token(
        body=push_api.AppPushTokenRequest(token=new, platform="android"),
        current_user=_user(AUTH_A), supabase=db,
    ))

    a_toks = {r["token"] for r in _rows(db) if r["user_id"] == USER_A}
    assert len(a_toks) == push_service.MAX_SUBS_PER_USER == 5
    assert "ExponentPushToken[old-never-ok]" not in a_toks
    assert {new, "ExponentPushToken[old-but-used]"} <= a_toks
    assert any(r["token"] == TOK_B for r in _rows(db))


def test_reregistering_the_stalest_token_keeps_it_over_the_cap(expo_off) -> None:
    db = FakeSupabase()
    stale = "ExponentPushToken[stalest-0001]"
    _rows(db).append(_tok(stale, created_at="2026-01-01T00:00:00+00:00"))
    for i in range(2, 7):
        _rows(db).append(_tok(f"ExponentPushToken[dev-{i:08d}]",
                              created_at=f"2026-0{i}-01T00:00:00+00:00"))

    _run(push_api.register_app_token(
        body=push_api.AppPushTokenRequest(token=stale, platform="android"),
        current_user=_user(AUTH_A), supabase=db,
    ))
    toks = {r["token"] for r in _rows(db)}
    assert len(toks) == 5
    assert stale in toks
    assert "ExponentPushToken[dev-00000002]" not in toks


def test_delete_removes_only_own_row_and_is_idempotent() -> None:
    db = FakeSupabase()
    _rows(db).extend([_tok(TOK_1, USER_A), _tok(TOK_B, USER_B)])

    # A tries to delete B's token → untouched, still 204 (no disclosure).
    resp = _run(push_api.unregister_app_token(
        body=push_api.AppPushTokenDeleteRequest(token=TOK_B),
        current_user=_user(AUTH_A), supabase=db,
    ))
    assert resp.status_code == 204
    assert {r["token"] for r in _rows(db)} == {TOK_1, TOK_B}

    for _ in range(2):  # second call: nothing left to delete, still 204
        resp = _run(push_api.unregister_app_token(
            body=push_api.AppPushTokenDeleteRequest(token=TOK_1),
            current_user=_user(AUTH_A), supabase=db,
        ))
        assert resp.status_code == 204
    assert [r["token"] for r in _rows(db)] == [TOK_B]


def test_delete_is_lenient_so_any_own_row_can_be_removed() -> None:
    db = FakeSupabase()
    legacy = "not-an-expo-token-row"
    _rows(db).append(_tok(legacy, USER_A))
    _run(push_api.unregister_app_token(
        body=push_api.AppPushTokenDeleteRequest(token=f" {legacy} "),
        current_user=_user(AUTH_A), supabase=db,
    ))
    assert _rows(db) == []


class _ExplodingTable:
    """Supabase stand-in: users lookup works, app_push_tokens raises with the token in the message."""

    def __init__(self) -> None:
        self.db = FakeSupabase()

    def table(self, name: str):
        if name == app_push_service.TABLE:
            raise RuntimeError(f'Failing row contains (x, {TOK_1}, android)')
        return self.db.table(name)


@pytest.mark.parametrize("route, body, msg", [
    ("register_app_token",
     push_api.AppPushTokenRequest(token=TOK_1, platform="android"),
     push_api.MSG_PUSH_SUBSCRIBE_FAILED),
    ("unregister_app_token",
     push_api.AppPushTokenDeleteRequest(token=TOK_1),
     push_api.MSG_PUSH_UNSUBSCRIBE_FAILED),
])
def test_route_db_failure_is_arabic_500_and_never_logs_token(
    route: str, body: Any, msg: str, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    with pytest.raises(LunaHTTPException) as ei:
        _run(getattr(push_api, route)(body=body, current_user=_user(AUTH_A),
                                      supabase=_ExplodingTable()))
    assert ei.value.status_code == 500
    assert ei.value.detail == msg
    assert any("؀" <= ch <= "ۿ" for ch in ei.value.detail)
    assert TOK_1 not in caplog.text


# ---------------------------------------------------------------------------
# Sender
# ---------------------------------------------------------------------------


def test_ok_tickets_send_fixed_payload_and_stamp_rows(
    expo_on, expo, logspy, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    db = FakeSupabase()
    _rows(db).extend([_tok(TOK_1, failure_count=2), _tok(TOK_2, platform="ios"),
                      _tok(TOK_B, USER_B)])

    _run(app_push_service.notify_turn_ready_app(USER_A, CONV, supabase=db))

    assert len(expo.requests) == 1  # ONE batched POST
    req = expo.requests[0]
    assert req["method"] == "POST"
    assert req["url"] == "https://exp.host/--/api/v2/push/send"
    assert req["headers"]["content-type"] == "application/json"
    assert req["headers"]["accept"] == "application/json"
    assert req["headers"]["authorization"] == "Bearer expo-access-secret"
    assert sorted(m["to"] for m in req["messages"]) == sorted([TOK_1, TOK_2])  # only USER_A
    for m in req["messages"]:
        assert m == {
            "to": m["to"],
            "title": "ريحان",
            "body": "إجابتك جاهزة",
            "data": {"conversation_id": CONV},
            "channelId": "turn-ready",
            "priority": "high",
            "sound": "default",
            "ttl": 21600,
        }

    a_rows = [r for r in _rows(db) if r["user_id"] == USER_A]
    assert all(r["last_success_at"] and r["failure_count"] == 0 for r in a_rows)
    b_row = next(r for r in _rows(db) if r["user_id"] == USER_B)
    assert b_row["last_success_at"] is None

    sent = [f for (_lvl, ev, f) in logspy if ev == "push.sent"]
    assert len(sent) == 2
    assert all(f["channel"] == "expo" and f["conversation_id"] == CONV for f in sent)
    assert {f["platform"] for f in sent} == {"android", "ios"}
    _assert_no_token_logged(logspy, caplog, TOK_1, TOK_2)


def test_payload_carries_no_content_keys() -> None:
    msg = app_push_service.build_expo_message(TOK_1, CONV)
    assert set(msg) == FIXED_KEYS
    assert set(msg["data"]) == {"conversation_id"}
    assert (msg["title"], msg["body"]) == ("ريحان", "إجابتك جاهزة")
    # There is no way to pass a title / question / answer to the app sender.
    params = set(inspect.signature(app_push_service.notify_turn_ready_app).parameters)
    assert params == {"user_id", "conversation_id", "supabase"}
    assert set(inspect.signature(app_push_service.build_expo_message).parameters) \
        == {"token", "conversation_id"}


def test_no_authorization_header_without_access_token(monkeypatch, expo, logspy) -> None:
    for blank in (None, "", "   "):
        _expo_settings(monkeypatch, enabled=True, access_token=blank)
        db = FakeSupabase()
        _rows(db).append(_tok(TOK_1))
        _run(app_push_service.notify_turn_ready_app(USER_A, CONV, supabase=db))
        assert "authorization" not in expo.requests[-1]["headers"]
    assert len(expo.requests) == 3


def test_device_not_registered_deletes_row_without_logging_token(
    expo_on, expo, logspy, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    db = FakeSupabase()
    _rows(db).extend([_tok(TOK_1), _tok(TOK_2)])
    expo.ticket_by_token[TOK_1] = _dnr_ticket(TOK_1)

    _run(app_push_service.notify_turn_ready_app(USER_A, CONV, supabase=db))

    assert [r["token"] for r in _rows(db)] == [TOK_2]
    assert _rows(db)[0]["last_success_at"]
    pruned = [f for (_lvl, ev, f) in logspy if ev == "push.pruned"]
    assert pruned == [{"channel": "expo", "conversation_id": CONV,
                       "platform": "android", "error": "DeviceNotRegistered"}]
    _assert_no_token_logged(logspy, caplog, TOK_1, TOK_2)


@pytest.mark.parametrize("code", ["MessageRateExceeded", "ProviderError", "ExpoError", "SomethingNew"])
def test_other_ticket_error_bumps_failure_count_then_prunes_at_five(
    expo_on, expo, logspy, caplog: pytest.LogCaptureFixture, code: str,
) -> None:
    caplog.set_level(logging.DEBUG)
    db = FakeSupabase()
    _rows(db).append(_tok(TOK_1, failure_count=3))
    expo.ticket_by_token[TOK_1] = _err_ticket(code, TOK_1)

    _run(app_push_service.notify_turn_ready_app(USER_A, CONV, supabase=db))
    assert _rows(db)[0]["failure_count"] == 4
    failed = [f for (_lvl, ev, f) in logspy if ev == "push.failed"]
    assert failed[-1]["error"] == code and failed[-1]["failure_count"] == 4

    _run(app_push_service.notify_turn_ready_app(USER_A, CONV, supabase=db))  # 5th failure
    assert _rows(db) == []
    pruned = [f for (_lvl, ev, f) in logspy if ev == "push.pruned"]
    assert pruned[-1]["reason"] == "max_failures" and pruned[-1]["failure_count"] == 5
    _assert_no_token_logged(logspy, caplog, TOK_1)


def test_ticket_without_details_counts_as_unknown_error(expo_on, expo, logspy) -> None:
    db = FakeSupabase()
    _rows(db).append(_tok(TOK_1))
    expo.ticket_by_token[TOK_1] = {"status": "error", "message": f"{TOK_1} broke"}
    _run(app_push_service.notify_turn_ready_app(USER_A, CONV, supabase=db))
    assert _rows(db)[0]["failure_count"] == 1
    assert [f["error"] for (_l, ev, f) in logspy if ev == "push.failed"] == ["unknown"]


@pytest.mark.parametrize("code", ["InvalidCredentials", "DeveloperError", "MessageTooBig"])
def test_config_ticket_errors_never_count_against_the_device(expo_on, expo, logspy, code: str) -> None:
    # Our FCM/APNs credentials or request are wrong, not the phone. Pruning
    # would lose the row for good (the app only re-registers on token change).
    db = FakeSupabase()
    _rows(db).append(_tok(TOK_1, failure_count=4))
    expo.ticket_by_token[TOK_1] = _err_ticket(code, TOK_1)

    _run(app_push_service.notify_turn_ready_app(USER_A, CONV, supabase=db))
    assert _rows(db)[0]["failure_count"] == 4
    failed = [f for (_lvl, ev, f) in logspy if ev == "push.failed"]
    assert failed == [{"channel": "expo", "conversation_id": CONV, "platform": "android",
                       "error": code, "reason": "config_error"}]


@pytest.mark.parametrize("scenario", ["http_503", "network", "timeout", "malformed_200", "short_200"])
def test_whole_request_failure_bumps_every_token(
    expo_on, expo, logspy, caplog: pytest.LogCaptureFixture, scenario: str,
) -> None:
    caplog.set_level(logging.DEBUG)
    db = FakeSupabase()
    _rows(db).extend([_tok(TOK_1, failure_count=0), _tok(TOK_2, failure_count=4)])
    if scenario == "http_503":
        expo.response = httpx.Response(503, text="upstream unavailable")
    elif scenario == "network":
        expo.raise_exc = httpx.ConnectError("connection refused")
    elif scenario == "timeout":
        expo.raise_exc = httpx.ReadTimeout("timed out")
    elif scenario == "malformed_200":
        expo.response = httpx.Response(200, text="<html>not json</html>")
    else:  # fewer tickets than messages
        expo.response = httpx.Response(200, json={"data": [{"status": "ok", "id": "t1"}]})

    _run(app_push_service.notify_turn_ready_app(USER_A, CONV, supabase=db))

    # TOK_1: 0 → 1; TOK_2: 4 → 5 → pruned.
    assert [(r["token"], r["failure_count"]) for r in _rows(db)] == [(TOK_1, 1)]
    assert all(r["last_success_at"] is None for r in _rows(db))
    events = {ev for (_l, ev, f) in logspy if f.get("channel") == "expo"}
    assert events == {"push.failed", "push.pruned"}
    _assert_no_token_logged(logspy, caplog, TOK_1, TOK_2)


@pytest.mark.parametrize("response", [
    httpx.Response(401, json={"errors": [{"code": "UNAUTHORIZED", "message": "bad token"}]}),
    httpx.Response(429, json={"errors": [{"code": "RATE_LIMIT_ERROR", "message": "slow down"}]}),
    httpx.Response(400, json={"errors": [{"code": "PUSH_TOO_MANY_EXPERIENCE_IDS",
                                          "message": f"{TOK_1} and {TOK_2}",
                                          "details": {"@a/b": [TOK_1], "@c/d": [TOK_2]}}]}),
    httpx.Response(200, json={"errors": [{"code": "VALIDATION_ERROR", "message": "x"}]}),
])
def test_request_rejected_by_expo_leaves_rows_untouched(
    expo_on, expo, logspy, caplog: pytest.LogCaptureFixture, response: httpx.Response,
) -> None:
    caplog.set_level(logging.DEBUG)
    db = FakeSupabase()
    _rows(db).extend([_tok(TOK_1, failure_count=4), _tok(TOK_2)])
    expo.response = response

    _run(app_push_service.notify_turn_ready_app(USER_A, CONV, supabase=db))

    assert [(r["token"], r["failure_count"]) for r in _rows(db)] == [(TOK_1, 4), (TOK_2, 0)]
    failed = [f for (_l, ev, f) in logspy if ev == "push.failed"]
    assert len(failed) == 1
    assert failed[0]["reason"] == "request_rejected"
    assert failed[0]["status"] == response.status_code
    assert failed[0]["error"] == response.json()["errors"][0]["code"]
    _assert_no_token_logged(logspy, caplog, TOK_1, TOK_2)


def test_send_never_exceeds_cap_and_prefers_most_recently_successful(expo_on, expo, logspy) -> None:
    db = FakeSupabase()
    for i in range(1, 4):
        _rows(db).append(_tok(f"ExponentPushToken[used-{i:07d}]",
                              created_at="2026-01-01T00:00:00+00:00",
                              last_success_at=f"2026-09-0{i}T00:00:00+00:00"))
    for i in range(1, 6):
        _rows(db).append(_tok(f"ExponentPushToken[never-{i:06d}]",
                              created_at=f"2026-0{i}-01T00:00:00+00:00"))

    _run(app_push_service.notify_turn_ready_app(USER_A, CONV, supabase=db))

    assert len(expo.requests) == 1
    sent = [m["to"] for m in expo.requests[0]["messages"]]
    assert len(sent) == push_service.MAX_SUBS_PER_USER <= app_push_service.EXPO_BATCH_LIMIT
    assert set(sent) == {"ExponentPushToken[used-0000001]", "ExponentPushToken[used-0000002]",
                         "ExponentPushToken[used-0000003]", "ExponentPushToken[never-000005]",
                         "ExponentPushToken[never-000004]"}


def test_stored_malformed_tokens_are_never_sent(expo_on, expo, logspy) -> None:
    db = FakeSupabase()
    _rows(db).extend([_tok("garbage-from-a-direct-insert"), _tok(TOK_1)])
    _run(app_push_service.notify_turn_ready_app(USER_A, CONV, supabase=db))
    assert [m["to"] for m in expo.requests[0]["messages"]] == [TOK_1]
    assert any(ev == "push.skipped_invalid_token" for (_l, ev, _f) in logspy)

    db2 = FakeSupabase()
    _rows(db2).append(_tok("garbage-only"))
    _run(app_push_service.notify_turn_ready_app(USER_A, CONV, supabase=db2))
    assert len(expo.requests) == 1  # no POST at all when nothing valid is left


def test_no_tokens_means_no_request(expo_on, expo, logspy) -> None:
    _run(app_push_service.notify_turn_ready_app(USER_A, CONV, supabase=FakeSupabase()))
    assert expo.requests == []


def test_flag_off_sender_is_a_noop(expo_off, expo, logspy) -> None:
    db = FakeSupabase()
    _rows(db).append(_tok(TOK_1))
    _run(app_push_service.notify_turn_ready_app(USER_A, CONV, supabase=db))
    assert expo.requests == []
    assert _rows(db)[0]["last_success_at"] is None


def test_sender_never_raises_even_if_db_breaks(expo_on, expo, logspy) -> None:
    class Broken:
        def table(self, _name: str):
            raise RuntimeError("db down")

    _run(app_push_service.notify_turn_ready_app(USER_A, CONV, supabase=Broken()))  # no raise
    assert expo.requests == []


def test_http_client_has_10s_timeout_and_no_redirects() -> None:
    async def _go() -> None:
        async with app_push_service._new_http_client() as c:
            assert c.timeout.connect == c.timeout.read == 10.0
            assert c.follow_redirects is False

    _run(_go())


# ---------------------------------------------------------------------------
# schedule_turn_ready split (web half × app half)
# ---------------------------------------------------------------------------


async def _schedule_and_drain(db: FakeSupabase) -> int:
    push_service.schedule_turn_ready(USER_A, CONV, supabase=db)
    n = len(push_service._background_tasks)
    await asyncio.gather(*list(push_service._background_tasks))
    return n


def _db_with_both_channels() -> FakeSupabase:
    db = FakeSupabase()
    db.subs.append({
        "id": "id-web-1", "user_id": USER_A, "endpoint": "https://fcm.googleapis.com/fcm/send/dev-01",
        "p256dh": "BPkey", "auth": "authsecret", "user_agent": None,
        "created_at": "2026-01-01T00:00:00+00:00", "last_success_at": None, "failure_count": 0,
    })
    _rows(db).append(_tok(TOK_1))
    return db


def test_flag_off_no_app_send_and_web_half_unchanged(vapid_on, fake_webpush, expo_off, expo) -> None:
    db = _db_with_both_channels()
    n = _run(_schedule_and_drain(db))
    assert n == 1                      # exactly today's single web task
    assert len(fake_webpush.calls) == 1
    assert json.loads(fake_webpush.calls[0]["data"]) == {
        "title": "ريحان", "body": "إجابتك جاهزة", "url": f"/chat/{CONV}", "tag": CONV,
    }
    assert expo.requests == []
    assert push_service._background_tasks == set()


def test_vapid_unset_flag_on_still_sends_app_push(vapid_off, fake_webpush, expo_on, expo, logspy) -> None:
    db = _db_with_both_channels()
    n = _run(_schedule_and_drain(db))
    assert n == 1
    assert fake_webpush.calls == []
    assert [m["to"] for m in expo.requests[0]["messages"]] == [TOK_1]
    assert _rows(db)[0]["last_success_at"]
    assert push_service._background_tasks == set()


def test_both_configured_fire_two_independent_tasks(vapid_on, fake_webpush, expo_on, expo, logspy) -> None:
    db = _db_with_both_channels()
    n = _run(_schedule_and_drain(db))
    assert n == 2
    assert len(fake_webpush.calls) == 1
    assert len(expo.requests) == 1
    assert push_service._background_tasks == set()


def test_neither_configured_schedules_nothing(vapid_off, fake_webpush, expo_off, expo) -> None:
    db = _db_with_both_channels()
    assert _run(_schedule_and_drain(db)) == 0
    assert fake_webpush.calls == [] and expo.requests == []


def test_app_half_failure_never_blocks_web_half(
    monkeypatch, vapid_on, fake_webpush, expo, caplog: pytest.LogCaptureFixture,
) -> None:
    def _boom() -> bool:
        raise RuntimeError("settings exploded")

    monkeypatch.setattr(app_push_service, "expo_push_enabled", _boom)
    db = _db_with_both_channels()
    n = _run(_schedule_and_drain(db))  # no raise
    assert n == 1
    assert len(fake_webpush.calls) == 1
    assert "failed to schedule notify_turn_ready_app" in caplog.text


def test_schedule_without_running_loop_never_raises_or_leaks_coroutine(
    vapid_on, fake_webpush, expo_on, expo, recwarn,
) -> None:
    push_service.schedule_turn_ready(USER_A, CONV, supabase=FakeSupabase())  # sync context
    assert push_service._background_tasks == set()
    assert not [w for w in recwarn.list if "never awaited" in str(w.message)]
