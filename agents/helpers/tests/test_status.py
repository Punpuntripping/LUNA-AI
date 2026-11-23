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


def test_by_default_a_done_row_counts_against_the_attempts_too():
    seg, store = Segmenter(), FakeStore()
    store.put("a", "segmenter", status="done", attempt=3, version="v1")
    out = asyncio.run(_ensure(_registry(seg.spec()), store))
    assert out == {"a": Readiness.fallback("attempts_exhausted")}
    assert seg.calls == []


@pytest.mark.parametrize("status_, attempt, expected_attempt", [
    ("done", 3, 1),       # a done run starts the count over — even at max_attempts
    ("done", 1, 1),
    ("failed", 2, 3),     # failures in a row still count …
])
def test_a_helper_that_counts_failures_only_starts_over_after_a_done_run(status_, attempt, expected_attempt):
    seg, store = Segmenter(), FakeStore()
    store.put("a", "segmenter", status=status_, attempt=attempt, version="v1")
    out = asyncio.run(_ensure(_registry(seg.spec(attempts_count_failures_only=True)), store))
    assert out == {"a": Readiness.ready("map-a")}
    assert seg.calls == [("a", expected_attempt)]
    assert store.get("a", "segmenter").attempt == expected_attempt


@pytest.mark.parametrize("status_", ["failed", "running"])
def test_a_helper_that_counts_failures_only_still_gives_up_on_failures(status_):
    """… and a spent failure (or a killed run at max_attempts) is still the end."""
    seg, store = Segmenter(), FakeStore()
    store.put("a", "segmenter", status=status_, attempt=3, version="v1",
              lease_until=utcnow() - timedelta(seconds=1))
    out = asyncio.run(_ensure(_registry(seg.spec(attempts_count_failures_only=True)), store))
    assert out == {"a": Readiness.fallback("attempts_exhausted")}
    assert seg.calls == []


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
# Dependents of a run a CONSUMER started (CONSUMER_RUN_DEPENDENTS)
# --------------------------------------------------------------------------- #


