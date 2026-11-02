"""A long message always gets its understanding reviewed before a search.

The decider left this to its own judgment and paused on none of the long
messages it saw, so the runner flags them by length and the decider holds a
decision until it has called ``ask_user``. These tests pin each branch: the flag,
the send-back, the way out when the model never asks, and the two paths that must
never be forced (a resumed run and a headless run).
"""
from __future__ import annotations

import pytest
from pydantic_ai import DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agents.deep_search_v4.planner.agent import (
    PLANNER_DECIDER_LIMITS,
    _REVIEW_RETRIES,
    create_planner_decider,
)
from agents.deep_search_v4.planner.models import PinnedPlan, PlannerDecision
from agents.deep_search_v4.planner.prompts import (
    LONG_MESSAGE_REVIEW_MIN_CHARS,
    LONG_MESSAGE_REVIEW_RULE,
)
from agents.deep_search_v4.planner.runner import handle_planner_turn

from ._fmodels import ask_user_model, output_model, pause_then_output_model

LONG = "و" * LONG_MESSAGE_REVIEW_MIN_CHARS
JUST_SHORT = "و" * (LONG_MESSAGE_REVIEW_MIN_CHARS - 1)

DECISION = PlannerDecision(mode="reg_compliance_led", support=False, rationale="قرار")


def decide_then_ask_model(decisions: int) -> tuple[FunctionModel, list[str]]:
    """Emit ``decisions`` decisions, then ``ask_user``. Returns the model + a move log."""
    moves: list[str] = []

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(moves) < decisions:
            moves.append("decide")
            tool = info.output_tools[0]
            return ModelResponse(
                parts=[ToolCallPart(tool_name=tool.name, args=DECISION.model_dump())]
            )
        moves.append("ask")
        return ModelResponse(
            parts=[ToolCallPart(tool_name="ask_user", args={"question": "هل فهمت؟"})]
        )

    return FunctionModel(fn), moves


@pytest.mark.asyncio
async def test_long_message_carries_the_rule_and_pauses(patch_planner_model, planner_deps):
    seen: list[str | None] = []

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(info.instructions)
        return ModelResponse(
            parts=[ToolCallPart(tool_name="ask_user", args={"question": "هل فهمت؟"})]
        )

    patch_planner_model(FunctionModel(fn))
    result = await handle_planner_turn(LONG, planner_deps, decision=None)

    assert result.kind == "paused"
    assert isinstance(result.deferred, DeferredToolRequests)
    assert LONG_MESSAGE_REVIEW_RULE in seen[0]


@pytest.mark.asyncio
async def test_a_decision_on_a_long_message_is_sent_back(
    patch_planner_model, planner_deps, retrieval_spy
):
    """The model tries to skip the review → it is told to ask → the turn pauses."""
    model, moves = decide_then_ask_model(decisions=1)
    patch_planner_model(model)

    result = await handle_planner_turn(
        LONG, planner_deps, decision=None, run_retrieval=retrieval_spy
    )

    assert moves == ["decide", "ask"]
    assert result.kind == "paused"
    assert retrieval_spy.call_count == 0


@pytest.mark.asyncio
async def test_length_counts_from_the_threshold(patch_planner_model, planner_deps):
    """One character short of the threshold, the model's decision stands."""
    seen: list[str | None] = []

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(info.instructions)
        tool = info.output_tools[0]
        return ModelResponse(
            parts=[ToolCallPart(tool_name=tool.name, args=DECISION.model_dump())]
        )

    patch_planner_model(FunctionModel(fn))
    decider = create_planner_decider()
    planner_deps.review_required = len(JUST_SHORT) >= LONG_MESSAGE_REVIEW_MIN_CHARS
    result = await decider.run(
        f"<query>{JUST_SHORT}</query>", deps=planner_deps,
        usage_limits=PLANNER_DECIDER_LIMITS,
    )

    assert isinstance(result.output, PlannerDecision)
    assert len(seen) == 1
    assert LONG_MESSAGE_REVIEW_RULE not in seen[0]


@pytest.mark.asyncio
async def test_a_model_that_never_asks_keeps_the_turn(patch_planner_model, planner_deps):
    """The review is worth retries, never the turn: the decision finally stands."""
    model, moves = decide_then_ask_model(decisions=99)
    patch_planner_model(model)
    planner_deps.review_required = True

    result = await create_planner_decider().run(
        "<query>x</query>", deps=planner_deps, usage_limits=PLANNER_DECIDER_LIMITS,
    )

    assert isinstance(result.output, PlannerDecision)
    assert len(moves) == _REVIEW_RETRIES + 1


@pytest.mark.asyncio
async def test_the_reply_is_not_reviewed_in_turn(
    patch_planner_model, planner_deps, make_planner_deps
):
    """Resume rebuilds deps, so the decision after the reply goes straight through."""
    patch_planner_model(pause_then_output_model(DECISION))
    decider = create_planner_decider()
    planner_deps.review_required = True

    paused = await decider.run(
        "<query>x</query>", deps=planner_deps, usage_limits=PLANNER_DECIDER_LIMITS,
    )
    assert isinstance(paused.output, DeferredToolRequests)

    call = paused.output.calls[0]
    final = await decider.run(
        message_history=paused.all_messages(),
        deferred_tool_results=DeferredToolResults(calls={call.tool_call_id: "نعم"}),
        deps=make_planner_deps(),
        usage_limits=PLANNER_DECIDER_LIMITS,
    )

    assert isinstance(final.output, PlannerDecision)
    assert final.usage().requests == 1, "the decision must not be sent back"


@pytest.mark.asyncio
async def test_a_headless_run_is_never_forced_to_pause(patch_planner_model, planner_deps):
    """Nobody can answer a headless run, so the length gate stays off."""
    patch_planner_model(output_model(DECISION))
    await handle_planner_turn(
        LONG, planner_deps, decision=None,
        run_retrieval=_boom, pinned=PinnedPlan(headless=True),
    )

    assert planner_deps.review_required is False


async def _boom(*_args, **_kwargs):
    raise RuntimeError("retrieval is not under test")


@pytest.mark.asyncio
async def test_the_ordinary_ask_still_pauses_a_short_message(patch_planner_model, planner_deps):
    patch_planner_model(ask_user_model())
    result = await handle_planner_turn("ابحث في القضايا", planner_deps, decision=None)

    assert result.kind == "paused"
    assert planner_deps.review_required is False
