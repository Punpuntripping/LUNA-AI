"""Parallel conversations — per-user cap + points reserve (migration 171).

Two independent guards, both exercised here:

  1. The CAP. message_service counts the user's OTHER live runs in
     ``_active_runs`` (unbound-but-fresh reservations and bound, not-done tasks;
     never stale reservations, finished tasks, other users, or the conversation
     being sent to). k + 1 > plans.max_parallel_runs for the EFFECTIVE plan →
     ``parallel_limit`` SSE event, nothing persisted but the unsent_messages row.
  2. The RESERVE. quota.check(inflight_runs=k): k == 0 keeps ``used >= limit``;
     k >= 1 needs ``PARALLEL_RESERVE_POINTS × (k + 1)`` points left in every
     limited ord window, and a reserve-only shortfall is tagged
     ``reason="parallel_reserve"`` (the user is NOT out of points).
"""
import asyncio
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from backend.app.services import message_service as ms
from shared import quota


USER = "aaaaaaaa-0000-0000-0000-000000000001"
OTHER_USER = "aaaaaaaa-0000-0000-0000-000000000009"
CONV = "bbbbbbbb-0000-0000-0000-000000000002"
CONTENT = "ما هي شروط الفصل التعسفي في نظام العمل السعودي؟"

PLANS = [
    {"plan_id": "free", "price_sar": None, "points_session": None,
     "points_weekly": None, "points_monthly": 5, "max_parallel_runs": 1},
    {"plan_id": "pro", "price_sar": 94.90, "points_session": 15,
     "points_weekly": 100, "points_monthly": None, "max_parallel_runs": 1},
    {"plan_id": "max", "price_sar": 289.90, "points_session": 75,
     "points_weekly": 500, "points_monthly": None, "max_parallel_runs": 5},
    {"plan_id": "dev", "price_sar": None, "points_session": None,
     "points_weekly": None, "points_monthly": None, "max_parallel_runs": 5},
]


# ── fakes ───────────────────────────────────────────────────────────────────

class _Res:
    def __init__(self, data: Any) -> None:
        self.data = data


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

    def execute(self) -> _Res:
        if self._table == "plans":
            self._fake.plan_reads += 1
            if self._fake.plans_fail:
                raise RuntimeError("simulated plans read failure")
            return _Res([dict(r) for r in self._fake.plans])
        return _Res([])


class _Rpc:
    def __init__(self, fake: "FakeSupabase") -> None:
        self._fake = fake

    def execute(self) -> _Res:
        row = self._fake.quota_row
        return _Res([] if row is None else [dict(row)])


class FakeSupabase:
    def __init__(self, quota_row: Optional[dict] = None, plans: Optional[list] = None) -> None:
        self.quota_row = quota_row
        self.plans = PLANS if plans is None else plans
        self.plans_fail = False
        self.plan_reads = 0
        self.inserts: dict[str, list[dict]] = {}

    def table(self, name: str) -> _Chain:
        return _Chain(self, name)

    def rpc(self, _name: str, _params: dict) -> _Rpc:
        return _Rpc(self)


def qrow(
    plan: str = "pro",
    *,
    effective: Optional[str] = None,
    session_limit: Optional[float] = None,
    session_used_pts: float = 0.0,
    weekly_limit: Optional[float] = None,
    weekly_used_pts: float = 0.0,
    monthly_limit: Optional[float] = None,
    monthly_used_pts: float = 0.0,
) -> dict:
    """A get_user_quota_state row. Costs are USD (points / 100)."""
    return {
        "locked": False,
        "plan_id": plan,
        "effective_plan_id": effective or plan,
        "points_session": session_limit,
        "session_cost": session_used_pts / quota.POINTS_PER_USD,
        "session_oldest": None,
        "points_weekly": weekly_limit,
        "weekly_cost": weekly_used_pts / quota.POINTS_PER_USD,
        "weekly_oldest": None,
        "points_monthly": monthly_limit,
        "monthly_cost": monthly_used_pts / quota.POINTS_PER_USD,
        "monthly_oldest": None,
        "ocr_pages_monthly": None,
        "ocr_pages": 0,
    }


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean_state():
    ms._active_runs.clear()
    quota._reset_parallel_caps_cache()
    yield
    ms._active_runs.clear()
    quota._reset_parallel_caps_cache()


