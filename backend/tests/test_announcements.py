"""«رسائل ريحان» — /api/v1/announcements routes + announcement_service.

No live DB: the Supabase client is an in-memory fake whose
``rpc("get_user_announcements")`` applies the same audience rule as migration
189 (published, and 'all' or 'paid' for a paying user) and whose
``announcement_reads`` upsert records receipts. ``get_user_id`` is patched to
map auth ids straight to user ids.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from backend.app.api import announcements as api
from backend.app.errors import LunaHTTPException
from backend.app.services import announcement_service as service
from shared.auth.jwt import AuthUser

PAID = "aaaaaaaa-0000-0000-0000-000000000001"
FREE = "bbbbbbbb-0000-0000-0000-000000000002"

A_ALL = "11111111-0000-0000-0000-000000000001"
A_PAID = "22222222-0000-0000-0000-000000000002"
A_FUTURE = "33333333-0000-0000-0000-000000000003"

NOW = datetime.now(timezone.utc)


class _Exec:
    def __init__(self, data: Any = None, on_execute=None) -> None:
        self._data = data
        self._on_execute = on_execute

    def execute(self) -> SimpleNamespace:
        if self._on_execute:
            self._on_execute()
        return SimpleNamespace(data=self._data)


class FakeSupabase:
    def __init__(self) -> None:
        self.announcements = [
            {"announcement_id": A_ALL, "title": "للجميع", "body_md": "نص",
             "audience": "all", "link_label": None, "link_url": None,
             "published_at": (NOW - timedelta(days=2)).isoformat()},
            {"announcement_id": A_PAID, "title": "للمشتركين", "body_md": "نص",
             "audience": "paid", "link_label": "سياسة حد الاستخدام",
             "link_url": "/learn/usage-limits",
             "published_at": (NOW - timedelta(days=1)).isoformat()},
            {"announcement_id": A_FUTURE, "title": "مجدولة", "body_md": "نص",
             "audience": "all", "link_label": None, "link_url": None,
             "published_at": (NOW + timedelta(days=1)).isoformat()},
        ]
        self.paid_users = {PAID}
        self.reads: set[tuple[str, str]] = set()
        self.upserts: list[list[dict]] = []
        self.fail = False

    def rpc(self, name: str, params: dict) -> _Exec:
        assert name == "get_user_announcements"
        if self.fail:
            raise RuntimeError("db down")
        uid = params["p_user_id"]
        rows = [
            {**a, "is_read": (uid, a["announcement_id"]) in self.reads}
            for a in self.announcements
            if datetime.fromisoformat(a["published_at"]) <= NOW
            and (a["audience"] == "all" or uid in self.paid_users)
        ]
        rows.sort(key=lambda r: r["published_at"], reverse=True)
        return _Exec(rows)

    def table(self, name: str) -> "FakeSupabase":
        assert name == "announcement_reads"
        return self

    def upsert(self, rows: list[dict], on_conflict: str, ignore_duplicates: bool) -> _Exec:
        assert on_conflict == "user_id,announcement_id" and ignore_duplicates

        def apply() -> None:
            self.upserts.append(rows)
            for r in rows:
                self.reads.add((r["user_id"], r["announcement_id"]))

        return _Exec(on_execute=apply)


@pytest.fixture(autouse=True)
def _identity_user_id(monkeypatch) -> None:
    monkeypatch.setattr(api, "get_user_id", lambda _sb, auth_id: auth_id)


def _user(uid: str) -> AuthUser:
    return AuthUser(auth_id=uid, email="u@example.com", role="authenticated")


def _run(coro):
    return asyncio.run(coro)


def _list(db: FakeSupabase, uid: str) -> dict:
    return _run(api.list_announcements(current_user=_user(uid), supabase=db))


def _read(db: FakeSupabase, uid: str, ids: list[str]):
    body = api.MarkReadRequest(ids=ids)
    return _run(api.mark_announcements_read(body=body, current_user=_user(uid), supabase=db))


# ---------------------------------------------------------------------------


def test_paid_user_sees_all_and_paid_newest_first_with_contract_shape() -> None:
    out = _list(FakeSupabase(), PAID)
    assert [i["announcement_id"] for i in out["items"]] == [A_PAID, A_ALL]
    assert out["unread_count"] == 2
    assert set(out["items"][0]) == {
        "announcement_id", "title", "body_md", "link_label", "link_url",
        "published_at", "read",
    }
    assert out["items"][0]["link_url"] == "/learn/usage-limits"
    assert out["items"][0]["read"] is False


def test_free_user_never_sees_paid_and_scheduled_is_hidden() -> None:
    out = _list(FakeSupabase(), FREE)
    assert [i["announcement_id"] for i in out["items"]] == [A_ALL]
    assert out["unread_count"] == 1


def test_mark_read_flips_read_and_drops_unread_count() -> None:
    db = FakeSupabase()
    resp = _read(db, PAID, [A_PAID])
    assert resp.status_code == 204
    out = _list(db, PAID)
    assert {i["announcement_id"]: i["read"] for i in out["items"]} == {A_PAID: True, A_ALL: False}
    assert out["unread_count"] == 1


def test_mark_read_is_idempotent() -> None:
    db = FakeSupabase()
    _read(db, PAID, [A_PAID])
    _read(db, PAID, [A_PAID])
    assert len(db.upserts) == 1  # the second call found nothing unread to write


def test_mark_read_ignores_messages_the_caller_cannot_see() -> None:
    db = FakeSupabase()
    # A free user targeting a paid message, a scheduled one, and an unknown id.
    resp = _read(db, FREE, [A_PAID, A_FUTURE, "44444444-0000-0000-0000-000000000004"])
    assert resp.status_code == 204
    assert db.reads == set() and db.upserts == []


def test_mark_read_is_scoped_to_the_caller() -> None:
    db = FakeSupabase()
    _read(db, PAID, [A_ALL])
    assert _list(db, FREE)["unread_count"] == 1
    assert db.reads == {(PAID, A_ALL)}


def test_mark_read_matches_ids_case_insensitively() -> None:
    db = FakeSupabase()
    assert service.mark_read(db, PAID, [A_PAID.upper()]) == 1


def test_mark_read_body_validation() -> None:
    with pytest.raises(ValidationError):
        api.MarkReadRequest(ids=[])
    with pytest.raises(ValidationError):
        api.MarkReadRequest(ids=["not-a-uuid"])
    with pytest.raises(ValidationError):
        api.MarkReadRequest(ids=[A_ALL] * 51)


def test_db_failure_is_an_arabic_500() -> None:
    db = FakeSupabase()
    db.fail = True
    with pytest.raises(LunaHTTPException) as ei:
        _list(db, PAID)
    assert ei.value.status_code == 500
    assert ei.value.detail == api.MSG_LIST_FAILED
    assert any("؀" <= ch <= "ۿ" for ch in ei.value.detail)
