"""Across turns and processes (helper_frame.md §5.3): ``ensure_ready`` + leases.

A fake stored helper on a private registry and an in-memory ``helper_runs``
store whose ``claim`` is a real compare-and-set. Offline; ``asyncio.run``.
"""
from __future__ import annotations

import asyncio
import dataclasses
import threading
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

import agents.helpers._runtime as runtime
import agents.helpers.status as status
from agents.helpers import (
    HelperContext,
    HelperSpec,
    Readiness,
    RunRow,
    TurnHelpers,
    bind_turn,
    ensure_ready,
)
from agents.helpers._runtime import utcnow
from agents.helpers.registry import topo_order

USER = "user-1"
CONVO = "conv-1"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(runtime, "_INFLIGHT", {})
    monkeypatch.setattr(runtime, "_CONVO_LATEST", {})
    monkeypatch.setattr(status, "POLL_S", 0.01)


class FakeStore:
    """``helper_runs`` in memory. ``claim`` matches only the row as read."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], RunRow] = {}
        self.lock = threading.Lock()
        self.claims = 0
        self.renewals = 0

    def put(self, item_id: str, helper: str, **fields: Any) -> None:
        self.rows[(item_id, helper)] = RunRow(item_id=item_id, helper=helper, **fields)

    def get(self, item_id: str, helper: str) -> RunRow | None:
        return self.rows.get((item_id, helper))

    def claim(self, *, item_id, helper, user_id, prev, attempt, lease_until, version):
        with self.lock:
            current = self.rows.get((item_id, helper))
            if prev is None:
                if current is not None:
                    return None
            elif current is None or (current.attempt, current.status) != (prev.attempt, prev.status):
                return None
            elif current.status == "running" and current.lease_live(utcnow()):
                return None
            row = RunRow(item_id, helper, "running", attempt, lease_until, version)
            self.rows[(item_id, helper)] = row
            self.claims += 1
            return row

    def renew(self, row: RunRow, lease_until: datetime) -> bool:
        with self.lock:
            current = self.rows.get((row.item_id, row.helper))
            if current is None or current.attempt != row.attempt or current.status != "running":
                return False
            self.rows[(row.item_id, row.helper)] = dataclasses.replace(current, lease_until=lease_until)
            self.renewals += 1
            return True

    def finish(self, row: RunRow, *, status: str, error: str | None) -> bool:
        with self.lock:
            current = self.rows.get((row.item_id, row.helper))
            if current is None or current.attempt != row.attempt:
                return False
            self.rows[(row.item_id, row.helper)] = dataclasses.replace(
                current, status=status, error=error, lease_until=None
            )
            return True


class Segmenter:
    """A fake stored item helper: results live in ``maps``."""

    def __init__(self, *, delay: float = 0.0, fail: bool = False) -> None:
        self.maps: dict[str, str] = {}
        self.calls: list[tuple[str, int]] = []
        self.delay = delay
        self.fail = fail

    async def run(self, ctx: HelperContext) -> str:
        self.calls.append((ctx.item_id, ctx.attempt))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("SECRET-IN-THE-ERROR")
        self.maps[ctx.item_id] = f"map-{ctx.item_id}"
        return self.maps[ctx.item_id]

    def load(self, _supabase: Any, item_id: str) -> str | None:
        return self.maps.get(item_id)

    def spec(self, **kw: Any) -> HelperSpec:
        fields = dict(
            name="segmenter", scope="item", stored=True,
            critical_for=frozenset({"aggregator", "writer"}),
            ceiling_s=2.0, run=self.run, load=self.load, own_ledger_scope=False,
            lease_s=30.0, max_attempts=3, version="v1",
        )
        fields.update(kw)
        return HelperSpec(**fields)


def _registry(*specs: HelperSpec) -> dict[str, HelperSpec]:
    reg = {s.name: s for s in specs}
    topo_order(reg)
    return reg


async def _ensure(reg, store, ids=("a",), consumer="aggregator", **kw) -> dict[str, Readiness]:
    return await ensure_ready(
        "segmenter", list(ids), consumer=consumer, supabase=None, user_id=USER,
        store=store, registry=reg, **kw,
    )


# --------------------------------------------------------------------------- #
# The four steps
# --------------------------------------------------------------------------- #


def test_a_stored_result_is_used_without_a_run():
    seg, store = Segmenter(), FakeStore()
    seg.maps["a"] = "old-map"
    out = asyncio.run(_ensure(_registry(seg.spec()), store))
    assert out == {"a": Readiness.ready("old-map")}
    assert seg.calls == [] and store.claims == 0


def test_nothing_stored_claims_a_lease_and_runs_it_now():
    seg, store = Segmenter(), FakeStore()
    out = asyncio.run(_ensure(_registry(seg.spec()), store, ids=("a", "b", "a")))
    assert out == {"a": Readiness.ready("map-a"), "b": Readiness.ready("map-b")}
    assert sorted(seg.calls) == [("a", 1), ("b", 1)]
    assert store.get("a", "segmenter").status == "done"
    assert store.get("a", "segmenter").attempt == 1


def test_a_live_lease_elsewhere_is_polled_until_it_lands():
    """Another process is running it: no second run, wait for its result."""
    seg, store = Segmenter(), FakeStore()
    store.put("a", "segmenter", status="running", attempt=1,
              lease_until=utcnow() + timedelta(seconds=60), version="v1")

    async def go() -> Any:
        async def other_process_finishes() -> None:
            await asyncio.sleep(0.05)
            seg.maps["a"] = "their-map"
            store.finish(store.get("a", "segmenter"), status="done", error=None)

        asyncio.create_task(other_process_finishes())
        # A roomy ceiling: under a loaded CI runner the 0.05 s "other process"
        # must still land well inside it (2 s was flaky under load).
        return await _ensure(_registry(seg.spec(ceiling_s=30.0)), store)

    assert asyncio.run(go()) == {"a": Readiness.ready("their-map")}
    assert seg.calls == []


def test_a_live_lease_elsewhere_past_the_ceiling_is_a_timeout():
    seg, store = Segmenter(), FakeStore()
    store.put("a", "segmenter", status="running", attempt=1,
              lease_until=utcnow() + timedelta(seconds=60), version="v1")
    started = time.monotonic()
    out = asyncio.run(_ensure(_registry(seg.spec(ceiling_s=0.05)), store))
    assert out == {"a": Readiness.fallback("timeout")}
    assert time.monotonic() - started < 1
    assert seg.calls == []


def test_an_expired_lease_is_taken_over_and_rerun():
    """A redeploy killed the run: its lease ran out, the next consumer re-runs it."""
    seg, store = Segmenter(), FakeStore()
    store.put("a", "segmenter", status="running", attempt=1,
              lease_until=utcnow() - timedelta(seconds=1), version="v1")
    out = asyncio.run(_ensure(_registry(seg.spec()), store))
    assert out == {"a": Readiness.ready("map-a")}
    assert seg.calls == [("a", 2)]
    assert store.get("a", "segmenter").attempt == 2


def test_a_retryable_failure_is_rerun_and_a_spent_one_is_not():
    seg, store = Segmenter(), FakeStore()
    store.put("a", "segmenter", status="failed", attempt=2, version="v1")
    store.put("b", "segmenter", status="failed", attempt=3, version="v1")
    out = asyncio.run(_ensure(_registry(seg.spec()), store, ids=("a", "b")))
    assert out == {"a": Readiness.ready("map-a"), "b": Readiness.fallback("attempts_exhausted")}
    assert seg.calls == [("a", 3)]


def test_a_new_version_starts_the_attempts_over():
    seg, store = Segmenter(), FakeStore()
    store.put("a", "segmenter", status="failed", attempt=3, version="v0")
    out = asyncio.run(_ensure(_registry(seg.spec()), store))
    assert out == {"a": Readiness.ready("map-a")}
    assert seg.calls == [("a", 1)]


def test_a_done_row_with_nothing_to_load_is_rerun():
    """Stale or missing result (e.g. content changed): the load decides."""
    seg, store = Segmenter(), FakeStore()
    store.put("a", "segmenter", status="done", attempt=1, version="v1")
    out = asyncio.run(_ensure(_registry(seg.spec()), store))
    assert out == {"a": Readiness.ready("map-a")}
    assert seg.calls == [("a", 2)]


def test_a_failing_run_is_stored_failed_and_reaches_the_consumer_as_a_fallback(caplog):
    seg, store = Segmenter(fail=True), FakeStore()
    out = asyncio.run(_ensure(_registry(seg.spec()), store))
    assert out == {"a": Readiness.fallback("failed")}
    row = store.get("a", "segmenter")
    assert (row.status, row.error) == ("failed", "RuntimeError")


# --------------------------------------------------------------------------- #
# Critical vs not, ceilings, joining, races
# --------------------------------------------------------------------------- #


def test_a_consumer_it_is_not_critical_for_never_waits_or_starts_it():
    seg, store = Segmenter(), FakeStore()
    out = asyncio.run(_ensure(_registry(seg.spec()), store, consumer="deep_planner"))
    assert out == {"a": Readiness.fallback("not_critical")}
    assert seg.calls == [] and store.claims == 0


def test_the_ceiling_ends_the_wait_and_the_run_lands_later():
    seg, store = Segmenter(delay=0.2), FakeStore()

    async def go() -> Any:
        started = time.monotonic()
        out = await _ensure(_registry(seg.spec(ceiling_s=0.05)), store)
        waited = time.monotonic() - started
        assert store.get("a", "segmenter").status == "running"
        task = runtime.inflight("segmenter", "a")
        await asyncio.wait({task})
        return out, waited, task

    out, waited, task = asyncio.run(go())
    assert out == {"a": Readiness.fallback("timeout")}
    assert waited < 0.2
    assert not task.cancelled()
    assert store.get("a", "segmenter").status == "done"
    assert seg.maps["a"] == "map-a"


def test_two_consumers_in_one_process_share_one_run():
    seg, store = Segmenter(delay=0.03), FakeStore()
    reg = _registry(seg.spec())

    async def go() -> Any:
        return await asyncio.gather(
            _ensure(reg, store), _ensure(reg, store, consumer="writer")
        )

    one, two = asyncio.run(go())
    assert one == two == {"a": Readiness.ready("map-a")}
    assert seg.calls == [("a", 1)]


def test_only_one_of_two_processes_wins_the_claim():
    """Both read the same expired row; the conditional write lets one through."""
    seg, store = Segmenter(), FakeStore()
    store.put("a", "segmenter", status="failed", attempt=1, version="v1")
    spec = seg.spec()
    ctx = HelperContext(helper="segmenter", supabase=None, user_id=USER, item_id="a")
    prev = store.get("a", "segmenter")

    def claim() -> Any:
        return store.claim(
            item_id="a", helper="segmenter", user_id=USER, prev=prev, attempt=2,
            lease_until=utcnow() + timedelta(seconds=30), version="v1",
        )

    assert claim() is not None
    assert claim() is None
    # And decide() reads the loser's position as "someone else holds it".
    assert asyncio.run(status.decide(spec, ctx, store, claim=True))[0] == "wait"


def test_the_lease_is_renewed_while_the_run_is_alive():
    seg, store = Segmenter(delay=0.15), FakeStore()
    out = asyncio.run(_ensure(_registry(seg.spec(lease_s=0.06)), store))
    assert out == {"a": Readiness.ready("map-a")}
    assert store.renewals >= 2


def test_on_wait_fires_once_when_the_wait_passes_the_threshold(monkeypatch):
    monkeypatch.setattr(status, "ON_WAIT_AFTER_S", 0.02)
    shown: list[str] = []

    async def go(delay: float) -> None:
        seg, store = Segmenter(delay=delay), FakeStore()
        await _ensure(_registry(seg.spec()), store, on_wait=lambda: shown.append(status.WAIT_STATUS_TEXT))

    asyncio.run(go(0.1))
    assert shown == [status.WAIT_STATUS_TEXT]
    shown.clear()
    asyncio.run(go(0.0))
    assert shown == []


def test_an_async_on_wait_and_a_broken_one_are_both_fine(monkeypatch):
    monkeypatch.setattr(status, "ON_WAIT_AFTER_S", 0.01)
    shown: list[str] = []

    async def show() -> None:
        shown.append("x")

    def broken() -> None:
        raise RuntimeError("status line bug")

    async def go() -> Any:
        a = await _ensure(_registry(Segmenter(delay=0.05).spec()), FakeStore(), on_wait=show)
        runtime._INFLIGHT.clear()
        b = await _ensure(_registry(Segmenter(delay=0.05).spec()), FakeStore(), on_wait=broken)
        return a, b

    a, b = asyncio.run(go())
    assert shown == ["x"]
    assert a == b == {"a": Readiness.ready("map-a")}


def test_ensure_ready_never_raises():
    class BrokenStore(FakeStore):
        def get(self, item_id: str, helper: str) -> RunRow | None:
            raise ConnectionError("db down")

    seg = Segmenter()
    reg = _registry(seg.spec())
    assert asyncio.run(_ensure(reg, BrokenStore())) == {"a": Readiness.fallback("frame_error")}
    assert asyncio.run(ensure_ready(
        "nope", ["a"], consumer="writer", supabase=None, user_id=USER, registry=reg,
    )) == {"a": Readiness.fallback("unknown_helper")}
    assert asyncio.run(_ensure(reg, FakeStore(), ids=())) == {}


def test_a_not_applicable_item_is_never_claimed():
    seg, store = Segmenter(), FakeStore()
    spec = seg.spec(applies_to=lambda ctx: ctx.item_id != "short")
    out = asyncio.run(_ensure(_registry(spec), store, ids=("short",)))
    assert out == {"short": Readiness.not_applicable("not_applicable")}
    assert store.claims == 0


# --------------------------------------------------------------------------- #
# Inside a turn
# --------------------------------------------------------------------------- #


def test_the_turn_starts_a_stored_helper_when_its_dependency_lands_and_consumers_join_it():
    seg, store = Segmenter(delay=0.03), FakeStore()

    async def summary(ctx: HelperContext) -> str:
        return "S"

    reg = _registry(
        HelperSpec(name="summary", scope="item", critical_for=frozenset({"router"}),
                   run=summary, own_ledger_scope=False),
        seg.spec(depends_on=("summary",)),
    )

    async def go() -> Any:
        turn = TurnHelpers(supabase=None, user_id=USER, conversation_id=CONVO,
                           message_id="m1", registry=reg, store=store)
        bind_turn(turn)
        turn.start("summary", ["a"])
        await turn.ready("router")
        assert turn.task("segmenter", "a") is not None  # started by the summary
        # A deep consumer later in the turn joins that run instead of starting one.
        return await ensure_ready("segmenter", ["a"], consumer="aggregator",
                                  supabase=None, user_id=USER, registry=reg, store=store)

    assert asyncio.run(go()) == {"a": Readiness.ready("map-a")}
    assert seg.calls == [("a", 1)]


def test_the_turn_does_not_rerun_what_another_process_holds():
    seg, store = Segmenter(), FakeStore()
    store.put("a", "segmenter", status="running", attempt=1,
              lease_until=utcnow() + timedelta(seconds=60), version="v1")
    reg = _registry(seg.spec())

    async def go() -> Any:
        turn = TurnHelpers(supabase=None, user_id=USER, conversation_id=CONVO,
                           message_id="m1", registry=reg, store=store)
        turn.start("segmenter", ["a"])
        return await turn.ready("aggregator")

    report = asyncio.run(go())
    assert report.items == {"a": Readiness.fallback("running_elsewhere")}
    assert seg.calls == []


# --------------------------------------------------------------------------- #
# The Supabase store: the conditional writes it sends
# --------------------------------------------------------------------------- #


class _Recorder:
    def __init__(self, data: list[dict]) -> None:
        self.calls: list[tuple] = []
        self.data = data

    def table(self, name: str) -> "_Recorder":
        self.calls.append(("table", name))
        return self

    def __getattr__(self, op: str) -> Any:
        def record(*args: Any, **kwargs: Any) -> "_Recorder":
            self.calls.append((op, args, kwargs))
            return self
        return record

    def execute(self) -> Any:
        return SimpleNamespace(data=self.data)


def test_the_supabase_store_writes_are_conditional():
    row = {"item_id": "a", "helper": "segmenter", "status": "running", "attempt": 1,
           "lease_until": "2026-11-11T10:00:00+00:00", "version": "v1", "error": None}
    lease = utcnow() + timedelta(seconds=90)

    db = _Recorder([row])
    claimed = status.SupabaseHelperRunStore(db).claim(
        item_id="a", helper="segmenter", user_id=USER, prev=None, attempt=1,
        lease_until=lease, version="v1",
    )
    upsert = next(c for c in db.calls if c[0] == "upsert")
    assert upsert[2] == {"on_conflict": "item_id,helper", "ignore_duplicates": True}
    assert claimed.lease_until == datetime.fromisoformat(row["lease_until"])

    db = _Recorder([])
    prev = RunRow("a", "segmenter", "running", 1, utcnow() - timedelta(seconds=5), "v1")
    assert status.SupabaseHelperRunStore(db).claim(
        item_id="a", helper="segmenter", user_id=USER, prev=prev, attempt=2,
        lease_until=lease, version="v1",
    ) is None
    filters = [(c[0], c[1][0]) for c in db.calls if c[0] in ("eq", "lt")]
    assert ("eq", "attempt") in filters and ("eq", "status") in filters
    assert ("lt", "lease_until") in filters  # only an expired lease is taken over

    db = _Recorder([row])
    assert status.SupabaseHelperRunStore(db).finish(prev, status="failed", error="X" * 500)
    update = next(c for c in db.calls if c[0] == "update")[1][0]
    assert update["status"] == "failed" and len(update["error"]) == 200
    assert update["lease_until"] is None
