"""Marketing-email links + settings toggle (marketing plans/email/00 T6/T7).

The traps these guard:

* **GET never writes.** Link scanners prefetch every URL in a message; a GET
  that changed a row would unsubscribe whole firms, or manufacture consent.
* **Tokens are purpose-bound.** An unsubscribe link must not work as an opt-in.
* **POST is not an oracle.** A bad token gets the same 200 page as a good one.
* **Evidence is stamped.** Every write carries consent_at + consent_src.
* **The toggle reads a pre-167 default TRUE as OFF** — no recorded decision.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import email_prefs
from backend.app.deps import get_supabase
from backend.app.services import preferences_service

SECRET = "test-secret-0123456789"
USER_ID = "aaaaaaaa-0000-0000-0000-000000000001"


class _Query:
    def __init__(self, fake: "FakeSupabase", table: str) -> None:
        self._fake, self._table = fake, table
        self._payload: Any = None

    def update(self, payload: dict) -> "_Query":
        self._payload = payload
        return self

    def select(self, *_a: Any) -> "_Query":
        return self

    def eq(self, col: str, val: Any) -> "_Query":
        self._eq = (col, val)
        return self

    def single(self) -> "_Query":
        return self

    def execute(self) -> Any:
        if self._fake.fail:
            raise RuntimeError("db down")
        if self._payload is not None:
            self._fake.updates.append((self._table, self._eq, self._payload))
        return SimpleNamespace(data=self._fake.row)


class FakeSupabase:
    def __init__(self) -> None:
        self.updates: list[tuple] = []
        self.fail = False
        self.row: dict = {}

    def table(self, name: str) -> _Query:
        return _Query(self, name)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeSupabase:
    monkeypatch.setattr(
        email_prefs, "get_settings", lambda: SimpleNamespace(EMAIL_LINK_SECRET=SECRET)
    )
    return FakeSupabase()


@pytest.fixture
def client(fake: FakeSupabase) -> TestClient:
    app = FastAPI()
    app.include_router(email_prefs.router)
    app.dependency_overrides[get_supabase] = lambda: fake
    return TestClient(app)


def _q(purpose: str, user_id: str = USER_ID) -> dict:
    return {"u": user_id, "t": email_prefs.make_token(SECRET, purpose, user_id)}


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


def test_token_is_stable_and_purpose_bound() -> None:
    a = email_prefs.make_token(SECRET, "unsubscribe", USER_ID)
    assert a == email_prefs.make_token(SECRET, "unsubscribe", USER_ID.upper())
    assert a != email_prefs.make_token(SECRET, "subscribe", USER_ID)
    assert len(a) == email_prefs.TOKEN_CHARS


def test_token_matches_the_marketing_repo_vector() -> None:
    # Pinned: marketing/scripts/email_unsub.py must mint this exact string.
    assert (
        email_prefs.make_token("k", "unsubscribe", USER_ID)
        == "9MZSvKK12GVyBBYC_QZPHf2rZ7dI2EmX"
    )


def test_unset_secret_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        email_prefs, "get_settings", lambda: SimpleNamespace(EMAIL_LINK_SECRET=None)
    )
    t = email_prefs.make_token("", "unsubscribe", USER_ID)
    assert not email_prefs.verify_token("unsubscribe", USER_ID, t)


# ---------------------------------------------------------------------------
# Unsubscribe
# ---------------------------------------------------------------------------


def test_get_asks_and_never_writes(client: TestClient, fake: FakeSupabase) -> None:
    r = client.get("/api/v1/public/email/unsubscribe", params=_q("unsubscribe"))
    assert r.status_code == 200
    assert 'method="post"' in r.text
    assert r.headers["referrer-policy"] == "no-referrer"
    assert fake.updates == []


def test_one_click_post_unsubscribes_with_evidence(
    client: TestClient, fake: FakeSupabase
) -> None:
    r = client.post(
        "/api/v1/public/email/unsubscribe",
        params=_q("unsubscribe"),
        content="List-Unsubscribe=One-Click",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 200
    (table, eq, payload), = fake.updates
    assert table == "users" and eq == ("user_id", USER_ID)
    assert payload["marketing_opt_in"] is False
    assert payload["marketing_consent_src"] == "unsubscribe"
    assert payload["marketing_consent_at"]
    # The re-subscribe button carries a SUBSCRIBE token, not the one we got.
    assert email_prefs.make_token(SECRET, "subscribe", USER_ID) in r.text


def test_bad_token_post_looks_like_success(client: TestClient, fake: FakeSupabase) -> None:
    good = client.post("/api/v1/public/email/unsubscribe", params=_q("unsubscribe"))
    fake.updates.clear()
    bad = client.post(
        "/api/v1/public/email/unsubscribe", params={"u": USER_ID, "t": "x" * 32}
    )
    assert bad.status_code == good.status_code == 200
    assert "تم إيقاف الرسائل التسويقية" in bad.text
    assert fake.updates == []


def test_subscribe_token_cannot_unsubscribe(client: TestClient, fake: FakeSupabase) -> None:
    client.post("/api/v1/public/email/unsubscribe", params=_q("subscribe"))
    assert fake.updates == []


def test_db_failure_is_not_reported_as_done(client: TestClient, fake: FakeSupabase) -> None:
    fake.fail = True
    r = client.post("/api/v1/public/email/unsubscribe", params=_q("unsubscribe"))
    assert r.status_code == 503
    assert "تم إيقاف" not in r.text


# ---------------------------------------------------------------------------
# Subscribe (re-permission)
# ---------------------------------------------------------------------------


def test_subscribe_get_never_writes(client: TestClient, fake: FakeSupabase) -> None:
    r = client.get("/api/v1/public/email/subscribe", params=_q("subscribe"))
    assert r.status_code == 200 and fake.updates == []


def test_subscribe_post_opts_in_as_repermission(client: TestClient, fake: FakeSupabase) -> None:
    client.post("/api/v1/public/email/subscribe", params=_q("subscribe"))
    (_, _, payload), = fake.updates
    assert payload["marketing_opt_in"] is True
    assert payload["marketing_consent_src"] == "repermission_email"


def test_unsubscribe_token_cannot_opt_in(client: TestClient, fake: FakeSupabase) -> None:
    r = client.post("/api/v1/public/email/subscribe", params=_q("unsubscribe"))
    assert "الرابط غير صالح" in r.text
    assert fake.updates == []


# ---------------------------------------------------------------------------
# Settings toggle (service layer)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "row, expected",
    [
        ({"marketing_opt_in": True, "marketing_consent_at": None}, False),  # pre-167 default
        ({"marketing_opt_in": True, "marketing_consent_at": "2026-09-24T00:00:00Z"}, True),
        ({"marketing_opt_in": False, "marketing_consent_at": "2026-09-24T00:00:00Z"}, False),
    ],
)
def test_toggle_reads_default_true_as_off(
    monkeypatch: pytest.MonkeyPatch, row: dict, expected: bool
) -> None:
    monkeypatch.setattr(preferences_service, "get_user_id", lambda _s, _a: USER_ID)
    fake = FakeSupabase()
    fake.row = row
    assert preferences_service.get_marketing_opt_in(fake, "auth") is expected


def test_toggle_write_is_stamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(preferences_service, "get_user_id", lambda _s, _a: USER_ID)
    fake = FakeSupabase()
    assert preferences_service.set_marketing_opt_in(fake, "auth", True) is True
    (_, _, payload), = fake.updates
    assert payload["marketing_consent_src"] == "settings_toggle"
    assert payload["marketing_consent_at"]
