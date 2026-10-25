"""Phase A decider engine for the reg reranker (reranker_decider_engine.md, step 7).

Covers ``decider_rules`` (the tested request shape, the D9 keep rules, the D3
flag) and the ``run_reranker_for_query`` gate: which engine runs, the qwen
fallback, and the cap sort — unchanged on the qwen path, score-ordered on the
decider path. ``decide()`` is always monkeypatched and every httpx send is
blocked, so no test can reach OpenRouter.
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from agents.deep_search_v4.reg_compliance_search import decider_rules
from agents.deep_search_v4.reg_compliance_search import reranker as reranker_mod
from agents.deep_search_v4.reg_compliance_search.models import (
    RegKeep,
    RegRerankerClassification,
)
from agents.deep_search_v4.reg_compliance_search.reranker import run_reranker_for_query
from agents.deep_search_v4.shared import decider as decider_mod
from agents.deep_search_v4.shared.decider import DeciderReply

from .conftest import MockSupabase, chunk_content, regulation_meta_row

_MODELS = "liquid/d1-ultra,typesafe/jev-1.14"


def _answers(scores: dict[str, float], **extra) -> dict:
    out = {lab: {"score": s} for lab, s in scores.items()}
    out.update(extra)
    return out


# -- decider_rules: request shape ---------------------------------------------


def test_questions_match_the_tested_batch_shape():
    """One 5-level ``score`` question per label, keyed by the bare label, in
    label order — the ``--shape batch --levels 5`` request the A/B ran."""
    questions, required = decider_rules.build_questions(["C1", "C2", "C10"])

    assert list(questions) == ["C1", "C2", "C10"]
    assert required == ["C1", "C2", "C10"]
    q = questions["C10"]
    assert set(q) == {"type", "instructions", "criteria"}
    assert q["type"] == "score"
    assert q["instructions"] == (
        f"{decider_rules.RUBRIC5}\n\nRate ONLY the candidate whose header is `### [C10]`."
    )
    assert q["criteria"] == decider_rules.LEVELS5
    assert len(decider_rules.LEVELS5) == 5


def test_ask_sufficient_is_off_by_default():
    """D3 waits for validation A2 — until then the request is the A/B's exactly."""
    assert decider_rules.ASK_SUFFICIENT is False
    questions, required = decider_rules.build_questions(["C1"])
    assert decider_rules.SUFFICIENT_KEY not in questions
    assert decider_rules.SUFFICIENT_KEY not in required


# -- decider_rules: D9 keep rules ---------------------------------------------


def test_d9_keep_and_tier_thresholds():
    labels = ["C1", "C2", "C3", "C4", "C5", "C6"]
    answers = _answers({"C1": 0.2, "C2": 1.49, "C3": 1.5, "C4": 2.49, "C5": 2.5, "C6": 3.9})

    cls = decider_rules.classify(answers, labels)

    assert [(k.label, k.relevance) for k in cls.keeps] == [
        ("C3", "medium"), ("C4", "medium"), ("C5", "high"), ("C6", "high"),
    ]
    assert all(k.reasoning == "" for k in cls.keeps)
    assert cls.sufficient is True
    assert cls.summary_note == ""


def test_zero_keeps_is_a_valid_classification():
    """D4: nothing scores >= 1.5 → an empty keeps list, not an error."""
    cls = decider_rules.classify(_answers({"C1": 0.1, "C2": 1.2}), ["C1", "C2"])
    assert cls.keeps == []
    assert cls.sufficient is True


def test_label_scores_rejects_a_missing_score():
    with pytest.raises(ValueError):
        decider_rules.label_scores({"C1": {"score": 2.0}, "C2": {}}, ["C1", "C2"])
    with pytest.raises(ValueError):
        decider_rules.label_scores({"C1": {"score": 2.0}}, ["C1", "C2"])


# -- decider_rules: D3 "not covered" behind the flag --------------------------


def test_ask_sufficient_on_adds_the_noul_question(monkeypatch):
    monkeypatch.setattr(decider_rules, "ASK_SUFFICIENT", True)
    questions, required = decider_rules.build_questions(["C1", "C2"])

    key = decider_rules.SUFFICIENT_KEY
    assert list(questions) == ["C1", "C2", key]
    assert required == ["C1", "C2", key]
    assert questions[key]["type"] == "noul"
    assert set(questions[key]["criteria"]) == {"true", "false"}


