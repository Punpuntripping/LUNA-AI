"""الأسبوع المجاني (migration 205) — the two routes in front of the SQL rule.

The rule itself (new account, never paid, once per user, no charge, expiry back
to free) is tested by the migration's own self-test against the real functions.
These guard what the routes add:

* **Fail closed.** An unreadable answer is "no offer", never a 500: the limit
  dialog must still show the ordinary plans.
* **The claim asks the database.** Anything but ``claimed`` is a refusal, so a
  stale dialog cannot grant a second week.
* **The internal user_id reaches the RPC,** not the auth id.
* **No cache** may hand one account's answer to another.
* **The claim shares the redeem rate limit,** and the events are on the allowlist.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import plans
from backend.app.deps import get_current_user, get_redis, get_supabase
from backend.app.middleware import rate_limit
from backend.app.services.analytics_service import PUBLIC_EVENT_NAMES

AUTH_ID = "bbbbbbbb-0000-0000-0000-000000000002"
USER_ID = "aaaaaaaa-0000-0000-0000-000000000001"


class _Rpc:
    def __init__(self, fake: "FakeSupabase", name: str) -> None:
        self._fake, self._name = fake, name

    def execute(self) -> Any:
        if self._fake.fail:
            raise RuntimeError("db down")
        if self._name == "free_trial_days":
            if self._fake.days == "fail":
                raise RuntimeError("db down")
            return SimpleNamespace(data=self._fake.days)
        return SimpleNamespace(data=self._fake.data)


class FakeSupabase:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.data: Any = None
        self.days: Any = 7
        self.fail = False

    def rpc(self, name: str, params: dict) -> _Rpc:
        self.calls.append((name, params))
        return _Rpc(self, name)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeSupabase:
    monkeypatch.setattr(plans, "get_user_id", lambda _sb, _auth_id: USER_ID)
    return FakeSupabase()


@pytest.fixture
def client(fake: FakeSupabase) -> TestClient:
    app = FastAPI()
    app.include_router(plans.router, prefix="/api/v1")
    app.dependency_overrides[get_supabase] = lambda: fake
    app.dependency_overrides[get_redis] = lambda: None
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(auth_id=AUTH_ID)
    return TestClient(app)


def test_eligible_answer_is_passed_through_uncached(client: TestClient, fake: FakeSupabase) -> None:
    fake.data = True

    res = client.get("/api/v1/plans/free-trial")

    assert res.status_code == 200
    assert res.json() == {"eligible": True, "days": 7}
    assert res.headers["cache-control"] == "no-store"
    assert fake.calls == [
        ("free_trial_eligible", {"p_user_id": USER_ID}),
        ("free_trial_days", {"p_user_id": USER_ID}),
    ]


def test_professions_are_told_two_weeks(client: TestClient, fake: FakeSupabase) -> None:
    fake.data = True
    fake.days = 14

    assert client.get("/api/v1/plans/free-trial").json() == {"eligible": True, "days": 14}


def test_an_unreadable_length_keeps_the_offer(client: TestClient, fake: FakeSupabase) -> None:
    fake.data = True
    fake.days = "fail"

    assert client.get("/api/v1/plans/free-trial").json() == {"eligible": True, "days": None}


def test_not_eligible(client: TestClient, fake: FakeSupabase) -> None:
    fake.data = False

    assert client.get("/api/v1/plans/free-trial").json() == {"eligible": False, "days": None}
    assert [name for name, _ in fake.calls] == ["free_trial_eligible"]


@pytest.mark.parametrize("data", [None, "true", 1, [True]])
def test_anything_but_true_is_not_eligible(client: TestClient, fake: FakeSupabase, data: Any) -> None:
    fake.data = data

    assert client.get("/api/v1/plans/free-trial").json() == {"eligible": False, "days": None}


def test_unreadable_answer_fails_closed_not_500(client: TestClient, fake: FakeSupabase) -> None:
    fake.fail = True

    res = client.get("/api/v1/plans/free-trial")

    assert res.status_code == 200
    assert res.json() == {"eligible": False, "days": None}


def test_claim_returns_the_grant(client: TestClient, fake: FakeSupabase) -> None:
    fake.data = [
        {
            "trial_plan_id": "marketing_lawyer",
            "trial_name_ar": "ترويجي",
            "trial_expires_at": "2026-11-09T12:00:00+00:00",
            "action": "claimed",
        }
    ]

    res = client.post("/api/v1/plans/free-trial/claim")

    assert res.status_code == 200
    assert res.json() == {
        "plan_id": "marketing_lawyer",
        "name_ar": "ترويجي",
        "expires_at": "2026-11-09T12:00:00+00:00",
    }
    assert res.headers["cache-control"] == "no-store"
    assert fake.calls == [("claim_free_trial", {"p_user_id": USER_ID})]


@pytest.mark.parametrize(
    "data",
    [
        [{"trial_plan_id": None, "trial_name_ar": None, "trial_expires_at": None, "action": "not_eligible"}],
        [],
        None,
    ],
)
def test_anything_but_claimed_is_refused_in_arabic(client: TestClient, fake: FakeSupabase, data: Any) -> None:
    fake.data = data

    res = client.post("/api/v1/plans/free-trial/claim")

    assert res.status_code == 409
    body = res.json()
    detail = body.get("detail")
    text = detail if isinstance(detail, str) else str(body)
    assert plans.FREE_TRIAL_UNAVAILABLE_AR in text


def test_both_routes_require_a_signed_in_user() -> None:
    expected = {"/plans/free-trial", "/plans/free-trial/claim"}
    found = set()
    for route in plans.router.routes:
        path = getattr(route, "path", "")
        if path in expected:
            found.add(path)
            deps = [d.call for d in route.dependant.dependencies]
            assert get_current_user in deps, path
    assert found == expected


def test_claim_shares_the_redeem_rate_limit() -> None:
    source = Path(rate_limit.__file__).read_text(encoding="utf-8")
    assert 'endswith("/plans/free-trial/claim")' in source


def test_events_are_on_the_allowlist() -> None:
    assert {"free_trial_shown", "free_trial_claimed"} <= PUBLIC_EVENT_NAMES
