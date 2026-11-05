"""The planner never rewords the user's message (owner decision 2026-11-05).

The search runs on the message as written; after an ``ask_user`` pause it runs
on the original message + the planner's question + the user's reply, verbatim.
"""
from __future__ import annotations

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from agents.deep_search_v4.planner.models import PlannerDecision
from agents.deep_search_v4.planner.prompts import (
    PLANNER_DECIDER_SYSTEM_PROMPT,
    build_decider_instructions,
    build_decider_user_message,
    compose_resumed_query,
)

ORIGINAL = "اشتريت سيارة بمبلغ 22000 <وبعد> اربعة اشهر & حكم برد الدعوى فما الحل ؟"


def _ask(call_id: str, question: str) -> ModelResponse:
    return ModelResponse(parts=[
        ToolCallPart(tool_name="ask_user", args={"question": question}, tool_call_id=call_id),
    ])


def _reply(call_id: str, reply: str) -> ModelRequest:
    return ModelRequest(parts=[
        ToolReturnPart(tool_name="ask_user", content=reply, tool_call_id=call_id),
    ])


def test_single_exchange_is_verbatim() -> None:
    history = [
        ModelRequest(parts=[UserPromptPart(content=build_decider_user_message(ORIGINAL))]),
        _ask("c1", "هل صدر الحكم ابتدائياً أم من الاستئناف؟"),
        _reply("c1", "ابتدائي"),
    ]
    assert compose_resumed_query(history) == (
        f"{ORIGINAL}\n\n"
        "سؤال التوضيح: هل صدر الحكم ابتدائياً أم من الاستئناف؟\n"
        "ردّ المستخدم: ابتدائي"
    )


def test_chained_pauses_keep_every_exchange_in_order() -> None:
    history = [
        ModelRequest(parts=[UserPromptPart(content=build_decider_user_message(ORIGINAL))]),
        _ask("c1", "س١"),
        _reply("c1", "ج١"),
        # the resume run's own empty user prompt must not replace the original
        ModelRequest(parts=[UserPromptPart(content="")]),
        _ask("c2", "س٢"),
        _reply("c2", "ج٢"),
        ModelResponse(parts=[TextPart(content="{}")]),
    ]
    out = compose_resumed_query(history)
    assert out.startswith(ORIGINAL + "\n\n")
    assert out.index("س١") < out.index("ج١") < out.index("س٢") < out.index("ج٢")


def test_unanswered_question_and_other_tools_are_ignored() -> None:
    history = [
        ModelRequest(parts=[UserPromptPart(content=build_decider_user_message(ORIGINAL))]),
        ModelResponse(parts=[
            ToolCallPart(tool_name="fetch_article", args={"regulation_title": "x"}, tool_call_id="f1"),
        ]),
        ModelRequest(parts=[ToolReturnPart(tool_name="fetch_article", content="نص", tool_call_id="f1")]),
        _ask("c9", "لم يُجب"),
    ]
    assert compose_resumed_query(history) == ORIGINAL


def test_no_original_message_returns_empty() -> None:
    assert compose_resumed_query([]) == ""
    assert compose_resumed_query([_ask("c1", "س"), _reply("c1", "ج")]) == ""


def test_decision_and_prompts_carry_no_restatement() -> None:
    assert "query_restatement" not in PlannerDecision.model_fields
    assert "query_restatement" not in PLANNER_DECIDER_SYSTEM_PROMPT

    class _Deps:
        conversation_memory_md = "<conversation_memory>x</conversation_memory>"
        case_brief = None
        compaction_summary_md = None
        recent_messages = None
        prior_searches = None
        attached_items = None
        analyzer_handoff = None

    assert "query_restatement" not in build_decider_instructions(_Deps())
