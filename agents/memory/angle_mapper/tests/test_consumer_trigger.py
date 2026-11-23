"""The trigger gap (2026-11-22): a CONSUMER (the aggregator / writer, through
``ensure_ready("segmenter", …)``) runs the segmenter — the message's angle map is
still made, once per group, by the real ``ANGLE_MAP`` helper and the real runner,
in its OWN ledger scope (keyed to the group), never touching the segmenter's
result or the consumer. The segmenter's run is a fake that stores real segment
maps; the model is a ``FunctionModel``; the database is the fake client."""
from __future__ import annotations

import asyncio
import dataclasses
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agents.helpers import _runtime, ensure_ready, status
from agents.helpers.registry import REGISTRY, SEGMENTER
from agents.memory.angle_mapper import agent as agent_mod
from agents.memory.angle_mapper import load_fresh_angle_map_for_item
from agents.memory.angle_mapper import runner as runner_mod
from agents.utils import angle_map_flags, usage_sink
from agents.utils.usage_sink import collect_llm_calls

from . import fixtures as F
from .fake_supabase import FakeSupabase

A, B = F.ITEM_A, F.ITEM_B
DOCS = {A: F.JUDGMENT, B: F.OBJECTION}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(_runtime, "_INFLIGHT", {})
    monkeypatch.setattr(_runtime, "_CONVO_LATEST", {})
    monkeypatch.setattr(runner_mod, "_GROUP_TASKS", {})
    monkeypatch.setattr(runner_mod, "_codec_for", lambda supabase, uid: None)
    monkeypatch.setattr(runner_mod, "angle_map_enabled", lambda uid: True)
    monkeypatch.setattr(angle_map_flags, "angle_map_enabled", lambda uid: True)  # the frame's gate


@pytest.fixture
def ledger(monkeypatch) -> list[list[dict]]:
    flushed: list[list[dict]] = []
    monkeypatch.setattr(usage_sink, "_flush", lambda supabase, buf: flushed.append([dict(r) for r in buf or []]))
    return flushed


def model(monkeypatch, answer=F.good_two) -> list[Any]:
    calls: list[Any] = []

    async def fn(messages: list, info: AgentInfo) -> ModelResponse:
        calls.append(messages)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, answer())])

    monkeypatch.setattr(agent_mod, "get_agent_model", lambda slot: FunctionModel(fn))
    return calls


def segmenter_registry(db: FakeSupabase, delays: dict[str, float], *, fail: tuple[str, ...] = ()) -> dict:
    """The real registry, its segmenter's run swapped for one that stores the
    fixture's segment map (seg-v5.2) after ``delays[item]`` seconds."""

    async def run(ctx: Any) -> Any:
        await asyncio.sleep(delays.get(ctx.item_id, 0.0))
        if ctx.item_id in fail:
            raise RuntimeError("provider down")
        doc = DOCS[ctx.item_id]
        for k, v in F.map_tables(doc, F.content(doc, big=True)).items():
            db.tables[k] += v
        return SimpleNamespace(outcome="ok", error="")

    seg = dataclasses.replace(SEGMENTER, run=run, enabled=lambda uid: True)
    return {**REGISTRY, "segmenter": seg}


def stored_maps(db: FakeSupabase) -> list[tuple[str | None, list[str]]]:
    out = []
    for m in db.tables["attachment_angle_maps"]:
        rows = sorted((r for r in db.tables["attachment_angle_map_items"] if r["map_id"] == m["map_id"]),
                      key=lambda r: r["doc_index"])
        out.append((m["message_id"], [r["item_id"] for r in rows]))
    return out


def runs(db: FakeSupabase, helper: str) -> dict[str, str]:
    return {r["item_id"]: r["status"] for r in db.tables["helper_runs"] if r["helper"] == helper}


async def drain() -> None:
    tasks = {t for t in (_runtime.inflight("angle_map", i) for i in (A, B)) if t is not None}
    if tasks:
        await asyncio.wait(tasks, timeout=10)


async def consumer(db: FakeSupabase, reg: dict, turn_message: str = "turn-2") -> dict:
    """The aggregator's wait, inside its turn's own ``llm_calls`` scope."""
    with collect_llm_calls(db, conversation_id=F.CONVO, user_id=F.USER, message_id=turn_message):
        return await ensure_ready("segmenter", [A, B], consumer="aggregator", supabase=db, user_id=F.USER,
                                  registry=reg)


