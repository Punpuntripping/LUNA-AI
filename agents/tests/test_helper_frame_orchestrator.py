"""The helper frame inside a turn (HELPER_FRAME_ENABLED): helper_frame.md §5.6–5.7.

Turns go through ``_handle_message_inner`` on the memory-router suite's fakes
(FakeDB, scripted router, scripted memory agent), so these see what a turn sees.
Offline; ``asyncio.run`` (CI has no pytest-asyncio).
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

import agents.helpers as helper_frame
import agents.helpers._runtime as runtime
import agents.helpers.registry as registry
import agents.memory.convo_memory_writer.runner as writer_runner
import agents.orchestrator as orch
from agents.tests.test_conversation_memory_router import (  # noqa: F401 - fixtures
    AMOUNT,
    CONVO,
    TABLE,
    USER,
    FakeDB,
    _after_the_run,
    _chatting,
    _collect,
    _completed,
    _empty_turn,
    _noting_the_run,
    _row,
    _runs_done,
    _turn,
    _writing,
    fake_writer,
    flag_off,
    flag_on,
    memory_agent,
    no_queue_left_over,
    seen_route,
)


@pytest.fixture
def frame_on(monkeypatch):
    from shared.config import get_settings

    monkeypatch.setattr(get_settings(), "HELPER_FRAME_ENABLED", True)
    monkeypatch.setattr(runtime, "_INFLIGHT", {})
    monkeypatch.setattr(runtime, "_CONVO_LATEST", {})


@pytest.fixture
def frame_off(monkeypatch):
    from shared.config import get_settings

    monkeypatch.setattr(get_settings(), "HELPER_FRAME_ENABLED", False)


@pytest.fixture
def settles(monkeypatch):
    """Every frame wait on a message helper: (helper, consumer)."""
    seen: list[tuple[str, str]] = []
    real = helper_frame.TurnHelpers.settle

    async def spy(self: Any, name: str, *, consumer: str) -> Any:
        seen.append((name, consumer))
        return await real(self, name, consumer=consumer)

    monkeypatch.setattr(helper_frame.TurnHelpers, "settle", spy)
    return seen


async def _helpers_done() -> None:
    """Helpers outlive the turn; ``asyncio.run`` would cancel them on exit."""
    await _runs_done()
    while runtime._BACKGROUND:
        await asyncio.wait(list(runtime._BACKGROUND))


def _run(db: FakeDB, script: Any, *, when_done: Any = None, **turn: Any) -> list[dict]:
    async def go() -> list[dict]:
        events = await _turn(db, script, **turn)
        if when_done is not None:
            when_done()
        await _helpers_done()
        return events

    return asyncio.run(go())


def _statuses(events: list[dict]) -> list[str]:
    return [e["text"] for e in events if e["type"] == "status"]


# --------------------------------------------------------------------------- #
# Steps 0a / 0b / memory start: same order, summaries in parallel
# --------------------------------------------------------------------------- #


def test_ocr_then_parallel_summaries_then_the_memory_agent(
    frame_on, flag_on, seen_route, memory_agent, monkeypatch
):
    order: list[str] = []
    started: set[str] = set()

    async def fake_ocr(*_a: Any, **_k: Any) -> SimpleNamespace:
        order.append("ocr")
        return SimpleNamespace(filled_item_ids=["i1", "i2"], failed=0)

    async def fake_summary(_supabase: Any, item_id: str) -> bool:
        order.append(f"summary:{item_id}")
        started.add(item_id)
        # Ends only once both are running: one after another, it never would
        # (and the turn's 5 s timeout fails the test).
        while len(started) < 2:
            await asyncio.sleep(0.005)
        order.append(f"end:{item_id}")
        return True

    real_start = orch._start_memory_writer

    def noting_start(**kwargs: Any) -> Any:
        order.append("start")
        return real_start(**kwargs)

    monkeypatch.setattr(orch, "run_ocr_extraction", fake_ocr)
    monkeypatch.setattr(orch, "summarize_workspace_item", fake_summary)
    monkeypatch.setattr(orch, "_start_memory_writer", noting_start)
    events = _run(_empty_turn(), _chatting())

    assert events[-1]["type"] == "done"
    assert order[0] == "ocr"
    assert set(order[1:3]) == {"summary:i1", "summary:i2"}
    assert order.index("start") > max(order.index("end:i1"), order.index("end:i2"))
    assert _statuses(events) == []


@pytest.mark.parametrize("mode", ["failed", "raises", "timeout"])
def test_an_unreadable_attachment_keeps_todays_status_line(
    frame_on, flag_off, seen_route, monkeypatch, mode
):
    gate = asyncio.Event()
    summaries: list[str] = []
    ocr_tasks: list[asyncio.Task] = []

    async def fake_ocr(*_a: Any, **_k: Any) -> SimpleNamespace:
        if mode == "raises":
            raise RuntimeError("mistral down")
        if mode == "timeout":
            await gate.wait()
        return SimpleNamespace(filled_item_ids=["i1"], failed=1 if mode == "failed" else 0)

    async def fake_summary(_supabase: Any, item_id: str) -> bool:
        summaries.append(item_id)
        return True

    monkeypatch.setattr(orch, "run_ocr_extraction", fake_ocr)
    monkeypatch.setattr(orch, "summarize_workspace_item", fake_summary)
    monkeypatch.setitem(
        registry.REGISTRY, "ocr", dataclasses.replace(registry.OCR, ceiling_s=0.05)
    )

    def release() -> None:
        ocr_tasks.extend(t for t in runtime._BACKGROUND if t.get_name() == "helper.ocr")
        gate.set()

    events = _run(_empty_turn(), _chatting(), when_done=release)

    assert _statuses(events) == [orch._OCR_FAILED_STATUS]
    assert events[-1]["type"] == "done"
    if mode == "timeout":
        # Past the ceiling the router went on; OCR was not cancelled, it landed,
        # and the summary of what it filled ran after the turn.
        assert len(ocr_tasks) == 1
        assert ocr_tasks[0].done() and not ocr_tasks[0].cancelled()
        assert ocr_tasks[0].result().status == "done"
        assert summaries == ["i1"]
    elif mode == "failed":
        assert summaries == ["i1"]
    else:
        assert summaries == []


def test_the_status_line_is_the_same_text_on_both_paths():
    assert orch._OCR_FAILED_STATUS == (
        "تعذّرت قراءة بعض المرفقات، وسيتابع المساعد الإجابة دون الاستناد إلى محتواها."
    )


# --------------------------------------------------------------------------- #
# Step 1: the pre-router hook is a helper critical for the router
# --------------------------------------------------------------------------- #


def test_the_router_waits_for_the_pre_router_hook(
    frame_on, flag_off, seen_route, monkeypatch, settles
):
    order: list[str] = []

    async def hook(*_a: Any, **_k: Any) -> None:
        await asyncio.sleep(0.02)
        order.append("hook")

    real_route = orch._route

    def noting_route(**kwargs: Any) -> Any:
        order.append("route")
        return real_route(**kwargs)

    monkeypatch.setattr(orch.memory, "compact_conversation", hook)
    monkeypatch.setattr(orch, "_route", noting_route)
    _run(_empty_turn(), _chatting())

    assert order == ["hook", "route"]
    assert ("pre_router_memory", "router") in settles


def test_a_failing_pre_router_hook_never_aborts_the_turn(
    frame_on, flag_off, seen_route, monkeypatch, caplog
):
    async def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("compactor down")

    monkeypatch.setattr(orch.memory, "resummarize_dirty_items", boom)
    with caplog.at_level(logging.WARNING):
        events = _run(_empty_turn(), _chatting())

    assert events[-1]["type"] == "done"
    assert "memory pre-hook failed" in caplog.text


# --------------------------------------------------------------------------- #
# The memory agent: not the router, everyone after it
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "script", [pytest.param(_chatting, id="chat"), pytest.param(_writing, id="dispatch")]
)
def test_the_router_runs_beside_the_memory_agent(
    frame_on, flag_on, seen_route, memory_agent, fake_writer, script
):
    memory_agent.gate = asyncio.Event()
    running: list[bool] = []
    router = _noting_the_run(script(), running)

    async def go() -> None:
        asyncio.get_running_loop().call_later(0.05, memory_agent.gate.set)
        await _turn(_empty_turn(), router)
        await _helpers_done()

    asyncio.run(go())
    assert running == [True]


def test_the_memory_agent_lands_before_the_dispatch(
    frame_on, flag_on, seen_route, memory_agent, fake_writer, settles
):
    memory_agent.gate = asyncio.Event()
    script = _after_the_run(_writing(), memory_agent)
    _run(_empty_turn(), script)

    assert AMOUNT not in script.infos[0].instructions  # the router did not have it
    assert AMOUNT in seen_route["input"].conversation_memory_md  # the dispatch did
    assert ("memory", "writer_planner") in settles


def test_a_slow_memory_agent_does_not_hold_the_dispatch_past_the_ceiling(
    frame_on, flag_on, seen_route, memory_agent, fake_writer, monkeypatch
):
    monkeypatch.setitem(
        registry.REGISTRY, "memory", dataclasses.replace(registry.MEMORY, ceiling_s=0.05)
    )
    monkeypatch.setattr(orch, "_logfire", MagicMock())
    memory_agent.gate = asyncio.Event()

    def at_dispatch() -> None:
        seen_route["tasks"] = list(writer_runner._BACKGROUND_TASKS)
        memory_agent.gate.set()

    seen_route["on_dispatch"] = at_dispatch
    db = _empty_turn(**{TABLE: [_row(1, content="The old entry.")]})
    events = _run(db, _writing())

    assert _completed(events)
    block = seen_route["input"].conversation_memory_md
    assert "The old entry." in block and AMOUNT not in block
    orch._logfire.info.assert_any_call("convo_memory_writer.past_ceiling", conversation_id=CONVO)
    task = seen_route["tasks"][0]
    assert task.done() and not task.cancelled()
    assert db.contents() == ["The old entry.", AMOUNT]  # it landed for the next turn


def test_a_chat_turn_never_waits_for_the_memory_agent(
    frame_on, flag_on, seen_route, memory_agent, settles
):
    _run(_empty_turn(), _chatting())
    assert [name for name, _ in settles] == ["pre_router_memory"]


# --------------------------------------------------------------------------- #
# Pause / resume
# --------------------------------------------------------------------------- #


def test_a_reply_to_a_pause_reads_its_attachments_before_the_pause_check(
    frame_on, flag_off, seen_route, monkeypatch
):
    order: list[str] = []
    seen: dict[str, Any] = {}

    async def fake_ocr(*_a: Any, **_k: Any) -> SimpleNamespace:
        order.append("ocr")
        return SimpleNamespace(filled_item_ids=["reply-file"], failed=0)

    async def fake_summary(_supabase: Any, item_id: str) -> bool:
        order.append(f"summary:{item_id}")
        return True

    async def hook(*_a: Any, **_k: Any) -> None:
        order.append("pre_router_hook")

    def find_pause(*_a: Any, **_k: Any) -> dict:
        order.append("pause_check")
        return {"run_id": "run-1", "agent_family": "deep_search"}

    async def fake_resume(**kwargs: Any) -> Any:
        order.append("resume")
        seen["turn"] = helper_frame.current_turn()
        seen["memory_task"] = kwargs["memory_task"]
        yield {"type": "done"}

    monkeypatch.setattr(orch, "run_ocr_extraction", fake_ocr)
    monkeypatch.setattr(orch, "summarize_workspace_item", fake_summary)
    monkeypatch.setattr(orch.memory, "resummarize_dirty_items", hook)
    monkeypatch.setattr(orch, "_find_awaiting_user", find_pause)
    monkeypatch.setattr(orch, "_expired", lambda _p: False)
    monkeypatch.setattr(orch, "_pause_is_current", lambda *_a: True)
    monkeypatch.setattr(orch, "_resume_major_agent", fake_resume)
    _run(_empty_turn(), _chatting())

    # A resumed agent never ran the pre-router hook, and still does not.
    assert order == ["ocr", "summary:reply-file", "pause_check", "resume"]
    assert isinstance(seen["turn"], helper_frame.TurnHelpers)


def test_a_resume_leg_waits_for_the_memory_agent_through_the_frame(
    frame_on, flag_on, seen_route, memory_agent, monkeypatch, settles
):
    memory_agent.gate = asyncio.Event()
    seen: dict[str, Any] = {}

    async def fake_resume(**kwargs: Any) -> Any:
        asyncio.get_running_loop().call_later(0.02, memory_agent.gate.set)
        seen["ok"] = await orch._settle_memory_writer(
            kwargs["memory_task"], CONVO, consumer="deep_planner"
        )
        seen["done"] = kwargs["memory_task"].done()
        yield {"type": "done"}

    monkeypatch.setattr(orch, "_find_awaiting_user", lambda *_a, **_k: {"run_id": "r"})
    monkeypatch.setattr(orch, "_expired", lambda _p: False)
    monkeypatch.setattr(orch, "_pause_is_current", lambda *_a: True)
    monkeypatch.setattr(orch, "_resume_major_agent", fake_resume)
    _run(_empty_turn(), _chatting())

    assert seen == {"ok": True, "done": True}
    assert settles == [("memory", "deep_planner")]


# --------------------------------------------------------------------------- #
# Reaching the turn; the editorial pin; flag off
# --------------------------------------------------------------------------- #


def test_deep_consumers_reach_this_turns_helpers(
    frame_on, flag_on, seen_route, memory_agent, monkeypatch
):
    seen: dict[str, Any] = {}

    async def fake_writer(*_a: Any, **_k: Any) -> Any:
        turn = helper_frame.current_turn()
        seen["turn"] = turn
        # A task the specialist creates (deep search's ds_task) inherits it.
        seen["in_task"] = await asyncio.create_task(_current())
        seen["holds_memory"] = turn is not None and turn.task("memory") is not None
        return SimpleNamespace(
            kind="completed",
            result=orch.SpecialistResult(chat_summary="ok", model_used="fake"),
        )

    async def _current() -> Any:
        return helper_frame.current_turn()

    monkeypatch.setattr(orch, "_run_writer", fake_writer)
    _run(_empty_turn(), _writing())

    assert isinstance(seen["turn"], helper_frame.TurnHelpers)
    assert seen["in_task"] is seen["turn"]
    assert seen["holds_memory"]


def test_the_editorial_pin_creates_no_helpers(frame_on, monkeypatch):
    made: list[Any] = []
    seen: dict[str, Any] = {}

    async def fake_dispatch(**_kwargs: Any) -> Any:
        seen["turn"] = helper_frame.current_turn()
        yield {"type": "done"}

    monkeypatch.setattr(helper_frame, "TurnHelpers", lambda **kw: made.append(kw))
    monkeypatch.setattr(orch, "_dispatch", fake_dispatch)
    asyncio.run(_collect(orch._handle_message_inner(
        question="q", user_id=USER, conversation_id=CONVO, supabase=FakeDB(),
        pinned_plan=SimpleNamespace(agent_family="deep_search", task_label="label"),
    )))

    assert made == []
    assert seen["turn"] is None


def test_flag_off_runs_the_old_steps_and_creates_no_helpers(
    frame_off, flag_on, seen_route, memory_agent, fake_writer, monkeypatch
):
    made: list[Any] = []
    seen: dict[str, Any] = {}
    real_settle = orch._settle_memory_writer

    async def noting_settle(task: Any, conversation_id: str, **kw: Any) -> bool:
        seen["turn_at_wait"] = helper_frame.current_turn()
        return await real_settle(task, conversation_id, **kw)

    monkeypatch.setattr(helper_frame, "TurnHelpers", lambda **kw: made.append(kw))
    monkeypatch.setattr(orch, "_settle_memory_writer", noting_settle)
    events = _run(_empty_turn(), _writing())

    assert _completed(events)
    assert made == []
    assert seen["turn_at_wait"] is None
    assert not runtime._BACKGROUND


def test_a_frame_that_cannot_start_falls_back_to_the_old_steps(
    frame_on, flag_off, seen_route, monkeypatch, caplog
):
    def broken(**_kw: Any) -> None:
        raise RuntimeError("frame bug")

    calls: list[str] = []

    async def fake_ocr(*_a: Any, **_k: Any) -> SimpleNamespace:
        calls.append("ocr")
        return SimpleNamespace(filled_item_ids=[], failed=0)

    monkeypatch.setattr(helper_frame, "TurnHelpers", broken)
    monkeypatch.setattr(orch, "run_ocr_extraction", fake_ocr)
    with caplog.at_level(logging.WARNING):
        events = _run(_empty_turn(), _chatting())

    assert events[-1]["type"] == "done"
    assert calls == ["ocr"]
    assert "helper frame not started" in caplog.text


def test_handle_message_unbinds_the_turn(monkeypatch):
    async def fake_inner(**_kwargs: Any) -> Any:
        helper_frame.bind_turn(
            helper_frame.TurnHelpers(supabase=None, user_id=USER, conversation_id=CONVO)
        )
        yield {"type": "done", "usage": {}}

    monkeypatch.setattr(orch, "_handle_message_inner", fake_inner)

    async def go() -> Any:
        async for _ in orch.handle_message("q", USER, CONVO, FakeDB(), codec=object()):
            pass
        return helper_frame.current_turn()

    assert asyncio.run(go()) is None
