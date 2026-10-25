"""Tests for ``shared/decider.py`` — the OpenRouter Decisions fallback chain.

Plan: ``.claude/plans/reranker_decider_engine.md`` build step 7 (D2, D6). No
network: every request goes through an ``httpx.MockTransport`` injected via
``decider._make_client``. What is pinned here:

- the request body (model, state, questions, ZDR provider block) and the key;
- the D2 chain: retry once, then the next model; 402 stops everything; a
  400 is not retried; a missing or null required answer is a failure;
- the token guard skips d1-ultra without a call;
- no dedicated key → ``None`` and no HTTP call at all (D6);
- the usage entry the orchestrator's ledger reads.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from agents.deep_search_v4.shared import decider
from agents.deep_search_v4.shared.decider import DeciderReply, decide, estimate_tokens, parse_models

D1, JEV = "liquid/d1-ultra", "typesafe/jev-1.14"
MODELS = [D1, JEV]
STATE = "## Sub-query\n\nما شروط فسخ العقد؟\n\n### [1]\n...\n### [2]\n..."
QUESTIONS = {
    "R1": {"type": "score", "instructions": "Rate ONLY `### [1]`.", "criteria": ["no", "yes"]},
    "R2": {"type": "score", "instructions": "Rate ONLY `### [2]`.", "criteria": ["no", "yes"]},
    "M1": {"type": "noul", "instructions": "Same mechanism?", "criteria": {"true": "y", "false": "n"}},
}
REQUIRED = ["R1", "R2", "M1"]
ANSWERS = {"R1": {"score": 3.1, "p": {"0": 0.1}}, "R2": {"score": 0.4}, "M1": {"noul": 0.8}}


def _ok(model: str, answers: dict | None = None, tokens: int = 1200) -> httpx.Response:
    return httpx.Response(200, json={
        "answers": ANSWERS if answers is None else answers,
        "model": f"{model}-20261015",
        "usage": {"input_tokens": tokens, "cost": tokens * 0.042e-6},
    })


class _Api:
    """Scripted Decisions API: per model, a queue of responses (or exceptions)."""

    def __init__(self, script: dict[str, list]) -> None:
        self.script = {m: list(rs) for m, rs in script.items()}
        self.calls: list[dict] = []
        self.headers: list[httpx.Headers] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append(body)
        self.headers.append(request.headers)
        step = self.script[body["model"]].pop(0)
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return await step()
        return step

    @property
    def models(self) -> list[str]:
        return [c["model"] for c in self.calls]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """A key, no retry pause, and a fresh client per test."""
    monkeypatch.setattr("shared.config.get_settings",
                        lambda: SimpleNamespace(OPENROUTER_RERANKER_API_KEY="sk-or-test"))
    monkeypatch.setattr(decider, "_RETRY_DELAY_S", 0)
    monkeypatch.setattr(decider, "_client", None)
    monkeypatch.setattr(decider, "_client_loop", None)


def _install(monkeypatch, script: dict[str, list]) -> _Api:
    api = _Api(script)
    monkeypatch.setattr(decider, "_make_client",
                        lambda: httpx.AsyncClient(transport=httpx.MockTransport(api)))
    return api


async def _decide(state: str = STATE) -> DeciderReply | None:
    return await decide(state, QUESTIONS, required=REQUIRED, models=MODELS)


# ===========================================================================
# The seven plan cases
# ===========================================================================


@pytest.mark.asyncio
async def test_d1_succeeds(monkeypatch) -> None:
    api = _install(monkeypatch, {D1: [_ok(D1, tokens=10559)]})
    reply = await _decide()

    assert reply is not None
    assert (reply.model, reply.model_served) == (D1, "liquid/d1-ultra-20261015")
    assert reply.answers == ANSWERS
    assert reply.fallback_from == []
    assert reply.usage_entry == {
        "agent": "reranker", "model": D1, "reranker_round": 1, "requests": 1,
        "input_tokens": 10559, "output_tokens": 0, "total_tokens": 10559,
        "engine": "decider", "fallback_from": [],
    }
    assert api.models == [D1]
    assert api.calls[0] == {"model": D1, "state": STATE, "questions": QUESTIONS,
                            "provider": {"zdr": True, "data_collection": "deny"}}
    assert api.headers[0]["authorization"] == "Bearer sk-or-test"


@pytest.mark.asyncio
async def test_d1_500_twice_falls_to_jev(monkeypatch) -> None:
    api = _install(monkeypatch, {D1: [httpx.Response(500), httpx.Response(500)], JEV: [_ok(JEV, tokens=2000)]})
    reply = await _decide()

    assert reply is not None and reply.model == JEV
    assert api.models == [D1, D1, JEV]
    assert reply.fallback_from == [f"{D1}: 500"]
    assert reply.usage_entry["model"] == JEV
    assert reply.usage_entry["requests"] == 1
    assert reply.usage_entry["input_tokens"] == 2000
    assert reply.usage_entry["fallback_from"] == [f"{D1}: 500"]


@pytest.mark.asyncio
async def test_both_fail_returns_none(monkeypatch) -> None:
    """d1 5xx twice, jev past the per-attempt timeout twice → None (caller runs qwen)."""
    async def slow() -> httpx.Response:
        await asyncio.sleep(1)
        return _ok(JEV)

    monkeypatch.setattr(decider, "_TIMEOUT_S", 0.05)
    api = _install(monkeypatch, {D1: [httpx.Response(503), httpx.Response(502)], JEV: [slow, slow]})

    assert await _decide() is None
    assert api.models == [D1, D1, JEV, JEV]


@pytest.mark.asyncio
async def test_d1_402_stops_without_jev(monkeypatch) -> None:
    api = _install(monkeypatch, {D1: [httpx.Response(402, json={"error": {"message": "credits"}})],
                                 JEV: [_ok(JEV)]})

    assert await _decide() is None
    assert api.models == [D1]  # no retry, and jev bills the same account


@pytest.mark.asyncio
async def test_missing_required_key_is_a_failure(monkeypatch) -> None:
    """R2 absent on the try, null on the retry → d1 failed; jev answers."""
    absent = {k: v for k, v in ANSWERS.items() if k != "R2"}
    null = {**ANSWERS, "R2": {"score": None}}
    api = _install(monkeypatch, {D1: [_ok(D1, absent), _ok(D1, null)], JEV: [_ok(JEV)]})
    reply = await _decide()

    assert reply is not None and reply.model == JEV
    assert api.models == [D1, D1, JEV]
    assert reply.fallback_from == [f"{D1}: missing R2"]


@pytest.mark.asyncio
async def test_token_guard_skips_d1(monkeypatch) -> None:
    big = "ب" * 100_000  # ≈ 30k estimated tokens at d1-ultra's 3.3 chars/token
    assert estimate_tokens(big, QUESTIONS, D1) > 28_000
    assert estimate_tokens(big, QUESTIONS, JEV) < 240_000
    api = _install(monkeypatch, {JEV: [_ok(JEV)]})
    reply = await _decide(big)

    assert reply is not None and reply.model == JEV
    assert api.models == [JEV]  # d1-ultra never called
    assert reply.fallback_from == [f"{D1}: tokens>28k"]


@pytest.mark.asyncio
async def test_no_key_returns_none_without_http(monkeypatch) -> None:
    monkeypatch.setattr("shared.config.get_settings",
                        lambda: SimpleNamespace(OPENROUTER_RERANKER_API_KEY=None))
    api = _install(monkeypatch, {D1: [_ok(D1)], JEV: [_ok(JEV)]})

    assert await _decide() is None
    assert api.calls == []
    assert decider._client is None  # the client was never even built


# ===========================================================================
# Edges of D2
# ===========================================================================


@pytest.mark.asyncio
async def test_400_is_not_retried(monkeypatch) -> None:
    api = _install(monkeypatch, {D1: [httpx.Response(400, json={"error": "bad"})], JEV: [_ok(JEV)]})
    reply = await _decide()

    assert reply is not None and reply.model == JEV
    assert api.models == [D1, JEV]
    assert reply.fallback_from == [f"{D1}: 400"]


@pytest.mark.asyncio
async def test_retry_succeeds_on_same_model_and_sums_billed_tokens(monkeypatch) -> None:
    """An incomplete 200 (still billed) then a good one: both requests' tokens
    land on the one usage entry, and the model didn't fail."""
    absent = {k: v for k, v in ANSWERS.items() if k != "M1"}
    api = _install(monkeypatch, {D1: [_ok(D1, absent, tokens=900), _ok(D1, tokens=1000)]})
    reply = await _decide()

    assert reply is not None and reply.model == D1
    assert api.models == [D1, D1]
    assert reply.fallback_from == []
    assert (reply.usage_entry["requests"], reply.usage_entry["input_tokens"]) == (2, 1900)


@pytest.mark.asyncio
async def test_transport_error_then_choice_answer(monkeypatch) -> None:
    """A connect error is retried; a ``choice`` answer needs ``probabilities``."""
    qs = {"FORUM": {"type": "choice", "instructions": "Which court?", "criteria": {"a": "x", "b": "y"}}}
    good = httpx.Response(200, json={"answers": {"FORUM": {"probabilities": {"a": 0.7, "b": 0.3}}},
                                     "model": D1, "usage": {"input_tokens": 50}})
    api = _install(monkeypatch, {D1: [httpx.ConnectError("boom"), good]})
    reply = await decide(STATE, qs, required=["FORUM"], models=[D1])

    assert reply is not None and reply.answers["FORUM"]["probabilities"]["a"] == 0.7
    assert api.models == [D1, D1]


def test_parse_models_keeps_order() -> None:
    assert parse_models(" liquid/d1-ultra , typesafe/jev-1.14,,") == MODELS
    assert parse_models(None) == []