@pytest.mark.parametrize(
    ("p", "sufficient", "note"),
    [(0.3, False, decider_rules.NOT_COVERED_NOTE), (0.5, True, ""), (0.9, True, "")],
)
def test_ask_sufficient_on_sets_sufficient_and_note(monkeypatch, p, sufficient, note):
    monkeypatch.setattr(decider_rules, "ASK_SUFFICIENT", True)
    answers = _answers({"C1": 3.0}, **{decider_rules.SUFFICIENT_KEY: {"noul": p}})

    cls = decider_rules.classify(answers, ["C1"])

    assert cls.sufficient is sufficient
    assert cls.summary_note == note
    assert [k.label for k in cls.keeps] == ["C1"]


def test_ask_sufficient_on_missing_answer_raises(monkeypatch):
    monkeypatch.setattr(decider_rules, "ASK_SUFFICIENT", True)
    with pytest.raises(ValueError):
        decider_rules.classify(_answers({"C1": 3.0}), ["C1"])


def test_ask_sufficient_off_ignores_a_stray_answer():
    answers = _answers({"C1": 3.0}, **{decider_rules.SUFFICIENT_KEY: {"noul": 0.01}})
    cls = decider_rules.classify(answers, ["C1"])
    assert cls.sufficient is True
    assert cls.summary_note == ""


# -- run_reranker_for_query harness -------------------------------------------


class _MockUsage:
    requests = 1
    input_tokens = 10
    output_tokens = 5
    total_tokens = 15
    details = {}


class _MockRunResult:
    def __init__(self, output):
        self.output = output

    def usage(self):
        return _MockUsage()


class _MockAgent:
    def __init__(self, output, sink: list[str]):
        self._output = output
        self._sink = sink

    async def run(self, msg, **_kw):
        self._sink.append(msg)
        return _MockRunResult(self._output)


def _settings(engine: str = "decider", key: str | None = "sk-or-test") -> SimpleNamespace:
    return SimpleNamespace(
        REG_RERANKER_ENGINE=engine,
        REG_RERANKER_DECIDERS=_MODELS,
        OPENROUTER_RERANKER_API_KEY=key,
    )


def _pool(rrfs: list[float]) -> tuple[MockSupabase, list[dict]]:
    """One chunk row per rrf, labelled C1..Cn in this order."""
    supa = MockSupabase(tables={
        "regulations_v2": [regulation_meta_row("reg-a")],
        "chunks_v2": [],
    })
    rows = []
    for i, rrf in enumerate(rrfs, 1):
        row = chunk_content(f"chunk-{i}", regulation_id="reg-a")
        row.update({"source_type": "regulation", "_mode": "simple", "_rrf": rrf})
        rows.append(row)
    return supa, rows


def _reply(scores: dict[str, float], fallback_from: list[str] | None = None) -> DeciderReply:
    return DeciderReply(
        model="liquid/d1-ultra",
        model_served="liquid/d1-ultra-20261015",
        answers=_answers(scores),
        usage_entry={
            "agent": "reranker", "model": "liquid/d1-ultra", "reranker_round": 1,
            "requests": 1, "input_tokens": 1234, "output_tokens": 0,
            "engine": "decider", "fallback_from": list(fallback_from or []),
        },
        fallback_from=list(fallback_from or []),
    )


@pytest.fixture
def env(monkeypatch):
    """Wires settings, a recording ``decide`` and a recording qwen agent.

    ``env.reply`` is what ``decide`` returns (a DeciderReply, None, or an
    Exception to raise); ``env.qwen_output`` is the qwen classification.
    """
    state = SimpleNamespace(
        settings=_settings(),
        reply=None,
        qwen_output=RegRerankerClassification(
            sufficient=True, keeps=[RegKeep(label="C1", relevance="high", reasoning="ق")],
            summary_note="ملاحظة qwen",
        ),
        decide_calls=[],
        qwen_msgs=[],
        agents_created=0,
    )

    async def _fake_decide(st, questions, *, required, models):
        state.decide_calls.append(
            {"state": st, "questions": questions, "required": required, "models": models}
        )
        if isinstance(state.reply, Exception):
            raise state.reply
        return state.reply

    def _fake_agent(**_kw):
        state.agents_created += 1
        return _MockAgent(state.qwen_output, state.qwen_msgs)

    async def _no_http(*_a, **_kw):
        raise AssertionError("unexpected HTTP call")

    monkeypatch.setattr(reranker_mod, "get_settings", lambda: state.settings)
    monkeypatch.setattr(decider_mod, "decide", _fake_decide)
    monkeypatch.setattr(reranker_mod, "create_reranker_agent", _fake_agent)
    monkeypatch.setattr(httpx.AsyncClient, "send", _no_http)
    return state


