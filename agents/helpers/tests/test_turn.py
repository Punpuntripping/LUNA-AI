"""``TurnHelpers``: the DAG, parallel runs, critical vs not, ceilings, fallbacks.

Offline, fake helpers on a private registry. Sync tests with ``asyncio.run``
(CI has no pytest-asyncio).
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest

import agents.helpers._runtime as runtime
from agents.helpers import (
    HelperContext,
    HelperSpec,
    Readiness,
    TurnHelpers,
    bind_turn,
    current_turn,
    unbind_turn,
)
from agents.helpers.registry import topo_order

USER = "user-1"
CONVO = "conv-1"


@pytest.fixture(autouse=True)
def clean_runtime(monkeypatch):
    """No run of one test is joined by the next."""
    monkeypatch.setattr(runtime, "_INFLIGHT", {})
    monkeypatch.setattr(runtime, "_CONVO_LATEST", {})


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, float]] = []

    def mark(self, what: str, who: str) -> None:
        self.events.append((what, who, time.monotonic()))

    def at(self, what: str, who: str) -> float:
        return next(t for w, h, t in self.events if w == what and h == who)

    def count(self, what: str, who: str) -> int:
        return sum(1 for w, h, _ in self.events if w == what and h == who)


def _registry(*specs: HelperSpec) -> dict[str, HelperSpec]:
    reg = {spec.name: spec for spec in specs}
    topo_order(reg)
    return reg


def _turn(reg: dict[str, HelperSpec], message_id: str = "msg-1", **kw: Any) -> TurnHelpers:
    return TurnHelpers(
        supabase=None, user_id=USER, conversation_id=CONVO, message_id=message_id,
        registry=reg, **kw,
    )


def _no_ledger(spec: HelperSpec) -> HelperSpec:
    import dataclasses

    return dataclasses.replace(spec, own_ledger_scope=False)


# --------------------------------------------------------------------------- #
# The DAG
# --------------------------------------------------------------------------- #


def _dag(rec: Recorder, *, summary_gate: asyncio.Event | None = None) -> dict[str, HelperSpec]:
    async def scan(ctx: HelperContext) -> Any:
        rec.mark("start", "scan")
        await asyncio.sleep(0.01)
        rec.mark("end", "scan")
        return SimpleNamespace(filled_item_ids=["a", "b"], failed=0)

    async def summary(ctx: HelperContext) -> str:
        rec.mark("start", f"summary:{ctx.item_id}")
        assert "scan" in ctx.deps  # the dependency's result travels on
        if summary_gate is not None:
            await summary_gate.wait()
        await asyncio.sleep(0.02 if ctx.item_id == "a" else 0.05)
        rec.mark("end", f"summary:{ctx.item_id}")
        return f"S-{ctx.item_id}"

    async def segment(ctx: HelperContext) -> str:
        rec.mark("start", f"segment:{ctx.item_id}")
        assert ctx.deps.get("summary") == f"S-{ctx.item_id}"
        return f"G-{ctx.item_id}"

    return _registry(
        _no_ledger(HelperSpec(
            name="scan", scope="message", critical_for=frozenset({"router"}),
            ceiling_s=5, run=scan,
            produces_items=lambda stats: stats.filled_item_ids,
        )),
        _no_ledger(HelperSpec(
            name="summary", scope="item", depends_on=("scan",),
            critical_for=frozenset({"router"}), ceiling_s=5, run=summary, concurrency=4,
        )),
        _no_ledger(HelperSpec(
            name="segment", scope="item", depends_on=("scan", "summary"),
            critical_for=frozenset({"aggregator"}), ceiling_s=5, run=segment,
        )),
    )


def test_helpers_start_as_their_dependencies_finish():
    rec = Recorder()
    reg = _dag(rec)

    async def go() -> Any:
        turn = _turn(reg)
        turn.start("scan")
        router = await turn.ready("router")
        aggregator = await turn.ready("aggregator")
        return router, aggregator

    router, aggregator = asyncio.run(go())

    assert router.helper("scan").ok
    assert router.helper("summary").ok
    assert router.items == {"a": Readiness.ready("S-a"), "b": Readiness.ready("S-b")}
    assert aggregator.items == {"a": Readiness.ready("G-a"), "b": Readiness.ready("G-b")}
    # Each segment starts once ITS summary ends — a's does not wait for b's.
    assert rec.at("start", "summary:a") >= rec.at("end", "scan")
    assert rec.at("start", "segment:a") >= rec.at("end", "summary:a")
    assert rec.at("start", "segment:a") < rec.at("end", "summary:b")


def test_summaries_run_in_parallel():
    """Each summary holds until both have started: run one after another, this
    would never finish."""
    rec = Recorder()
    gate = asyncio.Event()
    reg = _dag(rec, summary_gate=gate)

    async def go() -> Any:
        turn = _turn(reg)
        turn.start("scan")

        async def release_when_both_started() -> None:
            while rec.count("start", "summary:a") + rec.count("start", "summary:b") < 2:
                await asyncio.sleep(0.005)
            gate.set()

        releaser = asyncio.create_task(release_when_both_started())
        report = await asyncio.wait_for(turn.ready("router"), timeout=2)
        await releaser
        return report

    report = asyncio.run(go())
    assert report.helper("summary").ok


def test_concurrency_limits_parallel_runs():
    running: list[int] = []
    current = 0

    async def slow(ctx: HelperContext) -> None:
        nonlocal current
        current += 1
        running.append(current)
        await asyncio.sleep(0.01)
        current -= 1

    reg = _registry(_no_ledger(HelperSpec(
        name="one_at_a_time", scope="item", critical_for=frozenset({"router"}),
        run=slow, concurrency=1,
    )))

    async def go() -> None:
        turn = _turn(reg)
        turn.start("one_at_a_time", ["a", "b", "c"])
        await turn.ready("router")

    asyncio.run(go())
    assert max(running) == 1


# --------------------------------------------------------------------------- #
# Critical / not critical / covered upstream
# --------------------------------------------------------------------------- #


def _slow_spec(gate: asyncio.Event, **kw: Any) -> HelperSpec:
    async def run(ctx: HelperContext) -> str:
        await gate.wait()
        return "done"

    fields = dict(
        name="slow", scope="message", critical_for=frozenset({"deep_planner"}),
        covered_upstream=frozenset({"aggregator"}), ceiling_s=5, run=run,
    )
    fields.update(kw)
    return _no_ledger(HelperSpec(**fields))


def test_a_consumer_it_is_not_critical_for_runs_alongside():
    async def go() -> Any:
        gate = asyncio.Event()
        turn = _turn(_registry(_slow_spec(gate)))
        turn.start("slow")
        started = time.monotonic()
        router = await turn.ready("router")
        covered = await turn.ready("aggregator")
        settled = await turn.settle("slow", consumer="router")
        quick = time.monotonic() - started
        asyncio.get_running_loop().call_later(0.05, gate.set)
        planner = await turn.ready("deep_planner")
        return router, covered, settled, quick, planner

    router, covered, settled, quick, planner = asyncio.run(go())
    assert quick < 0.05
    assert router.helpers == {} and covered.helpers == {}
    assert settled == Readiness.fallback("not_critical")
    assert planner.helper("slow") == Readiness.ready("done")


def test_a_critical_wait_stops_at_the_ceiling_and_never_cancels_the_run():
    async def go() -> Any:
        gate = asyncio.Event()
        turn = _turn(_registry(_slow_spec(gate, ceiling_s=0.05)))
        turn.start("slow")
        started = time.monotonic()
        report = await turn.ready("deep_planner")
        waited = time.monotonic() - started
        task = turn.task("slow")
        assert not task.done()
        gate.set()
        await asyncio.wait({task})
        return report, waited, task

    report, waited, task = asyncio.run(go())
    assert report.helper("slow") == Readiness.fallback("timeout")
    assert 0.04 <= waited < 1
    assert not task.cancelled()
    assert task.result().readiness() == Readiness.ready("done")  # it landed for later


def test_a_failing_helper_reaches_consumers_as_a_fallback():
    calls: list[str] = []

    async def boom(ctx: HelperContext) -> None:
        raise RuntimeError("SECRET-IN-THE-ERROR")

    async def after(ctx: HelperContext) -> str:
        calls.append("after")
        assert "boom" not in ctx.deps  # a failed dependency hands nothing on
        return "ok"

    reg = _registry(
        _no_ledger(HelperSpec(
            name="boom", scope="message", critical_for=frozenset({"router"}), run=boom,
        )),
        _no_ledger(HelperSpec(
            name="after", scope="message", depends_on=("boom",),
            critical_for=frozenset({"router"}), run=after,
        )),
    )

    async def go() -> Any:
        turn = _turn(reg)
        turn.start("boom")
        return await turn.ready("router")

    report = asyncio.run(go())
    assert report.helper("boom") == Readiness.fallback("failed")
    # Ordering, not success: the dependent still ran once the failure ended.
    assert report.helper("after") == Readiness.ready("ok")
    assert calls == ["after"]


def test_ready_never_raises_on_a_broken_frame(monkeypatch):
    async def go() -> Any:
        gate = asyncio.Event()
        turn = _turn(_registry(_slow_spec(gate)))
        turn.start("slow")

        def broken(*_a: Any, **_k: Any) -> None:
            raise RuntimeError("frame bug")

        monkeypatch.setattr("agents.helpers.turn.wait_tasks", broken)
        report = await turn.ready("deep_planner")
        settled = await turn.settle("slow", consumer="deep_planner")
        gate.set()
        return report, settled

    report, settled = asyncio.run(go())
    assert report.helpers == {}
    assert settled == Readiness.fallback("frame_error")


def test_a_helper_not_started_this_turn_is_not_applicable():
    async def go() -> Any:
        turn = _turn(_registry(_slow_spec(asyncio.Event())))
        return await turn.ready("deep_planner"), await turn.settle("slow", consumer="deep_planner")

    report, settled = asyncio.run(go())
    assert report.helper("slow") == Readiness.not_applicable("not_started")
    assert settled == Readiness.not_applicable("not_started")


def test_enabled_and_applies_to_gate_a_run():
    calls: list[str] = []

    async def run(ctx: HelperContext) -> str:
        calls.append(ctx.item_id)
        return "x"

    reg = _registry(_no_ledger(HelperSpec(
        name="picky", scope="item", critical_for=frozenset({"router"}), run=run,
        applies_to=lambda ctx: ctx.item_id != "short",
    )), _no_ledger(HelperSpec(
        name="off", scope="item", critical_for=frozenset({"router"}), run=run,
        enabled=lambda user_id: False,
    )))

    async def go() -> Any:
        turn = _turn(reg)
        turn.start("picky", ["long", "short"])
        turn.start("off", ["long"])
        return await turn.ready("router")

    report = asyncio.run(go())
    assert calls == ["long"]
    assert report.items["short"] == Readiness.not_applicable("not_applicable")
    assert report.items["long"] == Readiness.not_applicable("disabled")  # worst of the two


# --------------------------------------------------------------------------- #
# One run per (helper, item); queues; adopted runs
# --------------------------------------------------------------------------- #


def test_a_second_request_joins_the_running_task():
    calls: list[str] = []

    async def run(ctx: HelperContext) -> str:
        calls.append(ctx.item_id)
        await asyncio.sleep(0.02)
        return "once"

    reg = _registry(_no_ledger(HelperSpec(
        name="once", scope="item", critical_for=frozenset({"router"}), run=run,
    )))

    async def go() -> Any:
        first, second = _turn(reg, "m1"), _turn(reg, "m2")
        first.start("once", ["a"])
        first.start("once", ["a"])  # same turn, same item
        second.start("once", ["a"])  # the next turn, while it still runs
        assert first.task("once", "a") is second.task("once", "a")
        return await first.ready("router"), await second.ready("router")

    one, two = asyncio.run(go())
    assert calls == ["a"]
    assert one.items == two.items == {"a": Readiness.ready("once")}


def test_a_queued_helper_waits_for_the_conversations_previous_run():
    rec = Recorder()

    async def scan(ctx: HelperContext) -> str:
        rec.mark("start", ctx.message_id)
        await asyncio.sleep(0.03)
        rec.mark("end", ctx.message_id)
        return ctx.message_id

    reg = _registry(_no_ledger(HelperSpec(
        name="scan", scope="message", critical_for=frozenset({"router"}),
        run=scan, queue_per_conversation=True,
    )))

    async def go() -> None:
        first, second = _turn(reg, "m1"), _turn(reg, "m2")
        first.start("scan")
        second.start("scan")
        await second.ready("router")

    asyncio.run(go())
    assert rec.at("start", "m2") >= rec.at("end", "m1")


def test_an_adopted_run_is_waited_for_by_the_matrix():
    async def go() -> Any:
        gate = asyncio.Event()

        async def memory_run() -> str:
            await gate.wait()
            return "entries"

        reg = _registry(HelperSpec(
            name="memory", scope="message", critical_for=frozenset({"deep_planner"}),
            ceiling_s=5, run=None, auto_start=False,
        ))
        turn = _turn(reg)
        task = asyncio.create_task(memory_run())
        turn.adopt("memory", task)
        turn.adopt("memory", None)  # ignored
        assert turn.holds(task) and not turn.holds(asyncio.create_task(asyncio.sleep(0)))
        router = await turn.settle("memory", consumer="router")
        turn.start("memory")  # nothing to start: it is adopted
        asyncio.get_running_loop().call_later(0.02, gate.set)
        planner = await turn.settle("memory", consumer="deep_planner")
        return router, planner

    router, planner = asyncio.run(go())
    assert router == Readiness.fallback("not_critical")
    assert planner == Readiness.ready("entries")


def test_an_adopted_run_that_raised_is_a_fallback():
    async def go() -> Readiness:
        async def bad() -> None:
            raise ValueError("x")

        reg = _registry(HelperSpec(
            name="memory", scope="message", critical_for=frozenset({"other"}), run=None,
        ))
        turn = _turn(reg)
        turn.adopt("memory", asyncio.create_task(bad()))
        return await turn.settle("memory", consumer="other")

    assert asyncio.run(go()) == Readiness.fallback("failed")


# --------------------------------------------------------------------------- #
# The ContextVar, the ledger
# --------------------------------------------------------------------------- #


def test_the_turn_reaches_tasks_created_after_it_is_bound():
    async def go() -> Any:
        turn = _turn(_registry())
        bind_turn(turn)
        seen = await asyncio.create_task(_read_current())
        unbind_turn()
        unbind_turn()  # idempotent
        return turn, seen, current_turn()

    async def _read_current() -> Any:
        return current_turn()

    turn, seen, after = asyncio.run(go())
    assert seen is turn
    assert after is None


def test_a_run_that_outlives_the_turn_still_lands_its_cost():
    """The turn's ledger scope flushes at the end of the turn; a run past its
    ceiling records into its own scope, keyed to the turn's message."""
    from agents.utils.usage_sink import collect_llm_calls, record_call

    inserted: list[dict] = []

    class _Ledger:
        def table(self, name: str) -> Any:
            assert name == "llm_calls"
            return SimpleNamespace(insert=lambda rows: SimpleNamespace(
                execute=lambda: inserted.extend(rows)
            ))

    async def late(ctx: HelperContext) -> None:
        await asyncio.sleep(0.05)
        record_call(agent="late_helper", model=None, cost_usd=0.01)

    reg = _registry(HelperSpec(
        name="late", scope="message", critical_for=frozenset({"router"}),
        ceiling_s=0.01, run=late,
    ))

    async def go() -> None:
        with collect_llm_calls(_Ledger(), conversation_id=CONVO, user_id=USER, message_id="msg-1"):
            turn = TurnHelpers(
                supabase=_Ledger(), user_id=USER, conversation_id=CONVO,
                message_id="msg-1", registry=reg,
            )
            turn.start("late")
            assert (await turn.ready("router")).helper("late").reason == "timeout"
        # The turn's scope has flushed (nothing in it). The run goes on.
        await asyncio.wait({turn.task("late")})

    asyncio.run(go())
    assert [row["agent"] for row in inserted] == ["late_helper"]
    assert inserted[0]["message_id"] == "msg-1"
    assert inserted[0]["conversation_id"] == CONVO


def test_tasks_are_held_strongly_until_they_end():
    async def go() -> None:
        gate = asyncio.Event()
        turn = _turn(_registry(_slow_spec(gate)))
        turn.start("slow")
        task = turn.task("slow")
        assert task in runtime._BACKGROUND
        gate.set()
        await asyncio.wait({task})
        await asyncio.sleep(0)
        assert task not in runtime._BACKGROUND
        assert ("slow", f"{CONVO}:msg-1") not in runtime._INFLIGHT

    asyncio.run(go())