class AngleMap:
    """A fake ``angle_map``: a stored item helper after the segmenter."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[HelperContext] = []
        self.scoped: list[bool] = []
        self.fail = fail
        self.done: dict[str, str] = {}

    async def run(self, ctx: HelperContext) -> str:
        from agents.utils import usage_sink

        self.calls.append(ctx)
        self.scoped.append(usage_sink.in_scope())
        await asyncio.sleep(0.01)
        if self.fail:
            raise RuntimeError("provider down")
        self.done[ctx.item_id] = f"angles-{ctx.item_id}"
        return self.done[ctx.item_id]

    def load(self, _supabase: Any, item_id: str) -> str | None:
        return self.done.get(item_id)

    def spec(self, **kw: Any) -> HelperSpec:
        fields = dict(
            name="angle_map", scope="item", stored=True, depends_on=("segmenter",),
            run=self.run, load=self.load, lease_s=30.0, max_attempts=2, version="a1",
        )
        fields.update(kw)
        return HelperSpec(**fields)


async def _drain(helper: str, ids: tuple[str, ...] = ("a",)) -> None:
    """Wait for the fire-and-forget runs of ``helper`` (they are not the consumer's)."""
    tasks = {t for t in (runtime.inflight(helper, i) for i in ids) if t is not None}
    if tasks:
        await asyncio.wait(tasks, timeout=5)


def test_a_consumer_started_run_starts_the_angle_map_when_it_ends():
    seg, angle, store = Segmenter(), AngleMap(), FakeStore()
    reg = _registry(seg.spec(), angle.spec())

    async def go() -> Any:
        out = await _ensure(reg, store, ids=("a", "b"))
        await _drain("angle_map", ("a", "b"))
        return out

    assert asyncio.run(go()) == {"a": Readiness.ready("map-a"), "b": Readiness.ready("map-b")}
    assert sorted(c.item_id for c in angle.calls) == ["a", "b"]
    # its own context: the consumer's user, no conversation (own ledger scope, below)
    assert all((c.user_id, c.conversation_id, c.message_id, c.attempt) == (USER, None, None, 1) for c in angle.calls)
    assert store.get("a", "angle_map").status == "done" and store.get("b", "angle_map").status == "done"


def test_its_run_is_detached_from_the_consumers_ledger_scope():
    from agents.utils import usage_sink

    seg, angle, store = Segmenter(), AngleMap(), FakeStore()
    reg = _registry(seg.spec(), angle.spec(own_ledger_scope=True))

    async def go() -> None:
        token = usage_sink._buffer.set([])  # the consumer's open llm_calls scope
        try:
            await _ensure(reg, store)
        finally:
            usage_sink._buffer.reset(token)
        await _drain("angle_map")

    asyncio.run(go())
    assert angle.scoped == [False]  # not the consumer's buffer — the runner opens its own


def test_flag_off_starts_nothing():
    seg, angle, store = Segmenter(), AngleMap(), FakeStore()
    reg = _registry(seg.spec(), angle.spec(enabled=lambda uid: False))

    async def go() -> Any:
        out = await _ensure(reg, store)
        assert runtime.inflight("angle_map", "a") is None
        return out

    assert asyncio.run(go()) == {"a": Readiness.ready("map-a")}
    assert angle.calls == [] and store.get("a", "angle_map") is None


def test_only_the_named_dependents_start_other_helpers_are_unchanged():
    """A consumer-started run starts ``angle_map`` only — any other dependent
    (here one with ``auto_start``) still starts only from a turn, as before."""
    seg, angle, store = Segmenter(), AngleMap(), FakeStore()
    other_calls: list[str] = []

    async def other(ctx: HelperContext) -> str:
        other_calls.append(ctx.item_id)
        return "x"

    reg = _registry(
        seg.spec(), angle.spec(),
        HelperSpec(name="other", scope="item", depends_on=("segmenter",), run=other, own_ledger_scope=False),
    )
    assert status.CONSUMER_RUN_DEPENDENTS == frozenset({"angle_map"})

    async def go() -> None:
        await _ensure(reg, store)
        assert runtime.inflight("other", "a") is None
        await _drain("angle_map")

    asyncio.run(go())
    assert [c.item_id for c in angle.calls] == ["a"] and other_calls == []


def test_a_failed_run_starts_it_too_like_a_turn_does():
    """The member whose segmenter ends last maps the group even when its own
    segmentation failed (the angle mapper's trigger rule) — so a failed run starts
    it as well, as ``TurnHelpers._after`` does."""
    seg, angle, store = Segmenter(fail=True), AngleMap(), FakeStore()
    reg = _registry(seg.spec(), angle.spec())

    async def go() -> Any:
        out = await _ensure(reg, store)
        await _drain("angle_map")
        return out

    assert asyncio.run(go()) == {"a": Readiness.fallback("failed")}
    assert [c.item_id for c in angle.calls] == ["a"]


def test_a_run_of_it_already_going_is_not_started_twice():
    seg, angle, store = Segmenter(), AngleMap(), FakeStore()
    reg = _registry(seg.spec(), angle.spec())

    async def go() -> None:
        going = asyncio.get_running_loop().create_future()
        runtime._INFLIGHT[("angle_map", "a")] = going
        await _ensure(reg, store)
        assert runtime._INFLIGHT[("angle_map", "a")] is going
        going.set_result(None)

    asyncio.run(go())
    assert angle.calls == [] and store.claims == 1  # the segmenter's lease only


def test_a_fresh_stored_result_is_not_redone_and_a_lease_elsewhere_is_left_alone():
    seg, angle, store = Segmenter(), AngleMap(), FakeStore()
    angle.done["a"] = "angles-a"  # the group's map is stored and fresh
    store.put("b", "angle_map", status="running", attempt=1,
              lease_until=utcnow() + timedelta(seconds=60), version="a1")  # another process maps b
    reg = _registry(seg.spec(), angle.spec())

    async def go() -> None:
        await _ensure(reg, store, ids=("a", "b"))
        await _drain("angle_map", ("a", "b"))

    asyncio.run(go())
    assert angle.calls == []
    assert store.get("a", "angle_map") is None and store.get("b", "angle_map").status == "running"


def test_a_failing_dependent_never_reaches_the_run_or_its_consumer(monkeypatch):
    import agents.helpers.registry as registry_mod

    seg, angle, store = Segmenter(), AngleMap(fail=True), FakeStore()
    reg = _registry(seg.spec(), angle.spec())

    async def go() -> Any:
        out = await _ensure(reg, store)
        await _drain("angle_map")
        return out

    # its run fails: stored failed, the segmenter's readiness untouched
    assert asyncio.run(go()) == {"a": Readiness.ready("map-a")}
    assert store.get("a", "angle_map").status == "failed"

    # its gate raises / the graph lookup raises: nothing starts, nothing leaks out
    runtime._INFLIGHT.clear()

    def boom(_uid: str) -> bool:
        raise RuntimeError("flag bug")

    reg = _registry(Segmenter().spec(), AngleMap().spec(enabled=boom))
    assert asyncio.run(_ensure(reg, FakeStore())) == {"a": Readiness.ready("map-a")}

    runtime._INFLIGHT.clear()

    def broken(*_a: Any, **_k: Any) -> Any:
        raise ValueError("graph bug")

    monkeypatch.setattr(registry_mod, "dependents", broken)
    reg = _registry(Segmenter().spec(), AngleMap().spec())
    assert asyncio.run(_ensure(reg, FakeStore())) == {"a": Readiness.ready("map-a")}


def test_a_turn_started_run_still_starts_it_once_from_the_turn():
    """Unchanged path: the turn's run starts it (turn context); a consumer that
    joins that run starts nothing more."""
    seg, angle, store = Segmenter(delay=0.03), AngleMap(), FakeStore()
    reg = _registry(seg.spec(), angle.spec())

    async def go() -> Any:
        turn = TurnHelpers(supabase=None, user_id=USER, conversation_id=CONVO,
                           message_id="m1", registry=reg, store=store)
        bind_turn(turn)
        turn.start("segmenter", ["a"])
        out = await ensure_ready("segmenter", ["a"], consumer="aggregator",
                                 supabase=None, user_id=USER, registry=reg, store=store)
        await _drain("angle_map")
        return out

    assert asyncio.run(go()) == {"a": Readiness.ready("map-a")}
    assert [(c.item_id, c.conversation_id, c.message_id) for c in angle.calls] == [("a", CONVO, "m1")]


def test_in_the_real_registry_it_is_the_angle_map_after_the_segmenter_only():
    from agents.helpers.registry import REGISTRY, dependents

    starters = {
        spec.name: [d.name for d in dependents(spec.name) if d.name in status.CONSUMER_RUN_DEPENDENTS]
        for spec in REGISTRY.values()
    }
    assert {k: v for k, v in starters.items() if v} == {"segmenter": ["angle_map"]}


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


class _ByTable(_Recorder):
    """``_Recorder`` that answers per table: the item → file lookup, then helper_runs."""

    def __init__(self, items: list[dict], runs: list[dict]) -> None:
        super().__init__([])
        self._answers = {"workspace_items": items, "helper_runs": runs}
        self._current = ""

    def table(self, name: str) -> "_ByTable":
        self._current = name
        return super().table(name)

    def execute(self) -> Any:
        return SimpleNamespace(data=self._answers.get(self._current, []))


def test_a_second_copy_of_a_file_shares_the_files_run():
    # migration 223: helper_runs.item_id holds the FILE id; a library file attached
    # to another conversation (migration 227) is a new item with the same file
    run = {"item_id": "file-1", "helper": "segmenter", "status": "done", "attempt": 1,
           "lease_until": None, "version": "v1", "error": None}
    db = _ByTable([{"item_id": "copy-2", "file_id": "file-1"}], [run])
    store = status.SupabaseHelperRunStore(db)
    got = store.get("copy-2", "segmenter")
    assert got is not None and got.item_id == "file-1" and got.status == "done"
    run_filters = [c[1] for c in db.calls if c[0] == "eq" and c[1][0] == "item_id"]
    assert ("item_id", "file-1") in run_filters

    db = _ByTable([{"item_id": "copy-2", "file_id": "file-1"}], [run])
    status.SupabaseHelperRunStore(db).claim(
        item_id="copy-2", helper="segmenter", user_id=USER, prev=None, attempt=1,
        lease_until=utcnow() + timedelta(seconds=90), version="v1",
    )
    upsert = next(c for c in db.calls if c[0] == "upsert")
    assert upsert[1][0]["item_id"] == "file-1"           # the run is claimed on the file
