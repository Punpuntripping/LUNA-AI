"""Deep-search plan gate (migration 197, .claude/plans/deep_search_plan_gate.md).

Owner rules 2026-10-30: free = 1 deep search per rolling 30 days AND none with
< 2 points left; basic = none with ≤ 1 point left (min 1.01 under the strict
`remaining < min` rule); pro / max / dev / marketing_* = no gate.

Pinned here:
  1. shared/quota — the thresholds per plan, the basic boundary (1.00 blocked,
     1.01 allowed), the free count (read only when the plan caps it), NULL = no
     gate, the effective plan (expired basic → free), fail-open reads, the
     ladder, the copy (Latin digits).
  2. the four orchestrator entry points — router dispatch, the simple_search
     fallback (fresh + resume), the paused deep_search resume (its pause row is
     RESOLVED) — and the editorial pin's exemption.
  3. message_service — the gate rides into handle_message; `deep_search_gated`
     is relayed; the refusal is saved as the assistant message.
  4. the two ledger agent names the count RPC depends on.

No network, no LLM, no pytest-asyncio (CI runs without it): every coroutine is
driven with asyncio.run.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest

import agents.orchestrator as orch
import agents.simple_search.runner as ss_runner
import agents.utils.welcome as welcome_mod
from agents.models import SpecialistResult
from backend.app.services import message_service as ms
from shared import quota

USER = "aaaaaaaa-0000-0000-0000-000000000001"
CONV = "bbbbbbbb-0000-0000-0000-000000000002"
REPO = Path(__file__).resolve().parents[2]
ARABIC_INDIC = re.compile(r"[٠-٩۰-۹]")

# The live catalog shape (prod, 2026-10-30) + the values 197 seeds.
PLANS = [
    {"plan_id": "free", "price_sar": None, "points_session": None, "points_weekly": None,
     "points_monthly": 5, "max_parallel_runs": 1,
     "deep_search_min_points": 2, "deep_search_per_window": 1},
    {"plan_id": "basic", "price_sar": "39.90", "points_session": 15, "points_weekly": 75,
     "points_monthly": None, "max_parallel_runs": 1,
     "deep_search_min_points": "1.01", "deep_search_per_window": None},
    {"plan_id": "pro", "price_sar": "94.90", "points_session": 20, "points_weekly": 100,
     "points_monthly": None, "max_parallel_runs": 1,
     "deep_search_min_points": None, "deep_search_per_window": None},
    {"plan_id": "max", "price_sar": "289.90", "points_session": 100, "points_weekly": 500,
     "points_monthly": None, "max_parallel_runs": 5,
     "deep_search_min_points": None, "deep_search_per_window": None},
    {"plan_id": "dev", "price_sar": None, "points_session": None, "points_weekly": None,
     "points_monthly": None, "max_parallel_runs": 5,
     "deep_search_min_points": None, "deep_search_per_window": None},
    {"plan_id": "marketing_lawyer", "price_sar": None, "points_session": 15,
     "points_weekly": 74, "points_monthly": None, "max_parallel_runs": 1,
     "deep_search_min_points": None, "deep_search_per_window": None},
]
# A backend deployed before 197: the catalog has no gate columns at all.
PLANS_PRE_197 = [
    {k: v for k, v in row.items() if not k.startswith("deep_search_")} for row in PLANS
]


# ── fakes ───────────────────────────────────────────────────────────────────

class _Res:
    def __init__(self, data: Any) -> None:
        self.data = data


class _Chain:
    def __init__(self, fake: "FakeSupabase", table: str) -> None:
        self._fake = fake
        self._table = table

    def __getattr__(self, _name: str):
        return lambda *a, **k: self          # select / eq / order / insert / …

    def execute(self) -> _Res:
        if self._table == "plans":
            self._fake.plan_reads += 1
            if self._fake.plans_fail:
                raise RuntimeError("simulated plans read failure")
            return _Res([dict(r) for r in self._fake.plans])
        return _Res([])


class _Rpc:
    def __init__(self, fake: "FakeSupabase", name: str, params: dict) -> None:
        self._fake, self._name, self._params = fake, name, params

    def execute(self) -> _Res:
        self._fake.rpc_calls.append((self._name, dict(self._params)))
        if self._name == "count_user_deep_searches":
            if self._fake.count_fail:
                raise RuntimeError("simulated count failure")
            starts = list(self._fake.run_starts)
            return _Res([{"runs": len(starts), "run_starts": starts}])
        row = self._fake.quota_row
        return _Res([] if row is None else [dict(row)])


class FakeSupabase:
    def __init__(
        self,
        quota_row: Optional[dict] = None,
        *,
        plans: Optional[list] = None,
        run_starts: Optional[list[str]] = None,
    ) -> None:
        self.quota_row = quota_row
        self.plans = PLANS if plans is None else plans
        self.run_starts = run_starts or []
        self.plans_fail = False
        self.count_fail = False
        self.plan_reads = 0
        self.rpc_calls: list[tuple[str, dict]] = []

    def table(self, name: str) -> _Chain:
        return _Chain(self, name)

    def rpc(self, name: str, params: dict) -> _Rpc:
        return _Rpc(self, name, params)

    def count_reads(self) -> int:
        return sum(1 for n, _ in self.rpc_calls if n == "count_user_deep_searches")


def qrow(
    plan: str,
    *,
    effective: Optional[str] = None,
    session: tuple[Optional[float], float] = (None, 0.0),
    weekly: tuple[Optional[float], float] = (None, 0.0),
    monthly: tuple[Optional[float], float] = (None, 0.0),
    session_oldest: Optional[str] = None,
    weekly_oldest: Optional[str] = None,
) -> dict:
    """A get_user_quota_state row; each window is (limit, used points)."""
    return {
        "locked": False,
        "plan_id": plan,
        "effective_plan_id": effective or plan,
        "points_session": session[0],
        "session_cost": session[1] / quota.POINTS_PER_USD,
        "session_oldest": session_oldest,
        "points_weekly": weekly[0],
        "weekly_cost": weekly[1] / quota.POINTS_PER_USD,
        "weekly_oldest": weekly_oldest,
        "points_monthly": monthly[0],
        "monthly_cost": monthly[1] / quota.POINTS_PER_USD,
        "monthly_oldest": None,
        "ocr_pages_monthly": None,
        "ocr_pages": 0,
    }


def free_row(used_pts: float = 0.0, **kw: Any) -> dict:
    return qrow("free", monthly=(5, used_pts), **kw)


def basic_row(session_used: float = 0.0, weekly_used: float = 0.0, **kw: Any) -> dict:
    return qrow("basic", session=(15, session_used), weekly=(75, weekly_used), **kw)


def run(coro):
    return asyncio.run(coro)


def gate_and_block(fake: FakeSupabase) -> tuple[Any, Any]:
    gate = run(quota.check(None, fake, USER))
    return gate, run(quota.deep_search_block(fake, USER, gate))


@pytest.fixture(autouse=True)
def _clean_state():
    quota._reset_parallel_caps_cache()
    ms._active_runs.clear()
    yield
    quota._reset_parallel_caps_cache()
    ms._active_runs.clear()


# ── 1. shared/quota ─────────────────────────────────────────────────────────

def test_free_first_search_with_points_is_allowed():
    fake = FakeSupabase(free_row(used_pts=1.0))           # 4 left, 0 runs
    gate, block = gate_and_block(fake)
    assert gate.plan_id == "free" and gate.active
    assert (gate.min_points, gate.per_window) == (2.0, 1)
    assert gate.remaining == pytest.approx(4.0)
    assert block is None
    assert fake.count_reads() == 1


def test_free_second_search_in_the_window_is_refused():
    started = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
    fake = FakeSupabase(free_row(used_pts=2.7), run_starts=[started.isoformat()])
    gate, block = gate_and_block(fake)
    assert block is not None and block.reason == "free_limit"
    assert (block.used, block.limit) == (1, 1)
    # The count frees up when that run ages out of the 30-day window.
    assert block.resets_at == started + timedelta(days=30)
    # Every paid plan clears it (NULL per_window = no cap), cheapest first.
    assert block.upgrade_options == ["basic", "pro", "max"]
    payload = block.to_event_payload()
    assert set(payload) == {"reason", "plan_id", "upgrade_options", "resets_at", "message_ar"}
    assert payload["plan_id"] == "free"
    assert payload["resets_at"] == (started + timedelta(days=30)).isoformat()
    # The window floor the RPC gets: now − 30 days (it adds usage_reset_at itself).
    (_, params), = [c for c in fake.rpc_calls if c[0] == "count_user_deep_searches"]
    since = datetime.fromisoformat(params["p_since"])
    assert abs((datetime.now(timezone.utc) - since) - timedelta(days=30)) < timedelta(minutes=1)
    assert params["p_user_id"] == USER


def test_legacy_overrun_frees_up_when_the_newest_counted_run_ages_out():
    # 16 free users ran 2–3 searches in the last 30 days before the gate.
    starts = ["2026-10-02T10:00:00+00:00", "2026-10-09T10:00:00+00:00",
              "2026-10-15T10:00:00+00:00"]
    _gate, block = gate_and_block(FakeSupabase(free_row(used_pts=1.0), run_starts=starts))
    assert block.reason == "free_limit" and block.used == 3
    assert block.resets_at == datetime(2026, 11, 14, 10, tzinfo=timezone.utc)


@pytest.mark.parametrize("used,blocked", [(3.01, True), (3.0, False), (0.0, False)])
def test_free_two_point_floor(used, blocked):
    # 5-pt window: 1.99 left blocks, exactly 2.00 left passes (`remaining < 2`).
    _gate, block = gate_and_block(FakeSupabase(free_row(used_pts=used)))
    if blocked:
        assert block is not None and block.reason == "low_points"
        assert block.upgrade_options == ["basic", "pro", "max"]
    else:
        assert block is None


def test_free_limit_wins_when_both_rules_apply():
    fake = FakeSupabase(free_row(used_pts=4.0), run_starts=["2026-10-20T09:00:00+00:00"])
    _gate, block = gate_and_block(fake)
    assert block.reason == "free_limit"


@pytest.mark.parametrize("session_used,blocked", [(14.0, True), (13.99, False), (14.5, True)])
def test_basic_boundary_one_point_left_is_blocked(session_used, blocked):
    # 15-pt session: 1.00 left → blocked; 1.01 left → allowed (min 1.01, `<`).
    fake = FakeSupabase(basic_row(session_used=session_used))
    _gate, block = gate_and_block(fake)
    assert (block is not None) is blocked
    if blocked:
        assert block.reason == "low_points"
        assert block.plan_id == "basic"
        # Pro (20) and Max (100) raise the session limit that binds.
        assert block.upgrade_options == ["pro", "max"]
    # Basic has no count cap — the count RPC is never read for it.
    assert fake.count_reads() == 0


def test_basic_reports_the_window_that_binds_longest():
    now = datetime.now(timezone.utc)
    session_oldest = (now - timedelta(hours=1)).isoformat()      # resets in ~4h
    weekly_oldest = (now - timedelta(days=2)).isoformat()        # resets in ~5d
    fake = FakeSupabase(basic_row(
        session_used=14.5, weekly_used=74.5,
        session_oldest=session_oldest, weekly_oldest=weekly_oldest,
    ))
    _gate, block = gate_and_block(fake)
    assert block.reason == "low_points"
    # Both windows are under the floor: the session coming back does not help.
    assert block.resets_at == datetime.fromisoformat(weekly_oldest) + timedelta(days=7)


def test_basic_weekly_under_the_floor_blocks_even_with_session_points():
    fake = FakeSupabase(basic_row(session_used=0.0, weekly_used=74.0))
    _gate, block = gate_and_block(fake)
    assert block is not None and block.reason == "low_points"


@pytest.mark.parametrize("plan,row", [
    ("pro", qrow("pro", session=(20, 19.9), weekly=(100, 99.9))),
    ("max", qrow("max", session=(100, 99.9), weekly=(500, 499.9))),
    ("dev", qrow("dev")),
    ("marketing_lawyer", qrow("marketing_lawyer", session=(15, 14.9), weekly=(74, 10))),
])
def test_null_columns_mean_no_gate(plan, row):
    fake = FakeSupabase(row)
    gate, block = gate_and_block(fake)
    assert gate.plan_id == plan and not gate.active
    assert block is None
    assert fake.count_reads() == 0


def test_expired_basic_gets_the_free_rules():
    # The RPC falls an expired basic back to free; the gate keys off that.
    row = qrow("basic", effective="free", monthly=(5, 0.0))
    gate, block = gate_and_block(FakeSupabase(row, run_starts=["2026-10-20T09:00:00+00:00"]))
    assert gate.plan_id == "free"
    assert block.reason == "free_limit"


def test_backend_ahead_of_197_runs_ungated():
    fake = FakeSupabase(free_row(), plans=PLANS_PRE_197, run_starts=["2026-10-20T09:00:00+00:00"])
    gate, block = gate_and_block(fake)
    assert gate is not None and not gate.active
    assert block is None
    assert fake.count_reads() == 0


def test_unreadable_catalog_leaves_the_gate_open_but_the_send_admitted():
    fake = FakeSupabase(free_row())
    fake.plans_fail = True
    assert run(quota.check(None, fake, USER)) is None
    assert run(quota.deep_search_block(fake, USER, None)) is None


def test_failed_count_read_fails_open():
    fake = FakeSupabase(free_row(), run_starts=["2026-10-20T09:00:00+00:00"])
    fake.count_fail = True
    _gate, block = gate_and_block(fake)
    assert block is None


def test_check_still_fails_closed_on_its_own_state_read():
    class _Down(FakeSupabase):
        def rpc(self, name: str, params: dict):
            raise RuntimeError("rpc down")

    with pytest.raises(quota.QuotaUnavailable):
        run(quota.check(None, _Down(free_row()), USER))


def test_the_catalog_read_is_shared_with_the_parallel_cap():
    fake = FakeSupabase(free_row())
    run(quota.check(None, fake, USER))
    run(quota.parallel_cap(fake, USER))
    run(quota.check(None, fake, USER))
    assert fake.plan_reads == 1


@pytest.mark.parametrize("reason", ["free_limit", "low_points"])
@pytest.mark.parametrize("plan", ["free", "basic"])
def test_copy_is_arabic_with_latin_digits(reason, plan):
    block = quota.DeepSearchGated(
        reason=reason, plan_id=plan, resets_at=None, remaining=0.5, used=1, limit=1,
    )
    for text in (block.reply_ar, block.message_ar):
        assert text.strip() and not ARABIC_INDIC.search(text)
        assert "{" not in text                              # every slot filled
    if reason == "free_limit":
        assert "1 بحث معمّق كل 30 يوماً" in block.reply_ar
    assert ("الاشتراك" in block.reply_ar) is (plan == "free")


def test_the_contextvar_defaults_to_no_gate():
    assert quota.active_deep_search_gate() is None
    gate = run(quota.check(None, FakeSupabase(free_row()), USER))
    token = quota.set_deep_search_gate(gate)
    try:
        assert quota.active_deep_search_gate() is gate
    finally:
        quota.reset_deep_search_gate(token)
    assert quota.active_deep_search_gate() is None


# ── 2. the orchestrator entry points ────────────────────────────────────────

_FREE_GATE = quota.DeepSearchGate(
    plan_id="free", min_points=2.0, per_window=1,
    windows=(("monthly", 2.3, datetime(2026, 11, 20, tzinfo=timezone.utc)),),
    window_start=datetime(2026, 9, 30, tzinfo=timezone.utc),
)
_BLOCK = quota.DeepSearchGated(
    reason="free_limit", plan_id="free",
    resets_at=datetime(2026, 11, 19, 9, tzinfo=timezone.utc),
    remaining=2.3, used=1, limit=1, upgrade_options=["basic", "pro", "max"],
)


class _OrchSupabase:
    """Records table writes; empty reads."""

    def __init__(self) -> None:
        self.updates: list[tuple[str, dict]] = []

    def table(self, name: str) -> "_OrchTable":
        return _OrchTable(self, name)


class _OrchTable:
    def __init__(self, db: _OrchSupabase, name: str) -> None:
        self._db, self._name = db, name

    def update(self, payload: dict) -> "_OrchTable":
        self._db.updates.append((self._name, dict(payload)))
        return self

    def __getattr__(self, _name: str):
        return lambda *a, **k: self

    def execute(self) -> Any:
        return SimpleNamespace(data=[])


@pytest.fixture
def env(monkeypatch):
    """Neutralise the DB reads around the families; record what matters."""
    monkeypatch.setattr(orch, "_count_artifact_kinds", lambda *a, **k: 0)
    monkeypatch.setattr(orch, "_load_attached_items", lambda *a, **k: [])
    monkeypatch.setattr(orch, "_load_recent_messages", lambda *a, **k: [])
    monkeypatch.setattr(orch, "_load_compaction_summary", lambda *a, **k: None)
    monkeypatch.setattr(orch, "_load_reply_attachment_ids", lambda *a, **k: [])

    state: dict[str, Any] = {"resolved": [], "ds_calls": 0, "gate_calls": 0,
                             "welcomed": [], "logged": []}

    def _resolve(_supabase, run_id, *, where):
        state["resolved"].append((str(run_id), where))
        return True

    async def _fake_ds(*_a, **_k):
        state["ds_calls"] += 1
        return orch._DeepSearchOutcome(
            kind="completed",
            result=SpecialistResult(chat_summary="جواب البحث المعمّق", model_used="qwen"),
        )

    async def _block(_supabase, _user_id, gate):
        state["gate_calls"] += 1
        return _BLOCK if gate is not None and gate.active else None

    monkeypatch.setattr(orch, "_resolve_pause_loud", _resolve)
    monkeypatch.setattr(orch, "_run_deep_search", _fake_ds)
    monkeypatch.setattr(quota, "deep_search_block", _block)
    monkeypatch.setattr(welcome_mod, "mark_welcomed",
                        lambda _s, _u, w: state["welcomed"].append(w))
    monkeypatch.setattr(orch._logfire, "info",
                        lambda name, **kw: state["logged"].append((name, kw)), raising=False)
    return state


def _collect(make_gen, gate: Any = _FREE_GATE) -> list[dict]:
    async def _go() -> list[dict]:
        token = quota.set_deep_search_gate(gate)
        try:
            return [ev async for ev in make_gen()]
        finally:
            quota.reset_deep_search_gate(token)
    return run(_go())


def _types(events: list[dict]) -> list[str]:
    return [e.get("type") for e in events]


def _dispatch(family: str, db: _OrchSupabase, **kw: Any):
    return lambda: orch._dispatch(
        family, "ما شروط الفصل التعسفي؟", "الفصل التعسفي", None, [], None,
        db, USER, CONV, None, **kw,
    )


def _assert_refusal(events: list[dict]) -> None:
    gated = next(e for e in events if e["type"] == "deep_search_gated")
    assert {k: gated[k] for k in ("reason", "plan_id", "upgrade_options")} == {
        "reason": "free_limit", "plan_id": "free", "upgrade_options": ["basic", "pro", "max"],
    }
    assert gated["resets_at"] == "2026-11-19T09:00:00+00:00"
    assert gated["message_ar"] == quota.DEEP_SEARCH_GATED_MESSAGE_AR["free_limit"]
    tokens = [e["text"] for e in events if e["type"] == "token"]
    assert tokens == [_BLOCK.reply_ar]
    # The event comes first so the client can offer the upgrade beside the text.
    assert _types(events).index("deep_search_gated") < _types(events).index("token")
    done = next(e for e in events if e["type"] == "done")
    assert done["usage"]["model"] == "deep_search_gated"


def test_router_dispatch_is_refused_before_the_run_starts(env):
    events = _collect(_dispatch("deep_search", _OrchSupabase()))
    assert _types(events) == ["deep_search_gated", "token", "done"]
    _assert_refusal(events)
    assert env["ds_calls"] == 0                      # no planner, no progress bar
    assert [n for n, _ in env["logged"]] == ["deep_search.gated"]
    _, kw = env["logged"][0]
    assert (kw["entry"], kw["reason"], kw["plan_id"], kw["remaining"]) == (
        "dispatch", "free_limit", "free", 2.3,
    )
    assert "user_id" not in kw


def test_router_dispatch_runs_when_the_gate_allows(env, monkeypatch):
    async def _allow(*_a, **_k):
        return None

    monkeypatch.setattr(quota, "deep_search_block", _allow)
    events = _collect(_dispatch("deep_search", _OrchSupabase()))
    assert env["ds_calls"] == 1
    assert "deep_search_gated" not in _types(events)
    assert _types(events)[0] == "agent_run_started"


def test_no_gate_on_the_contextvar_means_no_gate(env):
    events = _collect(_dispatch("deep_search", _OrchSupabase()), gate=None)
    assert env["ds_calls"] == 1 and env["gate_calls"] == 0
    assert "deep_search_gated" not in _types(events)


def test_editorial_pin_is_exempt(env):
    from agents.deep_search_v4.planner.models import PinnedPlan

    events = _collect(_dispatch("deep_search", _OrchSupabase(), pinned_plan=PinnedPlan()))
    assert env["ds_calls"] == 1
    assert env["gate_calls"] == 0                    # never even asked
    assert "deep_search_gated" not in _types(events)


def test_other_families_are_never_gated(env, monkeypatch):
    async def fake_ss(*_a, **_k):
        return SimpleNamespace(
            chat_messages=["الجواب"], created_item_ids=[], sse_events=[], paused=False,
            question_text=None, aborted=False, abort_reason="", fallback_reason="",
        )

    monkeypatch.setattr(orch, "run_simple_search", fake_ss)
    events = _collect(_dispatch("simple_search", _OrchSupabase()))
    assert env["gate_calls"] == 0
    assert "deep_search_gated" not in _types(events)


def test_simple_search_fallback_is_refused(env, monkeypatch):
    async def aborted(*_a, **_k):
        return SimpleNamespace(
            chat_messages=[], created_item_ids=[], sse_events=[], paused=False,
            question_text=None, aborted=True, abort_reason="سؤال مقارن",
            fallback_reason="",
        )

    monkeypatch.setattr(orch, "run_simple_search", aborted)
    welcome = welcome_mod.WelcomeState("user_first", "أسعد", "legal")
    events = _collect(_dispatch("simple_search", _OrchSupabase(), welcome=welcome))
    assert _types(events) == [
        "agent_run_started", "deep_search_gated", "token", "done", "agent_run_finished",
    ]
    _assert_refusal(events)
    assert env["ds_calls"] == 0
    # The refusal carried no greeting — the welcome survives to the next answer.
    assert all(w is None for w in env["welcomed"])
    assert ("deep_search.gated", "ss_fallback") in [
        (n, kw.get("entry")) for n, kw in env["logged"]
    ]


def _ss_pause_row() -> dict:
    return {"run_id": "run-ss-1", "agent_family": "simple_search",
            "question_text": "أي حكم تقصد؟", "pause_reason": "clarify", "task_label": "بحث"}


def test_simple_search_resume_fallback_is_refused_and_consumes_its_pause(env, monkeypatch):
    async def aborted(*_a, **_k):
        return SimpleNamespace(
            chat_messages=[], created_item_ids=[], sse_events=[], paused=False,
            question_text=None, aborted=True, abort_reason="خارج النطاق",
            fallback_reason="",
        )

    monkeypatch.setattr(ss_runner, "resume_simple_search", aborted, raising=False)
    db = _OrchSupabase()
    events = _collect(lambda: orch._resume_major_agent(
        pending=_ss_pause_row(), user_reply="قارن بين الحكمين", supabase=db,
        user_id=USER, conversation_id=CONV, case_id=None, user_message_id="um-1",
    ))
    assert _types(events) == [
        "agent_resumed", "agent_run_started", "deep_search_gated", "token", "done",
        "agent_run_finished",
    ]
    _assert_refusal(events)
    assert env["ds_calls"] == 0
    assert env["resolved"] == [("run-ss-1", "ss_resume_finally")]


def _ds_pause_row() -> dict:
    return {
        "run_id": "run-ds-1", "agent_family": "deep_search",
        "question_text": "هل العقد محدد المدة؟", "pause_reason": "clarify",
        "task_label": "الفصل التعسفي",
        "message_history": b"[]",
        "deferred_payload": {"tool_call_id": "call-1"},
    }


def test_paused_deep_search_resume_is_refused_and_its_pause_row_resolved(env, monkeypatch):
    import agents.deep_search_v4.planner.agent as planner_agent

    def _no_decider(*_a, **_k):
        raise AssertionError("the planner must not resume behind a refusal")

    monkeypatch.setattr(planner_agent, "create_planner_decider", _no_decider)
    db = _OrchSupabase()
    events = _collect(lambda: orch._resume_major_agent(
        pending=_ds_pause_row(), user_reply="نعم، محدد المدة", supabase=db,
        user_id=USER, conversation_id=CONV, case_id=None, user_message_id="um-1",
    ))
    assert _types(events) == [
        "agent_resumed", "deep_search_gated", "token", "done", "agent_run_finished",
    ]
    _assert_refusal(events)
    # Resolved exactly once — left open, every next message would resume it.
    assert env["resolved"] == [("run-ds-1", "deep_search_gated")]
    assert ("deep_search.gated", "ds_resume") in [
        (n, kw.get("entry")) for n, kw in env["logged"]
    ]


def test_handle_message_publishes_and_resets_the_gate(monkeypatch):
    seen: list[Any] = []

    async def _inner(**_kw):
        seen.append(quota.active_deep_search_gate())
        yield {"type": "done", "usage": {}}

    monkeypatch.setattr(orch, "_handle_message_inner", _inner)

    async def _go(gate):
        return [ev async for ev in orch.handle_message(
            question="سؤال", user_id=USER, conversation_id=CONV,
            supabase=_OrchSupabase(), codec=object(), deep_search_gate=gate,
        )]

    run(_go(_FREE_GATE))
    run(_go(None))                                   # the headless front door
    assert seen == [_FREE_GATE, None]
    assert quota.active_deep_search_gate() is None


# ── 3. message_service ──────────────────────────────────────────────────────

class _MsChain:
    def __init__(self, fake: "_MsSupabase", table: str) -> None:
        self._fake, self._table, self._update = fake, table, None

    def update(self, payload: dict) -> "_MsChain":
        self._update = payload
        return self

    def __getattr__(self, _name: str):
        return lambda *a, **k: self

    def execute(self) -> Any:
        if self._update is not None:
            self._fake.updates.append((self._table, dict(self._update)))
        return SimpleNamespace(data=[])


class _MsSupabase:
    def __init__(self) -> None:
        self.updates: list[tuple[str, dict]] = []

    def table(self, name: str) -> _MsChain:
        return _MsChain(self, name)


def test_message_service_carries_the_gate_and_relays_the_refusal(monkeypatch):
    sentinel = object()
    captured: dict[str, Any] = {}

    async def _check(*_a, **_k):
        return sentinel

    async def _handle(**kw):
        captured.update(kw)
        for ev in orch._gated_events(_BLOCK):
            yield ev
        yield {"type": "done", "usage": {}}

    from shared.privacy import PrivacyCodec

    monkeypatch.setattr(quota, "check", _check)
    monkeypatch.setattr(ms, "handle_message", _handle)
    monkeypatch.setattr(ms, "_insert_turn_rows", lambda *a, **k: None)
    monkeypatch.setattr(ms, "write_audit_log", lambda *a, **k: None)
    monkeypatch.setattr(ms, "build_turn_codec",
                        lambda *a, **k: PrivacyCodec(real_to_fake={}, fake_to_real={}, enabled=False))

    async def _connected() -> bool:
        return False

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(redis=None)),
        is_disconnected=_connected,
    )
    fake = _MsSupabase()

    async def _drain() -> list[str]:
        return [chunk async for chunk in ms.send_message_stream(
            fake, user_id=USER, conversation_id=CONV,
            conv={"conversation_id": CONV, "case_id": None, "message_count": 2},
            content="ما شروط الفصل التعسفي؟", request=request, attachment_ids=None,
        )]

    events: list[tuple[str, dict]] = []
    for chunk in run(_drain()):
        lines = dict(ln.split(": ", 1) for ln in chunk.strip().splitlines() if ": " in ln)
        events.append((lines.get("event", ""), json.loads(lines.get("data", "{}"))))

    assert captured["deep_search_gate"] is sentinel
    names = [n for n, _ in events]
    assert "quota_exceeded" not in names
    assert names.index("message_start") < names.index("deep_search_gated") < names.index("done")
    assert dict(events)["deep_search_gated"] == _BLOCK.to_event_payload()
    # The turn IS saved: the refusal is the assistant message's content.
    contents = [u["content"] for t, u in fake.updates if t == "messages" and "content" in u]
    assert contents == [_BLOCK.reply_ar]


# ── 4. the ledger names the count depends on ────────────────────────────────

def test_the_count_matches_the_ledger_agent_names():
    """count_user_deep_searches counts a run once it has a
    `deep_search.sector_picker` or `deep_search.search_fee.*` row. A rename on
    either side silently zeroes free's count — pin both ends."""
    import inspect

    import agents.deep_search_v4.sector_picker.runner as picker
    from agents.deep_search_v4.orchestrator import _emit_search_fee
    from agents.utils.usage_sink import collect_llm_calls

    class _Ledger:
        def __init__(self) -> None:
            self.rows: list[dict] = []

        def table(self, _name: str):
            ledger = self

            class _T:
                def insert(self, rows):
                    ledger.rows.extend(rows)
                    return self

                def execute(self):
                    return SimpleNamespace(data=[])
            return _T()

    ledger = _Ledger()
    with collect_llm_calls(ledger, conversation_id=CONV, user_id=USER):
        _emit_search_fee("reg_compliance", ["q1", "q2"])
        _emit_search_fee("case", ["q1"])
    assert [(r["agent"], r["agent_family"]) for r in ledger.rows] == [
        ("deep_search.search_fee.reg_compliance", "deep_search"),
        ("deep_search.search_fee.case", "deep_search"),
    ]
    assert 'agent="deep_search.sector_picker"' in inspect.getsource(picker)
    assert 'agent_family="deep_search"' in inspect.getsource(picker)

    sql = (REPO / "shared/db/migrations/197_deep_search_gate.sql").read_text(encoding="utf-8")
    assert "c.agent = 'deep_search.sector_picker'" in sql
    assert "starts_with(c.agent, 'deep_search.search_fee.')" in sql
    assert "c.agent_family = 'deep_search'" in sql
