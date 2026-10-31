"""The transition sentence on the chat message, on both responder paths.

The responder reads excerpts of the synthesis and nothing makes it repeat a
sentence, so code appends one fixed line whenever a ``<transitions>`` block fed
the synthesis. It belongs to the chat message alone: the workspace item is not
touched. Sync on purpose: CI has no ``pytest-asyncio``.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.deep_search_v4.aggregator.models import AggregatorOutput
from agents.deep_search_v4.planner import runner as planner_runner
from agents.deep_search_v4.planner.deps import PlannerDeps
from agents.deep_search_v4.planner.models import PlannerDecision, PlannerResponse
from agents.deep_search_v4.planner.prompts import build_responder_instructions
from agents.orchestrator import _bubble_tokens
from agents.responder import DeepSearchResponse
from agents.responder.prompts import render_deep_search_package
from shared.library.supersession import RESPONDER_VERSION_RULE

_LINE = "هذا التحليل وفق نظام السجل البيئي النافذ منذ 2031/03/01م، لا وفق نظام السجل البيئي (القديم)."
_SUMMARY = "ملخص البحث."
_SYNTHESIS = "## الخلاصة\nنص التحليل."
_QUESTION = "هل صدر الحكم قبل نفاذ النظام؟"


class _Responder:
    def __init__(self, fail: bool) -> None:
        self._fail = fail

    async def run(self, *_a, **_kw) -> SimpleNamespace:
        if self._fail:
            raise RuntimeError("responder down")
        return SimpleNamespace(output=PlannerResponse(chat_summary_md=_SUMMARY))


@pytest.fixture(params=["planner_responder", "unified"])
def path(request, monkeypatch) -> SimpleNamespace:
    """Installs one responder path; ``path.fail`` makes that responder fail."""
    state = SimpleNamespace(name=request.param, fail=False)
    unified = request.param == "unified"
    monkeypatch.setattr("agents.responder.wiring.responder_unified", lambda _family: unified)
    monkeypatch.setattr(
        planner_runner, "create_planner_responder",
        lambda **_kw: _Responder(state.fail),
    )

    async def _unified_responder(*_a, **_kw):
        if state.fail:
            return None
        out = SimpleNamespace(follow_up_question=_QUESTION, question_basis="أساس")
        return PlannerResponse(chat_summary_md=_SUMMARY), out

    monkeypatch.setattr(planner_runner, "_run_unified_responder", _unified_responder)
    return state


def _turn(run_retrieval) -> planner_runner.PlannerTurnResult:
    deps = PlannerDeps(supabase=MagicMock(), embedding_fn=AsyncMock(return_value=[0.0]))
    decision = PlannerDecision(mode="reg_compliance_led", support=False, rationale="اختبار")
    return asyncio.run(
        planner_runner.handle_planner_turn(
            "سؤال", deps, decision=decision, run_retrieval=run_retrieval,
        )
    )


def _retrieval(transition_line: str = "", synthesis: str = _SYNTHESIS):
    async def _run(_query, _config, _deps) -> AggregatorOutput:
        return AggregatorOutput(
            synthesis_md=synthesis, confidence="high", transition_line=transition_line,
        )

    return _run


def test_line_is_the_last_paragraph_of_the_chat_message(path):
    turn = _turn(_retrieval(_LINE))

    assert turn.response.chat_summary_md == f"{_SUMMARY}\n\n{_LINE}"
    assert turn.degraded is False


def test_no_block_leaves_the_message_byte_identical(path):
    turn = _turn(_retrieval())

    assert turn.response.chat_summary_md == _SUMMARY


def test_the_workspace_item_body_is_untouched(path):
    turn = _turn(_retrieval(_LINE))

    assert turn.agg_output.synthesis_md == _SYNTHESIS
    assert _LINE not in turn.agg_output.synthesis_md


def test_a_failed_responder_on_a_real_synthesis_still_says_it(path):
    path.fail = True

    turn = _turn(_retrieval(_LINE))

    assert turn.degraded is True
    assert turn.response.chat_summary_md == f"{_SYNTHESIS}\n\n{_LINE}"


def test_a_failed_responder_with_no_block_is_the_artifact_head_alone(path):
    path.fail = True

    turn = _turn(_retrieval())

    assert turn.response.chat_summary_md == _SYNTHESIS


def test_a_failed_retrieval_has_no_line(path):
    async def _raises(_query, _config, _deps):
        raise RuntimeError("retrieval down")

    turn = _turn(_raises)

    assert turn.degraded is True
    assert turn.agg_output is None
    assert _LINE not in turn.response.chat_summary_md
    assert turn.response.build_artifact is False


def _bubble(turn: planner_runner.PlannerTurnResult) -> str:
    """What the user reads: the delivery the orchestrator runs on either path."""
    return "".join(
        _bubble_tokens(
            SimpleNamespace(
                chat_summary=turn.response.chat_summary_md,
                key_findings=[],
                follow_up_question=turn.follow_up_question,
            )
        )
    )


def test_a_follow_up_question_stays_the_last_paragraph(monkeypatch):
    monkeypatch.setattr("agents.responder.wiring.responder_unified", lambda _family: True)

    async def _unified_responder(*_a, **_kw):
        out = SimpleNamespace(follow_up_question=_QUESTION, question_basis="أساس")
        return PlannerResponse(chat_summary_md=_SUMMARY), out

    monkeypatch.setattr(planner_runner, "_run_unified_responder", _unified_responder)

    bubble = _bubble(_turn(_retrieval(_LINE)))

    assert bubble.index(_LINE) < bubble.index(_QUESTION)
    assert bubble.endswith(_QUESTION)


@pytest.fixture
def real_unified_responder(monkeypatch) -> SimpleNamespace:
    """Everything in ``agents/responder`` runs for real; only the LLM call is stubbed."""
    seen = SimpleNamespace(message=None, calls=0)

    async def _llm_call(_agent, message, **_kw) -> SimpleNamespace:
        seen.message = message
        seen.calls += 1
        return SimpleNamespace(output=DeepSearchResponse(chat_summary_md=_SUMMARY))

    monkeypatch.setattr("agents.responder.wiring.responder_unified", lambda _family: True)
    monkeypatch.setattr("agents.responder.runner.create_responder", lambda *_a: object())
    monkeypatch.setattr("agents.responder.runner.run_tracked", _llm_call)
    return seen


def test_the_real_unified_responder_gets_the_line(real_unified_responder):
    turn = _turn(_retrieval(_LINE))

    assert real_unified_responder.calls == 1
    assert turn.degraded is False
    expected = f"{_SUMMARY}\n\n{_LINE}"
    assert turn.response.chat_summary_md == expected
    assert _bubble(turn) == expected


def test_the_real_unified_responder_prompt_gains_only_the_version_rule(real_unified_responder):
    """Code appends the line afterwards, so the model never sees the line itself."""
    _turn(_retrieval(_LINE))
    with_line = real_unified_responder.message

    _turn(_retrieval())
    plain = real_unified_responder.message

    block = f"### Which version each rule belongs to\n{RESPONDER_VERSION_RULE}\n\n"
    assert block in with_line
    assert with_line.replace(block, "") == plain
    assert _LINE not in with_line


def test_the_real_unified_responder_with_no_block_is_byte_identical(real_unified_responder):
    assert _bubble(_turn(_retrieval())) == _SUMMARY


# -- The version rule, on both responder paths ----------------------------------


def _deps(agg_output: AggregatorOutput) -> PlannerDeps:
    deps = PlannerDeps(supabase=MagicMock(), embedding_fn=AsyncMock(return_value=[0.0]))
    deps._agg_output = agg_output
    deps._decision = PlannerDecision(mode="reg_compliance_led", support=False, rationale="اختبار")
    return deps


def _unified_artifact(monkeypatch, agg_output: AggregatorOutput):
    captured = {}

    async def _run_responder(inp, **_kw):
        captured["artifact"] = inp.artifact

    monkeypatch.setattr("agents.responder.run_responder", _run_responder)
    monkeypatch.setattr("agents.responder.wiring.load_profession_group", lambda *_a: None)
    monkeypatch.setattr("agents.responder.wiring.load_style_profile", lambda *_a: None)
    deps = _deps(agg_output)
    asyncio.run(planner_runner._run_unified_responder("سؤال", deps, agg_output, deps._decision))
    return captured["artifact"]


def test_the_unified_responder_gets_the_rule_during_a_transition(monkeypatch):
    output = AggregatorOutput(synthesis_md=_SYNTHESIS, confidence="high", transition_line=_LINE)

    artifact = _unified_artifact(monkeypatch, output)

    assert artifact.version_rule == RESPONDER_VERSION_RULE
    package = render_deep_search_package(artifact)
    assert package.index(RESPONDER_VERSION_RULE) < package.index("### The synthesis")


def test_the_unified_responder_package_is_unchanged_with_no_transition(monkeypatch):
    output = AggregatorOutput(synthesis_md=_SYNTHESIS, confidence="high")

    artifact = _unified_artifact(monkeypatch, output)

    assert artifact.version_rule == ""
    package = render_deep_search_package(artifact)
    assert "Which version" not in package
    assert package == render_deep_search_package(replace(artifact, version_rule=""))


def test_the_planner_responder_gets_the_rule_during_a_transition():
    output = AggregatorOutput(synthesis_md=_SYNTHESIS, confidence="high", transition_line=_LINE)

    text = build_responder_instructions(_deps(output))

    assert text.index(RESPONDER_VERSION_RULE) < text.index("### An excerpt from the detailed synthesis")


def test_the_planner_responder_is_unchanged_with_no_transition():
    plain = build_responder_instructions(
        _deps(AggregatorOutput(synthesis_md=_SYNTHESIS, confidence="high"))
    )

    assert RESPONDER_VERSION_RULE not in plain
    assert "### Reported gaps\n- No gaps reported.\n\n### An excerpt from the detailed synthesis\n" in plain