class _Task:
    """Stand-in for an asyncio.Task — the counter only calls .done()."""

    def __init__(self, done: bool) -> None:
        self._done = done

    def done(self) -> bool:
        return self._done


def _seed(cid: str, *, user: str = USER, task: Any = None, age_s: float = 0.0) -> None:
    ms._active_runs[cid] = ms._ActiveRun(
        assistant_msg_id=f"asst-{cid}", user_id=user, task=task,
        reserved_at=time.monotonic() - age_s,
    )


# ── 1. the reserve math (shared/quota.check) ────────────────────────────────

def _check(fake: FakeSupabase, k: int) -> None:
    run(quota.check(None, fake, USER, inflight_runs=k))


@pytest.mark.parametrize("k", [0, 1, 2])
def test_reserve_19_points_left_admits_up_to_three(k):
    # 100-pt weekly window with 81 spent → 19 left. Needs 5, 10, 15: all fit.
    _check(FakeSupabase(qrow(weekly_limit=100, weekly_used_pts=81)), k)


def test_reserve_19_points_left_refuses_the_fourth():
    with pytest.raises(quota.QuotaExceeded) as ei:
        _check(FakeSupabase(qrow(weekly_limit=100, weekly_used_pts=81)), 3)  # needs 20
    qe = ei.value
    assert qe.reason == "parallel_reserve"
    assert qe.period == "weekly"
    payload = qe.to_event_payload()
    assert payload["reason"] == "parallel_reserve"
    assert payload["message_ar"] == quota.PARALLEL_RESERVE_AR


def test_k0_unchanged_with_3_points_left():
    # A single send keeps today's rule: 3 pts left is enough to send one.
    _check(FakeSupabase(qrow(weekly_limit=100, weekly_used_pts=97)), 0)
    # …but not next to a running conversation (needs 10).
    with pytest.raises(quota.QuotaExceeded) as ei:
        _check(FakeSupabase(qrow(weekly_limit=100, weekly_used_pts=97)), 1)
    assert ei.value.reason == "parallel_reserve"


def test_k0_spent_window_is_a_plain_limit_block():
    with pytest.raises(quota.QuotaExceeded) as ei:
        _check(FakeSupabase(qrow(weekly_limit=100, weekly_used_pts=100)), 0)
    assert ei.value.reason == "limit"
    assert ei.value.to_event_payload()["reason"] == "limit"


def test_spent_window_wins_over_reserve_shortfall():
    # session is genuinely spent, weekly only short of the reserve → "limit".
    fake = FakeSupabase(qrow(
        session_limit=15, session_used_pts=15, weekly_limit=100, weekly_used_pts=95,
    ))
    with pytest.raises(quota.QuotaExceeded) as ei:
        _check(fake, 2)
    assert ei.value.reason == "limit"
    assert ei.value.period == "session"


def test_null_limits_skip_the_reserve():
    # dev: every window unlimited → no reserve at any k.
    _check(FakeSupabase(qrow("dev")), 4)
    _check(FakeSupabase(qrow("dev")), 9)


def test_reserve_applies_to_every_limited_window():
    # free's only window is the 30-day one: 5-pt limit, 0 used → k=1 needs 10.
    with pytest.raises(quota.QuotaExceeded) as ei:
        _check(FakeSupabase(qrow("free", monthly_limit=5)), 1)
    assert ei.value.period == "monthly"
    assert ei.value.reason == "parallel_reserve"


# ── 2. parallel_cap + usage report ──────────────────────────────────────────