async def _run(rrfs: list[float], **kw):
    supa, rows = _pool(rrfs)
    trace: list[dict] = []
    kw.setdefault("max_keep", 8)
    result, usage, log = await run_reranker_for_query(
        query="مكافأة نهاية الخدمة", rationale="تجريدي", chunks=rows, supabase=supa,
        round_trace=trace, **kw,
    )
    return result, usage, log, trace


# -- Decider path ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_decider_path_serves_the_sub_query(env):
    env.reply = _reply({"C1": 3.2, "C2": 1.7, "C3": 0.4})

    result, usage, log, trace = await _run([0.9, 0.8, 0.7])

    # One request: state = the reranker user message, one question per label.
    assert len(env.decide_calls) == 1
    call = env.decide_calls[0]
    assert call["state"] == trace[0]["user_msg"]
    assert call["state"].startswith("## Sub-query")
    assert list(call["questions"]) == ["C1", "C2", "C3"]
    assert call["required"] == ["C1", "C2", "C3"]
    assert call["models"] == ["liquid/d1-ultra", "typesafe/jev-1.14"]

    # The qwen agent is never built.
    assert env.agents_created == 0

    assert [(r.db_id, r.relevance, r.reasoning) for r in result.results] == [
        ("chunk-1", "high", ""), ("chunk-2", "medium", ""),
    ]
    assert result.sufficient is True
    assert result.summary_note == ""
    assert result.dropped_count == 1
    assert usage == [env.reply.usage_entry]
    assert {d["label"]: d["action"] for d in log} == {"C1": "keep", "C2": "keep", "C3": "drop"}

    rt = trace[0]
    assert rt["engine"] == "decider"
    assert rt["model_served"] == "liquid/d1-ultra-20261015"
    assert rt["scores"] == {"C1": 3.2, "C2": 1.7, "C3": 0.4}
    assert rt["fallback_from"] == []
    assert rt["sufficient_p"] is None


@pytest.mark.asyncio
async def test_decider_records_fallback_from_a_failed_decider(env):
    env.reply = _reply({"C1": 3.0}, fallback_from=["liquid/d1-ultra: 500"])
    _result, _usage, _log, trace = await _run([0.9])
    assert trace[0]["fallback_from"] == ["liquid/d1-ultra: 500"]


@pytest.mark.asyncio
async def test_decider_zero_keeps_accepted_without_reconsider(env):
    """D4: zero keeps from the decider is final — no qwen run, no retry."""
    env.reply = _reply({"C1": 0.3, "C2": 1.1})

    result, usage, _log, trace = await _run([0.9, 0.8])

    assert result.results == []
    assert result.dropped_count == 2
    assert result.sufficient is True
    assert len(env.decide_calls) == 1
    assert env.agents_created == 0
    assert env.qwen_msgs == []
    assert usage == [env.reply.usage_entry]
    assert trace[0]["engine"] == "decider"


@pytest.mark.asyncio
async def test_explicit_engine_decider_overrides_settings(env):
    env.settings = _settings(engine="llm")
    env.reply = _reply({"C1": 3.0})

    _result, _usage, _log, trace = await _run([0.9], engine="decider")

    assert len(env.decide_calls) == 1
    assert trace[0]["engine"] == "decider"


# -- Gate: every way the qwen path runs ---------------------------------------


@pytest.mark.asyncio
async def test_flag_llm_never_calls_the_decider(env):
    env.settings = _settings(engine="llm")

    result, usage, _log, trace = await _run([0.9, 0.8])

    assert env.decide_calls == []
    assert env.agents_created == 1
    assert len(env.qwen_msgs) == 1
    assert [r.db_id for r in result.results] == ["chunk-1"]
    assert result.summary_note == "ملاحظة qwen"
    assert usage[0]["model"] != "liquid/d1-ultra"
    assert trace[0]["engine"] == "llm"
    assert trace[0]["fallback_from"] == []


