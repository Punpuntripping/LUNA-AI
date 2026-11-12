"""Fixes from the independent review (2026-11-12): ledger scope, failed runs, gate order."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agents.helpers import _runtime, registry, status
from agents.helpers.spec import HelperContext, HelperSpec
from agents.utils import usage_sink


def test_detached_leaves_any_open_scope_for_the_block():
    token = usage_sink._buffer.set([])
    try:
        assert usage_sink.in_scope()
        with usage_sink.detached():
            assert not usage_sink.in_scope()
        assert usage_sink.in_scope()
    finally:
        usage_sink._buffer.reset(token)


def _spec(**kw) -> HelperSpec:
    fields = dict(name="segmenter", scope="item", critical_for=frozenset({"writer"}),
                  own_ledger_scope=True, stored=True)
    fields.update(kw)
    return HelperSpec(**fields)


def test_a_run_without_a_conversation_runs_detached_from_the_turn_scope():
    """Started by ensure_ready outside a turn handle: rows must not land in a turn
    buffer that may flush first — the segmenter opens its own scope instead."""
    ctx = HelperContext(helper="segmenter", supabase=None, user_id="u", item_id="a")
    token = usage_sink._buffer.set([])
    try:
        with _runtime._ledger_scope(_spec(), ctx):
            assert not usage_sink.in_scope()
    finally:
        usage_sink._buffer.reset(token)


def test_failed_segmenter_outcomes_raise(monkeypatch):
    import agents.memory.attachment_segmenter as seg

    async def fake(_sb, _iid):
        return SimpleNamespace(outcome="failed_llm", error="TimeoutError")

    monkeypatch.setattr(seg, "run_attachment_segmenter", fake)
    ctx = HelperContext(helper="segmenter", supabase=None, user_id="u", item_id="a")
    with pytest.raises(registry.SegmenterFailed, match="failed_llm"):
        asyncio.run(registry.run_segmenter(ctx))


@pytest.mark.parametrize("outcome", ["ok", "skipped_short", "skipped_fresh", "skipped_disabled"])
def test_good_or_skipped_outcomes_return(monkeypatch, outcome):
    import agents.memory.attachment_segmenter as seg

    async def fake(_sb, _iid):
        return SimpleNamespace(outcome=outcome, error="")

    monkeypatch.setattr(seg, "run_attachment_segmenter", fake)
    ctx = HelperContext(helper="segmenter", supabase=None, user_id="u", item_id="a")
    assert asyncio.run(registry.run_segmenter(ctx)).outcome == outcome


def test_decide_checks_the_gate_before_reading_a_stored_result():
    reads: list[str] = []
    spec = _spec(enabled=lambda _u: False, load=lambda _sb, iid: reads.append(iid) or "map")
    ctx = HelperContext(helper="segmenter", supabase=None, user_id="u", item_id="a")
    verdict, reason = asyncio.run(status.decide(spec, ctx, None, claim=True))
    assert (verdict, reason) == ("not_applicable", "disabled")
    assert reads == []


def test_segmenter_version_never_breaks_the_import(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def boom(name, *a, **k):
        if name == "agents.memory.attachment_segmenter":
            raise ImportError("broken package")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", boom)
    assert registry._segmenter_version() == "unavailable"