@pytest.mark.parametrize("plan,cap", [("free", 1), ("pro", 1), ("max", 5), ("dev", 5)])
def test_cap_per_plan(plan, cap):
    got, eff = run(quota.parallel_cap(FakeSupabase(qrow(plan)), USER))
    assert (got, eff) == (cap, plan)


def test_cap_reads_the_effective_plan():
    # An expired `max` falls back to free in the RPC → cap 1, not 5.
    got, eff = run(quota.parallel_cap(FakeSupabase(qrow("max", effective="free")), USER))
    assert (got, eff) == (1, "free")


def test_cap_unknown_plan_defaults_to_one():
    got, _ = run(quota.parallel_cap(FakeSupabase(qrow("marketing_x")), USER))
    assert got == quota.DEFAULT_MAX_PARALLEL_RUNS == 1


def test_cap_catalog_is_cached():
    fake = FakeSupabase(qrow("max"))
    run(quota.parallel_cap(fake, USER))
    run(quota.parallel_cap(fake, USER))
    assert fake.plan_reads == 1


def test_cap_fails_closed_on_catalog_failure():
    fake = FakeSupabase(qrow("max"))
    fake.plans_fail = True
    with pytest.raises(quota.QuotaUnavailable):
        run(quota.parallel_cap(fake, USER))


def test_cap_locked_account_is_plan_inactive():
    with pytest.raises(quota.PlanInactive):
        run(quota.parallel_cap(FakeSupabase({"locked": True}), USER))


def test_usage_report_carries_max_parallel_runs():
    report = run(quota.current_usage_report(None, FakeSupabase(qrow("max")), USER))
    assert report["max_parallel_runs"] == 5
    report = run(quota.current_usage_report(None, FakeSupabase(qrow("pro")), USER))
    assert report["max_parallel_runs"] == 1


def test_usage_report_survives_catalog_failure():
    fake = FakeSupabase(qrow("max"))
    fake.plans_fail = True
    report = run(quota.current_usage_report(None, fake, USER))
    assert report["max_parallel_runs"] == 1


def test_usage_report_locked_has_default_cap():
    report = run(quota.current_usage_report(None, FakeSupabase({"locked": True}), USER))
    assert report["max_parallel_runs"] == 1


# ── 3. counting live runs (message_service) ─────────────────────────────────

def test_count_includes_fresh_reservation_and_live_task():
    _seed("c-reserving")                            # task None, fresh
    _seed("c-running", task=_Task(done=False))      # bound, live (or detached)
    assert sorted(ms._user_inflight_runs(USER, CONV)) == ["c-reserving", "c-running"]
    assert ms._user_inflight_count(USER, CONV) == 2


def test_count_excludes_stale_done_other_user_and_same_conversation():
    _seed("c-stale", age_s=ms._RESERVATION_STALE_S + 5)
    _seed("c-done", task=_Task(done=True))
    _seed("c-other-user", user=OTHER_USER, task=_Task(done=False))
    _seed(CONV, task=_Task(done=False))             # the conversation being sent to
    assert ms._user_inflight_count(USER, CONV) == 0


# ── 4. the send path ────────────────────────────────────────────────────────

def _FakeRequest() -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None)))


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


def _parse(events: list[str], name: str) -> dict:
    import json
    for e in events:
        if f"event: {name}" in e:
            data = next(ln for ln in e.splitlines() if ln.startswith("data:"))
            return json.loads(data[len("data:"):].strip())
    raise AssertionError(f"no {name} event in {events!r}")


