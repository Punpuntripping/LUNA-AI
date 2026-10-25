"""Phase B of the reranker decider engine — case-law rules + the reranker gate.

Plan: `.claude/plans/reranker_decider_engine.md` (build steps 4, 5, 7; D4, D10–D13).

Guards:

1. **D10 / D11 rules** — rank = score × P(mech), keep / high cuts per decider id
   (d1-ultra 1.7 / 2.23, jev-1.14 0.75 / 1.50), the top-3 floor at raw >= 1.0,
   ties to the better search position, the `max_keep` cap (incl. 4 and 5, D12).
2. **The request** — the state has no max_keep line, the channel rubric comes
   from the rationale prefix, and `required` covers every R{p} / M{p}.
3. **The gate** — flag "llm", a `model_override`, no key, and decide() → None
   (or raising) all take today's qwen path; the decider path never builds the
   qwen agent, never nudges on zero keeps, and bills the decider id.

No HTTP: `decide()` and settings are monkeypatched.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agents.deep_search_v4.case_search import decider_rules as dr
from agents.deep_search_v4.case_search import reranker as reranker_mod
from agents.deep_search_v4.case_search.models import (
    CaseKeep,
    CaseRerankerClassification,
)
from agents.deep_search_v4.case_search.prompts import build_reranker_user_message
from agents.deep_search_v4.case_search.reranker import run_reranker_for_query
from agents.deep_search_v4.shared.decider import DeciderReply

D1 = "liquid/d1-ultra"
JEV = "typesafe/jev-1.14"


def _keeps(cl: CaseRerankerClassification) -> list[tuple[int, str]]:
    return [(k.position, k.relevance) for k in cl.keeps]


# -- (1) Rules ---------------------------------------------------------------


def test_d1_keep_and_high_cuts():
    scores = {
        1: (3.0, 0.80),   # rank 2.40 → high
        2: (2.23, 1.0),   # rank 2.23 → high (boundary)
        3: (2.0, 0.85),   # rank 1.70 → medium (keep boundary)
        4: (2.0, 0.84),   # rank 1.68 → dropped
        5: (0.9, 1.0),    # rank 0.90 → dropped
    }
    cl = dr.classify(scores, model=D1, max_keep=7)
    assert _keeps(cl) == [(1, "high"), (2, "high"), (3, "medium")]


def test_jev_uses_its_own_cuts():
    scores = {
        1: (1.6, 1.0),    # 1.60 → jev high (>= 1.50); below d1's keep cut
        2: (1.5, 0.5),    # 0.75 → jev keep boundary
        3: (1.0, 0.74),   # 0.74 → jev drop
        4: (1.2, 1.0),    # 1.20 → jev medium
    }
    jev = dr.classify(scores, model=JEV, max_keep=7)
    assert _keeps(jev) == [(1, "high"), (4, "medium"), (2, "medium")]
    # The same answers under d1-ultra's cuts: nothing reaches 1.7 → the raw-score floor.
    d1 = dr.classify(scores, model=D1, max_keep=7)
    assert _keeps(d1) == [(1, "medium"), (4, "medium"), (2, "medium")]


def test_floor_tops_up_to_three_from_raw_score_at_least_one():
    scores = {
        1: (3.0, 0.9),    # rank 2.70 → kept, high
        2: (0.95, 1.0),   # rank 0.95, raw < 1.0 → never a floor pick
        3: (1.0, 0.5),    # rank 0.50, raw 1.0 → floor
        4: (2.0, 0.2),    # rank 0.40, raw 2.0 → floor
        5: (1.5, 0.1),    # rank 0.15 → below the floor's top 3
    }
    cl = dr.classify(scores, model=D1, max_keep=7)
    assert _keeps(cl) == [(1, "high"), (3, "medium"), (4, "medium")]


def test_floor_needs_raw_score_one_and_zero_keeps_is_valid():
    scores = {1: (0.99, 1.0), 2: (0.5, 1.0), 3: (0.2, 0.0)}
    cl = dr.classify(scores, model=D1, max_keep=7)
    assert cl.keeps == []   # D4: accepted as is


def test_floor_fills_what_it_can():
    scores = {1: (1.2, 0.1), 2: (0.4, 0.9), 3: (0.3, 0.3)}
    assert _keeps(dr.classify(scores, model=D1, max_keep=7)) == [(1, "medium")]


def test_floor_not_applied_at_three_keeps():
    scores = {p: (2.0, 1.0) for p in (1, 2, 3)} | {4: (3.0, 0.1)}
    assert [p for p, _ in _keeps(dr.classify(scores, model=D1, max_keep=7))] == [1, 2, 3]


@pytest.mark.parametrize("max_keep", [4, 5, 7])
def test_cap_at_max_keep_in_rank_order(max_keep):
    # Ten candidates all above the keep cut, rank rising with position.
    scores = {p: (1.5 + p * 0.25, 1.0) for p in range(1, 11)}
    cl = dr.classify(scores, model=D1, max_keep=max_keep)
    assert [k.position for k in cl.keeps] == list(range(10, 10 - max_keep, -1))


@pytest.mark.parametrize("max_keep", [2, 4, 5])
def test_cap_applies_after_the_floor(max_keep):
    # Only 1 passes; the floor takes the best-ranked with raw >= 1.0 (5 is raw 0.5;
    # 2 and 4 tie at rank 0.1 → position 2 first), then the cap.
    scores = {1: (3.0, 1.0), 2: (1.0, 0.1), 3: (1.1, 0.1), 4: (2.0, 0.05), 5: (0.5, 0.9)}
    cl = dr.classify(scores, model=D1, max_keep=max_keep)
    assert _keeps(cl) == [(1, "high"), (3, "medium"), (2, "medium")][:max_keep]


def test_ties_go_to_the_better_search_position():
    scores = {5: (2.0, 1.0), 2: (2.0, 1.0), 9: (2.0, 1.0), 1: (2.5, 0.8)}   # all rank 2.0
    cl = dr.classify(scores, model=D1, max_keep=3)
    assert [k.position for k in cl.keeps] == [1, 2, 5]


def test_unknown_decider_has_no_thresholds():
    with pytest.raises(ValueError):
        dr.classify({1: (3.0, 1.0)}, model="other/model", max_keep=7)


def test_classification_shape_d13():
    cl = dr.classify({1: (3.0, 1.0), 2: (2.0, 0.9)}, model=D1, max_keep=7)
    assert cl.sufficient is True
    assert cl.query_axes == []
    assert cl.summary_note == ""
    assert all(k.reasoning == "" and k.satisfies_axes == [] for k in cl.keeps)
    # Round-trips through the model the qwen agent outputs.
    assert CaseRerankerClassification.model_validate(cl.model_dump()) == cl


def test_scores_from_answers_and_missing_answer():
    answers = {"R1": {"score": 2.5}, "M1": {"noul": 0.4}, "R2": {"score": "1"}, "M2": {"noul": 1}}
    assert dr.scores_from_answers(answers, [1, 2]) == {1: (2.5, 0.4), 2: (1.0, 1.0)}
    with pytest.raises(KeyError):
        dr.scores_from_answers({"R1": {"score": 2.5}}, [1])
    with pytest.raises(TypeError):
        dr.scores_from_answers({"R1": {"score": None}, "M1": {"noul": 0.5}}, [1])


# -- (2) Request -------------------------------------------------------------


@pytest.mark.parametrize(
    ("rationale", "channel"),
    [
        ("[principle] المبدأ العام", "principle"),
        ("[facts] وقائع مشابهة", "facts"),
        ("[basis] ", "basis"),
        ("بلا قناة", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_channel_from_rationale_prefix(rationale, channel):
    assert dr.channel_of(rationale) == channel


def test_questions_carry_the_channel_rubric_and_every_position():
    questions, required = dr.build_questions([1, 2, 3], "facts")
    assert list(questions) == ["R1", "M1", "R2", "M2", "R3", "M3"]
    assert required == list(questions)
    r2 = questions["R2"]
    assert r2["type"] == "score" and r2["criteria"] == dr.LEVELS_V2
    assert r2["instructions"] == (
        f"{dr.RUBRIC_V2_HEAD}\n\n{dr.RUBRIC_V2_CHANNEL['facts']}\n\n"
        "Rate ONLY the ruling whose header is `### [2]`."
    )
    m3 = questions["M3"]
    assert m3["type"] == "noul" and m3["criteria"] == dr.MECH_CRITERIA
    assert m3["instructions"].startswith(dr.MECH_Q)
    assert m3["instructions"].endswith("`### [3]`.")


def test_unknown_channel_gets_the_head_only():
    questions, _ = dr.build_questions([1], "")
    assert questions["R1"]["instructions"].startswith(f"{dr.RUBRIC_V2_HEAD}\n\n\n\nRate ONLY")


def test_state_has_no_max_keep_line_and_keeps_the_brief():
    state = dr.build_state("س", "[basis] ر", "### [1]\n", planner_brief="موجز")
    assert state == build_reranker_user_message("س", "[basis] ر", "### [1]\n", planner_brief="موجز")
    assert "quota" not in state
    assert state.startswith("<planner_brief>")


# -- (3) The reranker gate -----------------------------------------------------

_MD = (
    "## نتائج البحث في السوابق القضائية (q1_principle) — 4 نتيجة\n\n"
    "### [1]\nالمحكمة: التجارية — الرياض (ابتدائي)\nالموضوعات المطابقة:\n- [مبدأ · موضوعي] أ\n\n"
    "### [2]\nالمحكمة: التجارية — جدة (استئناف)\nالموضوعات المطابقة:\n- [مبدأ · موضوعي] ب\n\n"
    "### [3]\nالمحكمة: العامة — الدمام (عليا)\nالموضوعات المطابقة:\n- [مبدأ · موضوعي] ج\n\n"
    "### [4]\nالمحكمة: العمالية — الرياض (ابتدائي)\nالموضوعات المطابقة:\n- [مبدأ · موضوعي] د\n"
)
_RATIONALE = "[principle] المبدأ العام في الفسخ"
# rank: 1 → 2.7 (high), 2 → 1.8 (medium), 3 → 0.4, 4 → 0.1 (raw 0.5)
_ANSWERS = {
    "R1": {"score": 3.0}, "M1": {"noul": 0.9},
    "R2": {"score": 2.0}, "M2": {"noul": 0.9},
    "R3": {"score": 2.0}, "M3": {"noul": 0.2},
    "R4": {"score": 0.5}, "M4": {"noul": 0.2},
}


def _settings(engine="decider", key="k"):
    return SimpleNamespace(
        CASE_RERANKER_ENGINE=engine,
        CASE_RERANKER_DECIDERS=f"{D1},{JEV}",
        OPENROUTER_RERANKER_API_KEY=key,
    )


def _reply(model=D1, answers=None, fallback_from=None):
    return DeciderReply(
        model=model,
        model_served=f"{model}-20261015",
        answers=answers or _ANSWERS,
        usage_entry={"agent": "reranker", "model": model, "reranker_round": 1, "requests": 1,
                     "input_tokens": 1234, "output_tokens": 0, "engine": "decider",
                     "fallback_from": list(fallback_from or [])},
        fallback_from=list(fallback_from or []),
    )


class _Usage:
    requests = 1
    input_tokens = 10
    output_tokens = 5
    total_tokens = 15
    details = {}


class _Result:
    def __init__(self, output):
        self.output = output

    def usage(self):
        return _Usage()


class _Agent:
    def __init__(self, sink: list[str]):
        self._sink = sink

    async def run(self, msg, **_kw):
        self._sink.append(msg)
        return _Result(CaseRerankerClassification(
            sufficient=False, keeps=[CaseKeep(position=4, relevance="medium", reasoning="qwen")],
            summary_note="ملاحظة qwen",
        ))


@pytest.fixture
def harness(monkeypatch):
    """Patch settings, decide() and the qwen agent; record what each saw."""
    h = SimpleNamespace(settings=_settings(), reply=_reply(), raises=None,
                        decide_calls=[], agent_msgs=[], agents_built=0)

    async def fake_decide(state, questions, *, required, models):
        h.decide_calls.append({"state": state, "questions": questions,
                               "required": required, "models": models})
        if h.raises:
            raise h.raises
        return h.reply

    def fake_agent(**_kw):
        h.agents_built += 1
        return _Agent(h.agent_msgs)

    monkeypatch.setattr("shared.config.get_settings", lambda: h.settings)
    monkeypatch.setattr(reranker_mod._decider, "decide", fake_decide)
    monkeypatch.setattr(reranker_mod, "create_reranker_agent", fake_agent)
    return h


async def _run(**kw):
    trace: list[dict] = []
    args = dict(query="فسخ العقد للإخلال", rationale=_RATIONALE, raw_markdown=_MD,
                max_keep=7, round_trace=trace, planner_brief="موجز المخطط")
    args.update(kw)
    result, usage, log = await run_reranker_for_query(**args)
    return result, usage, log, trace


@pytest.mark.asyncio
async def test_decider_path_builds_from_the_classification(harness):
    result, usage, log, trace = await _run()

    assert harness.agents_built == 0 and harness.agent_msgs == []
    call = harness.decide_calls[0]
    assert call["models"] == [D1, JEV]
    assert call["state"] == dr.build_state("فسخ العقد للإخلال", _RATIONALE, _MD, planner_brief="موجز المخطط")
    assert call["questions"] == dr.build_questions([1, 2, 3, 4], "principle")[0]
    assert call["required"] == ["R1", "M1", "R2", "M2", "R3", "M3", "R4", "M4"]

    keeps = [(d["position"], d["relevance"]) for d in log if d["action"] == "keep"]
    assert keeps == [(1, "high"), (2, "medium"), (3, "medium")]   # 3 = the floor (raw 2.0)
    assert sorted(d["position"] for d in log if d["action"] == "drop") == [4]
    assert [r.relevance for r in result.results] == ["high", "medium", "medium"]
    assert result.dropped_count == 1
    assert result.sufficient is True and result.summary_note == ""

    assert len(usage) == 1
    assert usage[0]["model"] == D1 and usage[0]["agent"] == "reranker"
    assert usage[0]["input_tokens"] == 1234

    t = trace[0]
    assert t["engine"] == "decider" and t["model"] == D1
    assert t["model_served"] == f"{D1}-20261015"
    assert t["fallback_from"] == []
    assert t["scores"] == {1: 3.0, 2: 2.0, 3: 2.0, 4: 0.5}
    assert t["mech"] == {1: 0.9, 2: 0.9, 3: 0.2, 4: 0.2}
    assert t["user_msg"] == call["state"]


@pytest.mark.asyncio
async def test_decider_zero_keeps_no_nudge(harness):
    harness.reply = _reply(answers={f"{q}{p}": {"score": 0.2} if q == "R" else {"noul": 0.1}
                                    for p in range(1, 5) for q in "RM"})
    result, usage, log, trace = await _run()
    assert result.results == [] and result.dropped_count == 4
    assert len(harness.decide_calls) == 1 and harness.agents_built == 0
    assert len(usage) == 1 and len(trace) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("max_keep", [1, 2])
async def test_decider_respects_max_keep(harness, max_keep):
    result, _usage, log, _trace = await _run(max_keep=max_keep)
    assert len(result.results) == max_keep
    assert len([d for d in log if d["action"] == "keep"]) == max_keep   # loop.py's re-cap stays a no-op
    assert result.caps_applied == {"max_keep": max_keep, "truncated_by_cap": 0}


@pytest.mark.asyncio
async def test_jev_after_d1_failure_uses_jev_thresholds(harness):
    # rank: 1 → 1.6 (jev high), 2 → 0.8 (jev medium), 3 → 0.7 (dropped), 4 → 0.5 (raw 0.5)
    harness.reply = _reply(
        model=JEV, fallback_from=[f"{D1}: 500"],
        answers={"R1": {"score": 1.6}, "M1": {"noul": 1.0}, "R2": {"score": 1.6}, "M2": {"noul": 0.5},
                 "R3": {"score": 0.7}, "M3": {"noul": 1.0}, "R4": {"score": 0.5}, "M4": {"noul": 1.0}},
    )
    result, usage, log, trace = await _run()
    keeps = [(d["position"], d["relevance"]) for d in log if d["action"] == "keep"]
    assert keeps == [(1, "high"), (2, "medium")]   # d1's cuts would floor 1 and 2 as medium
    assert usage[0]["model"] == JEV
    assert trace[0]["model"] == JEV and trace[0]["fallback_from"] == [f"{D1}: 500"]
    assert harness.agents_built == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("settings", "kw"),
    [
        (_settings(engine="llm"), {}),
        (_settings(engine="decider"), {"engine": "llm"}),
        (_settings(engine="decider"), {"model_override": "qwen"}),
        (_settings(engine="decider", key=None), {}),
        (_settings(engine="decider", key=""), {}),
        (_settings(engine="decider"), {"rationale": "المبدأ العام في الفسخ"}),   # legacy node
        (_settings(engine="decider"), {"rationale": "[other] المبدأ العام"}),
    ],
    ids=["flag-llm", "engine-arg-llm", "model-override", "no-key", "empty-key",
         "no-channel", "unknown-channel"],
)
async def test_llm_path_without_calling_decide(harness, settings, kw):
    harness.settings = settings
    result, usage, log, trace = await _run(**kw)
    assert harness.decide_calls == []
    assert harness.agents_built == 1 and len(harness.agent_msgs) == 1
    assert "quota" in harness.agent_msgs[0]   # qwen's user message is unchanged (max_keep line)
    assert [d["position"] for d in log if d["action"] == "keep"] == [4]
    assert result.summary_note == "ملاحظة qwen"
    assert trace[0]["engine"] == "llm" and trace[0]["fallback_from"] == []
    assert usage[0]["model"] != D1


@pytest.mark.asyncio
async def test_engine_arg_decider_overrides_the_flag(harness):
    harness.settings = _settings(engine="llm")
    _result, _usage, _log, trace = await _run(engine="decider")
    assert len(harness.decide_calls) == 1 and harness.agents_built == 0
    assert trace[0]["engine"] == "decider"


@pytest.mark.asyncio
async def test_settings_unavailable_takes_llm_path(harness, monkeypatch):
    def boom():
        raise RuntimeError("no env")

    monkeypatch.setattr("shared.config.get_settings", boom)
    await _run()
    assert harness.decide_calls == [] and harness.agents_built == 1


@pytest.mark.asyncio
async def test_decide_none_falls_back_to_qwen(harness):
    harness.reply = None
    result, usage, log, trace = await _run()
    assert len(harness.decide_calls) == 1 and harness.agents_built == 1
    assert [d["position"] for d in log if d["action"] == "keep"] == [4]
    assert len(usage) == 1 and usage[0]["model"] != D1
    assert trace[0]["engine"] == "llm" and trace[0]["fallback_from"] == ["decider: no answer"]


@pytest.mark.asyncio
async def test_decide_raising_falls_back_to_qwen(harness):
    harness.raises = NotImplementedError()
    result, _usage, _log, trace = await _run()
    assert harness.agents_built == 1
    assert len(result.results) == 1
    assert trace[0]["fallback_from"] == ["decider error: NotImplementedError"]


@pytest.mark.asyncio
async def test_unusable_reply_falls_back_but_bills_the_decider(harness):
    harness.reply = _reply(model="other/model", fallback_from=[f"{D1}: timeout"])
    _result, usage, _log, trace = await _run()
    assert harness.agents_built == 1
    assert [u["model"] for u in usage][0] == "other/model"   # billed call kept in the ledger
    assert len(usage) == 2
    assert trace[0]["engine"] == "llm"
    assert trace[0]["fallback_from"] == [f"{D1}: timeout", "other/model: rules failed (ValueError)"]