@pytest.mark.asyncio
async def test_explicit_engine_llm_overrides_settings(env):
    env.reply = _reply({"C1": 3.0})
    await _run([0.9], engine="llm")
    assert env.decide_calls == []
    assert env.agents_created == 1


@pytest.mark.asyncio
async def test_model_override_forces_the_llm_path(env):
    env.reply = _reply({"C1": 3.0})
    _result, _usage, _log, trace = await _run([0.9], model_override="qwen")
    assert env.decide_calls == []
    assert env.agents_created == 1
    assert trace[0]["engine"] == "llm"


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, "", "   "])
async def test_missing_key_forces_the_llm_path(env, key):
    env.settings = _settings(key=key)
    env.reply = _reply({"C1": 3.0})
    await _run([0.9])
    assert env.decide_calls == []
    assert env.agents_created == 1


@pytest.mark.asyncio
async def test_decide_none_falls_back_to_qwen(env):
    env.reply = None

    result, usage, _log, trace = await _run([0.9, 0.8])

    assert len(env.decide_calls) == 1
    assert env.agents_created == 1
    assert len(env.qwen_msgs) == 1
    # qwen sees the exact message the decider got as state.
    assert env.qwen_msgs[0] == env.decide_calls[0]["state"]
    assert [r.db_id for r in result.results] == ["chunk-1"]
    assert len(usage) == 1                       # qwen's entry only
    assert trace[0]["engine"] == "llm"
    assert trace[0]["fallback_from"]             # the failed chain is recorded


@pytest.mark.asyncio
async def test_decide_raising_falls_back_to_qwen(env):
    env.reply = RuntimeError("boom")
    result, _usage, _log, trace = await _run([0.9])
    assert env.agents_created == 1
    assert [r.db_id for r in result.results] == ["chunk-1"]
    assert trace[0]["engine"] == "llm"


@pytest.mark.asyncio
async def test_malformed_reply_falls_back_to_qwen(env):
    """A reply missing a candidate's score is a failure, never a silent drop."""
    env.reply = _reply({"C1": 3.0})               # C2 unscored
    result, _usage, _log, trace = await _run([0.9, 0.8])
    assert env.agents_created == 1
    assert trace[0]["engine"] == "llm"
    assert [r.db_id for r in result.results] == ["chunk-1"]


# -- Cap sort -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_cap_sort_unchanged_on_the_llm_path(env):
    """qwen keeps sort (tier, -rrf) exactly as before — score is 0 for all."""
    env.settings = _settings(engine="llm")
    env.qwen_output = RegRerankerClassification(
        sufficient=True,
        keeps=[
            RegKeep(label="C4", relevance="high", reasoning="ا"),
            RegKeep(label="C1", relevance="medium", reasoning="ب"),
            RegKeep(label="C3", relevance="high", reasoning="ج"),
            RegKeep(label="C2", relevance="medium", reasoning="د"),
        ],
        summary_note="",
    )
    rrfs = [0.9, 0.8, 0.7, 0.6]

    result, _usage, _log, _trace = await _run(rrfs, max_keep=3)

    # Pre-change key: (0 if high else 1, -rrf).
    rel = {k.label: k.relevance for k in env.qwen_output.keeps}
    rrf = {f"C{i}": r for i, r in enumerate(rrfs, 1)}
    expected = sorted(rel, key=lambda lab: (0 if rel[lab] == "high" else 1, -rrf[lab]))[:3]
    assert [r.db_id for r in result.results] == [f"chunk-{lab[1:]}" for lab in expected]
    assert [r.db_id for r in result.results] == ["chunk-3", "chunk-4", "chunk-1"]
    assert result.caps_applied["truncated_by_cap"] == 1


@pytest.mark.asyncio
async def test_cap_sort_orders_decider_keeps_by_score_within_tier(env):
    """Decider path: (tier, -score, -rrf) — the higher score wins a cap slot
    over the higher retrieval rank."""
    env.reply = _reply({"C1": 2.6, "C2": 3.5, "C3": 1.6, "C4": 2.0})

    result, _usage, _log, _trace = await _run([0.9, 0.8, 0.7, 0.6], max_keep=3)

    assert [(r.db_id, r.relevance) for r in result.results] == [
        ("chunk-2", "high"), ("chunk-1", "high"), ("chunk-4", "medium"),
    ]
    assert [d["db_id"] for d in result.dropped_results if d["drop_reason"] == "cap"] == ["chunk-3"]