def test_refused_send_emits_parallel_limit_and_persists_nothing(monkeypatch):
    checks: list[int] = []

    async def _check(*_a: Any, **kw: Any):
        checks.append(kw.get("inflight_runs"))
    monkeypatch.setattr(quota, "check", _check)

    _seed("c-running", task=_Task(done=False))      # pro user, cap 1, one running
    fake = FakeSupabase(qrow("pro"))
    events = _send(fake)

    payload = _parse(events, "parallel_limit")
    assert payload == {
        "detail": "يمكنك تشغيل محادثة واحدة في الوقت نفسه. تتيح باقة «القصوى» حتى 5 محادثات متزامنة.",
        "limit": 1,
        "running": 1,
        "running_conversation_ids": ["c-running"],
        "upgrade_plan": "max",
    }
    # Refused before the meters: the gate never ran.
    assert checks == []
    # Nothing the thread can see — only the intent capture.
    assert fake.inserts.get("messages", []) == []
    assert fake.inserts.get("message_attachments", []) == []
    rows = fake.inserts.get("unsent_messages", [])
    assert len(rows) == 1
    assert rows[0]["reason"] == "parallel_limit"
    assert rows[0]["content"] == CONTENT
    assert rows[0]["plan_id"] == "pro"
    # The slot is released; the other run is untouched.
    assert CONV not in ms._active_runs
    assert "c-running" in ms._active_runs


def test_max_at_cap_gets_wait_copy_and_no_upsell(monkeypatch):
    async def _check(*_a: Any, **_kw: Any):
        raise AssertionError("gate must not run past the cap")
    monkeypatch.setattr(quota, "check", _check)

    for i in range(5):
        _seed(f"c-{i}", task=_Task(done=False))
    events = _send(FakeSupabase(qrow("max")))

    payload = _parse(events, "parallel_limit")
    assert payload["detail"] == "وصلت إلى الحدّ الأقصى (5 محادثات جارية). انتظر اكتمال إحداها."
    assert payload["limit"] == 5
    assert payload["running"] == 5
    assert payload["upgrade_plan"] is None


def test_under_cap_passes_k_into_the_gate(monkeypatch):
    seen: list[int] = []

    async def _blocked(*_a: Any, **kw: Any):
        seen.append(kw.get("inflight_runs"))
        # Stop the send here so nothing past the gate is exercised.
        raise quota.QuotaExceeded(
            meter="ord", period="weekly", used=90.0, limit=100.0,
            resets_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
            plan_id="max", reason="parallel_reserve",
        )
    monkeypatch.setattr(quota, "check", _blocked)

    _seed("c-0", task=_Task(done=False))
    _seed("c-1")                                    # fresh reservation counts too
    _seed("c-stale", age_s=ms._RESERVATION_STALE_S + 1)
    events = _send(fake := FakeSupabase(qrow("max")))

    assert seen == [2]
    payload = _parse(events, "quota_exceeded")
    assert payload["reason"] == "parallel_reserve"
    assert not any("event: parallel_limit" in e for e in events)
    assert fake.inserts.get("messages", []) == []
    assert CONV not in ms._active_runs


def test_lone_send_skips_the_cap_read(monkeypatch):
    seen: list[int] = []

    async def _blocked(*_a: Any, **kw: Any):
        seen.append(kw.get("inflight_runs"))
        raise quota.PlanInactive()
    monkeypatch.setattr(quota, "check", _blocked)

    async def _no_cap(*_a: Any, **_kw: Any):
        raise AssertionError("cap must not be read for k == 0")
    monkeypatch.setattr(quota, "parallel_cap", _no_cap)

    _seed("c-other", user=OTHER_USER, task=_Task(done=False))
    _send(FakeSupabase(qrow("pro")))
    assert seen == [0]


def test_cap_unavailable_fails_closed(monkeypatch):
    async def _check(*_a: Any, **_kw: Any):
        raise AssertionError("gate must not run when the cap is unknowable")
    monkeypatch.setattr(quota, "check", _check)

    _seed("c-running", task=_Task(done=False))
    fake = FakeSupabase(qrow("max"))
    fake.plans_fail = True
    events = _send(fake)

    assert any("QUOTA_UNAVAILABLE" in e for e in events)
    assert fake.inserts.get("messages", []) == []
    assert fake.inserts["unsent_messages"][0]["reason"] == "quota_unavailable"
    assert CONV not in ms._active_runs