@pytest.mark.asyncio
@pytest.mark.parametrize("delays", [{A: 0.0, B: 0.05}, {A: 0.05, B: 0.0}, {A: 0.0, B: 0.0}])
async def test_a_consumer_run_segmenter_gets_its_message_mapped_once(monkeypatch, ledger, delays):
    db = FakeSupabase(F.tables(F.JUDGMENT, F.OBJECTION, big=DOCS.values(), unmapped=DOCS.values()))
    calls = model(monkeypatch)
    reg = segmenter_registry(db, delays)

    out = await consumer(db, reg)
    assert {k: r.status for k, r in out.items()} == {A: "ready", B: "ready"}  # the consumer: untouched
    await drain()

    assert len(calls) == 1  # one model call for the group, whichever member ran it
    assert stored_maps(db) == [(F.MSG, [A, B])]
    assert runs(db, "segmenter") == {A: "done", B: "done"}
    assert runs(db, "angle_map") == {A: "done", B: "done"}  # pending / joined / stored: none failed
    assert load_fresh_angle_map_for_item(db, A)["map"]["map_id"] == load_fresh_angle_map_for_item(db, B)["map"]["map_id"]

    # the consumer's scope got nothing; the map's cost landed in ITS OWN scope, keyed to the group
    rows = [r for flush in ledger for r in flush]
    assert [r["agent"] for r in rows] == ["angle_mapper"]
    assert (rows[0]["user_id"], rows[0]["conversation_id"], rows[0]["message_id"]) == (F.USER, F.CONVO, F.MSG)

    # a later consumer finds the segment maps stored: no segmenter run, no angle-map run
    await consumer(db, reg, "turn-3")
    assert _runtime.inflight("angle_map", A) is None and _runtime.inflight("angle_map", B) is None
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_member_whose_segmenter_failed_last_still_maps_the_rest(monkeypatch, ledger):
    """B's segmenter fails after A's ended: A's run waited for B (pending); B's run
    — started although its segmentation failed — maps A."""
    db = FakeSupabase(F.tables(F.JUDGMENT, F.OBJECTION, big=DOCS.values(), unmapped=DOCS.values()))
    calls = model(monkeypatch, F.good_one)
    reg = segmenter_registry(db, {A: 0.0, B: 0.05}, fail=(B,))

    out = await consumer(db, reg)
    assert (out[A].status, out[B].status, out[B].reason) == ("ready", "fallback", "failed")
    await drain()
    assert len(calls) == 1 and stored_maps(db) == [(F.MSG, [A])]
    assert runs(db, "segmenter") == {A: "done", B: "failed"}


@pytest.mark.asyncio
async def test_flag_off_no_angle_map_is_started(monkeypatch, ledger):
    monkeypatch.setattr(angle_map_flags, "angle_map_enabled", lambda uid: False)
    db = FakeSupabase(F.tables(F.JUDGMENT, F.OBJECTION, big=DOCS.values(), unmapped=DOCS.values()))
    calls = model(monkeypatch)
    out = await consumer(db, segmenter_registry(db, {}))
    assert {k: r.status for k, r in out.items()} == {A: "ready", B: "ready"}
    assert _runtime.inflight("angle_map", A) is None and _runtime.inflight("angle_map", B) is None
    assert calls == [] and runs(db, "angle_map") == {} and stored_maps(db) == []


@pytest.mark.asyncio
@pytest.mark.filterwarnings("error::RuntimeWarning")  # a refused start leaves no un-awaited coroutine
async def test_a_broken_angle_map_start_never_reaches_the_consumer(monkeypatch, ledger):
    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("spawn refused")

    monkeypatch.setattr(status, "spawn", boom)  # the dependents' spawn only — the segmenter's is start_claimed's
    monkeypatch.setattr(status, "start_claimed", _start_claimed_with_real_spawn)
    db = FakeSupabase(F.tables(F.JUDGMENT, F.OBJECTION, big=DOCS.values(), unmapped=DOCS.values()))
    calls = model(monkeypatch)
    out = await consumer(db, segmenter_registry(db, {}))
    assert {k: r.status for k, r in out.items()} == {A: "ready", B: "ready"}
    assert calls == [] and runs(db, "angle_map") == {}


def _start_claimed_with_real_spawn(spec, ctx, runner, *, store, row, after=None):
    return _runtime.spawn(
        spec.name, str(ctx.item_id),
        _runtime.execute(spec, status._with_attempt(ctx, row), runner, check_applicable=False,
                         store=store, row=row, after=after),
    )
