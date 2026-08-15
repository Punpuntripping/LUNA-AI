"""The quota gate captures refused sends into `unsent_messages` (migration 135).

Two properties, and the second is the whole reason the table exists:

  1. Each of the three block paths records the text the user tried to send,
     tagged with why it was refused.
  2. A blocked send still writes NOTHING to `messages`. That ordering is what
     keeps an unanswered user row out of the thread and out of
     `context_service`'s history — the bug documented at message_service §0c.
     A regression that "helpfully" saved the user row would pass test 1 and
     fail here.
"""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.services import message_service as ms
from shared import quota


USER = "aaaaaaaa-0000-0000-0000-000000000001"
CONV = "bbbbbbbb-0000-0000-0000-000000000002"
CONTENT = "ما هي شروط الفصل التعسفي في نظام العمل السعودي؟"
RESETS_AT = datetime(2026, 9, 1, tzinfo=timezone.utc)


# ── fake supabase: records inserts per table, nothing else ──────────────────

class _Chain:
    def __init__(self, fake: "FakeSupabase", table: str) -> None:
        self._fake = fake
        self._table = table

    def insert(self, row: Any) -> "_Chain":
        self._fake.inserts.setdefault(self._table, []).append(row)
        return self

    def select(self, *_a: Any, **_kw: Any) -> "_Chain":
        return self

    def eq(self, *_a: Any) -> "_Chain":
        return self

    def execute(self) -> Any:
        return type("R", (), {"data": []})()


class FakeSupabase:
    def __init__(self) -> None:
        self.inserts: dict[str, list[dict]] = {}

    def table(self, name: str) -> _Chain:
        return _Chain(self, name)


def _FakeRequest() -> SimpleNamespace:
    """Only `.app.state.redis` is touched before the gate raises."""
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None)))


@pytest.fixture(autouse=True)
def _clear_active_runs():
    ms._active_runs.clear()
    yield
    ms._active_runs.clear()


def run(coro):
    return asyncio.run(coro)


async def _drain(gen) -> list[str]:
    return [chunk async for chunk in gen]


def _send(fake: FakeSupabase) -> list[str]:
    return run(_drain(ms.send_message_stream(
        fake,
        user_id=USER,
        conversation_id=CONV,
        conv={"conversation_id": CONV, "case_id": None},
        content=CONTENT,
        request=_FakeRequest(),
        attachment_ids=None,
    )))


# ── the three block paths ───────────────────────────────────────────────────

def test_quota_exceeded_records_intent(monkeypatch):
    async def _blocked(*_a: Any, **_kw: Any):
        raise quota.QuotaExceeded(
            meter="ord", period="monthly", used=5.0, limit=5.0,
            resets_at=RESETS_AT, plan_id="free", upgrade_options=["pro"],
        )
    monkeypatch.setattr(quota, "check", _blocked)

    events = _send(fake := FakeSupabase())

    rows = fake.inserts.get("unsent_messages", [])
    assert len(rows) == 1
    row = rows[0]
    assert row["content"] == CONTENT
    assert row["reason"] == "quota_exceeded"
    assert row["user_id"] == USER
    assert row["conversation_id"] == CONV
    # The block context the analysis actually filters on.
    assert row["plan_id"] == "free"
    assert row["meter"] == "ord"
    assert row["period"] == "monthly"
    assert row["used_amount"] == 5.0
    assert row["limit_amount"] == 5.0

    # The user still gets their block event — capture is not on the happy path.
    assert any("quota_exceeded" in e for e in events)


def test_plan_inactive_records_intent(monkeypatch):
    async def _blocked(*_a: Any, **_kw: Any):
        raise quota.PlanInactive()
    monkeypatch.setattr(quota, "check", _blocked)

    _send(fake := FakeSupabase())

    rows = fake.inserts.get("unsent_messages", [])
    assert len(rows) == 1
    assert rows[0]["reason"] == "plan_inactive"
    assert rows[0]["content"] == CONTENT
    # No plan and no window exist on this path — the columns stay empty rather
    # than inventing a 'free' that would pollute the free-user query.
    assert rows[0]["plan_id"] is None
    assert rows[0]["limit_amount"] is None


def test_quota_unavailable_records_intent(monkeypatch):
    async def _blocked(*_a: Any, **_kw: Any):
        raise quota.QuotaUnavailable(meter="ord", period="monthly")
    monkeypatch.setattr(quota, "check", _blocked)

    _send(fake := FakeSupabase())

    rows = fake.inserts.get("unsent_messages", [])
    assert len(rows) == 1
    assert rows[0]["reason"] == "quota_unavailable"
    assert rows[0]["content"] == CONTENT
    assert rows[0]["meter"] == "ord"


# ── the property the separate table exists to guarantee ─────────────────────

@pytest.mark.parametrize("exc", [
    quota.QuotaExceeded(
        meter="ord", period="monthly", used=5.0, limit=5.0,
        resets_at=RESETS_AT, plan_id="free",
    ),
    quota.PlanInactive(),
    quota.QuotaUnavailable(meter="ord", period="monthly"),
])
def test_blocked_send_writes_nothing_to_messages(monkeypatch, exc):
    """No user row, no assistant placeholder — on ANY block path.

    If this fails, the thread has an unanswered user turn again and
    `context_service` will feed it to the next request as a second consecutive
    user message. Capturing intent must never cost us this.
    """
    async def _blocked(*_a: Any, **_kw: Any):
        raise exc
    monkeypatch.setattr(quota, "check", _blocked)

    _send(fake := FakeSupabase())

    # Anchor: proves we actually reached and tripped the gate, so the two
    # emptiness assertions below can never pass vacuously.
    assert len(fake.inserts.get("unsent_messages", [])) == 1

    assert fake.inserts.get("messages", []) == []
    assert fake.inserts.get("message_attachments", []) == []


def test_capture_failure_does_not_break_the_block(monkeypatch):
    """A dead `unsent_messages` insert must not turn a quota block into an
    error. The user's composer re-hydration depends on the `quota_exceeded`
    event arriving regardless."""
    async def _blocked(*_a: Any, **_kw: Any):
        raise quota.QuotaExceeded(
            meter="ord", period="monthly", used=5.0, limit=5.0,
            resets_at=RESETS_AT, plan_id="free",
        )
    monkeypatch.setattr(quota, "check", _blocked)

    class ExplodingSupabase(FakeSupabase):
        def table(self, name: str):
            if name == "unsent_messages":
                raise RuntimeError("relation does not exist")
            return super().table(name)

    events = _send(ExplodingSupabase())

    assert any("quota_exceeded" in e for e in events)
    assert not any('"error"' in e or "event: error" in e for e in events)


# ── slot hygiene ────────────────────────────────────────────────────────────

def test_block_releases_the_inflight_slot(monkeypatch):
    """The per-conversation dedup slot is reserved before the gate runs. A
    blocked send must release it, or the conversation is locked until restart."""
    async def _blocked(*_a: Any, **_kw: Any):
        raise quota.QuotaExceeded(
            meter="ord", period="monthly", used=5.0, limit=5.0,
            resets_at=RESETS_AT, plan_id="free",
        )
    monkeypatch.setattr(quota, "check", _blocked)

    _send(FakeSupabase())

    assert CONV not in ms._active_runs
