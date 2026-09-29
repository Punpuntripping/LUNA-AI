"""Tests for the QueryExpander agent and prompt utilities."""
from __future__ import annotations

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agents.deep_search_v4.case_search.prompts import (
    build_expander_user_message,
    get_expander_prompt,
)
from agents.deep_search_v4.case_search.models import ExpanderOutput


def _make_expander_fn(queries: list[str], rationales: list[str] | None = None):
    """Build a FunctionModel function returning a deterministic ExpanderOutput."""
    output = ExpanderOutput(
        queries=queries,
        rationales=rationales or [f"rationale for query {i+1}" for i in range(len(queries))],
    )

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        output_tool = info.output_tools[0] if info.output_tools else None
        if output_tool:
            return ModelResponse(
                parts=[ToolCallPart(tool_name=output_tool.name, args=output.model_dump())]
            )
        return ModelResponse(parts=[])

    return fn


@pytest.mark.asyncio
async def test_round_1_produces_queries():
    """FunctionModel returns ExpanderOutput with 3 queries; verify output has 2-4 queries."""
    queries = [
        "منازعة توريد بضاعة والمطالبة بالثمن المتبقي",
        "حكم في فسخ عقد مقاولة لطول مدة إيقاف الأعمال",
        "مبدأ عدم جواز التمسك بشرط الإيقاف لمدة غير معقولة",
    ]
    fn = _make_expander_fn(queries)
    agent: Agent[None, ExpanderOutput] = Agent(FunctionModel(fn), output_type=ExpanderOutput, retries=0)

    result = await agent.run(
        build_expander_user_message(
            focus_instruction="ابحث في السوابق القضائية عن منازعات عقود التوريد",
            user_context="تعاقدت شركتي مع مورد على توريد بضاعة ولم يسدد الثمن",
        )
    )

    output = result.output
    assert isinstance(output, ExpanderOutput)
    assert 2 <= len(output.queries) <= 4
    assert len(output.queries) == 3
    for q in output.queries:
        assert isinstance(q, str)
        assert len(q) > 0


@pytest.mark.asyncio
async def test_empty_focus_instruction():
    """Expander still produces valid output even with empty focus_instruction."""
    queries = ["استعلام بحث عام في الأحكام القضائية السعودية"]
    fn = _make_expander_fn(queries)
    agent: Agent[None, ExpanderOutput] = Agent(FunctionModel(fn), output_type=ExpanderOutput, retries=0)

    user_message = build_expander_user_message(focus_instruction="", user_context="")
    result = await agent.run(user_message)
    output = result.output
    assert isinstance(output, ExpanderOutput)
    assert len(output.queries) >= 1


def test_prompt_lookup():
    """Verify get_expander_prompt('prompt_1') works, unknown key raises KeyError."""
    prompt = get_expander_prompt("prompt_1")
    assert isinstance(prompt, str)
    assert len(prompt) > 100
    # The expander instructions were migrated to English (queries still emit
    # Arabic). Anchor on stable English phrases actually present in prompt_1.
    assert "crafting search queries" in prompt
    assert "court rulings" in prompt

    with pytest.raises(KeyError, match="not found"):
        get_expander_prompt("nonexistent_prompt_key")


def test_user_message_builder():
    """Verify build_expander_user_message produces correct format."""
    focus = "ابحث في السوابق القضائية عن الفصل التعسفي"
    context = "تم فصلي من العمل بدون إنذار مسبق"

    message = build_expander_user_message(focus, context)

    # The builder emits English labels now; the focus/context payloads
    # themselves remain whatever the caller passed (Arabic here).
    assert "Focus instructions:" in message
    assert focus in message
    assert "User context:" in message
    assert context in message


# ===========================================================================
# Editorial query cap — the "at most N" block (expander_query_cap.md §7.5)
# ===========================================================================


def _blocks():
    from agents.deep_search_v4.shared.context import ContextBlock

    return [
        ContextBlock(label="planner_brief", body="موجز المخطط", persistence="turn"),
        ContextBlock(label="case_brief", body="ملخّص القضية", persistence="case"),
    ]


def test_unpinned_user_message_is_byte_identical():
    """D8 — the whole point of the default.

    Every in-app expander call and every editorial job submitted before the
    seeds screen goes through this builder. If the block leaked in
    unconditionally it would change the prompt of every case expander call in
    the product to serve a field they never set.
    """
    for blocks in (None, [], _blocks()):
        assert build_expander_user_message(
            "سؤال", "سياق", context_blocks=blocks, cap=None
        ) == build_expander_user_message("سؤال", "سياق", context_blocks=blocks)


def test_unpinned_message_carries_no_trace_of_the_block():
    msg = build_expander_user_message("سؤال", "سياق", context_blocks=_blocks())
    assert "capped by the editor" not in msg
    assert "Query count for this run" not in msg


# 2 is the wire minimum (D5); 4 and 9 are ordinary values; 50 is D6 — there is
# no upper bound, and a cap above the prompt's own ceiling simply never binds.
@pytest.mark.parametrize("cap", [2, 4, 9, 50])
def test_a_pinned_cap_appends_its_block(cap):
    msg = build_expander_user_message("سؤال", "سياق", cap=cap)
    assert "## Query count for this run — capped by the editor" in msg
    assert f"**at most {cap}**" in msg
    assert f"only the first {cap} are used" in msg
    # ⚠ A ceiling, not a target — nothing in the block asks for a minimum (D3).
    assert "at least" not in msg


def test_the_block_comes_after_the_context_blocks():
    """§7.1 invariant 2 — appended AFTER ``<context_blocks>``, last in the message.

    It overrides the prompt's own "Number of queries" guidance for this run,
    and an instruction that overrides another has to be read after it.
    """
    msg = build_expander_user_message("سؤال", "سياق", context_blocks=_blocks(), cap=4)
    assert msg.index("</context_blocks>") < msg.index("Query count for this run")
    assert msg.rstrip().endswith("only the first 4 are used.")


def test_the_capped_message_is_the_uncapped_one_plus_the_block():
    """Nothing above the block moves — the cap is purely additive."""
    from agents.deep_search_v4.shared.query_cap import render_cap_block

    plain = build_expander_user_message("سؤال", "سياق", context_blocks=_blocks())
    capped = build_expander_user_message(
        "سؤال", "سياق", context_blocks=_blocks(), cap=9
    )
    assert capped == plain + "\n\n" + render_cap_block(9)


def test_the_cap_never_touches_the_system_prompt():
    """⚠ The system prompt is the DashScope prefix-cache key. A per-run block
    there would miss the cache on every expander call in the product."""
    from agents.deep_search_v4.case_search.prompts import EXPANDER_PROMPTS

    for key in EXPANDER_PROMPTS:
        system = get_expander_prompt(key)
        assert "capped by the editor" not in system
        assert "Query count for this run" not in system


@pytest.mark.asyncio
async def test_the_pinned_message_is_what_the_agent_actually_receives():
    """The block has to be in the USER message the model reads, not somewhere
    the builder returns and the node forgets."""
    seen: list[str] = []

    def fn(messages, info):
        for part in messages[-1].parts:
            content = getattr(part, "content", "")
            if isinstance(content, str):
                seen.append(content)
        tool = info.output_tools[0] if info.output_tools else None
        out = ExpanderOutput(queries=["استعلام"], rationales=["سبب"])
        return ModelResponse(parts=[ToolCallPart(tool_name=tool.name, args=out.model_dump())])

    agent: Agent[None, ExpanderOutput] = Agent(
        FunctionModel(fn), output_type=ExpanderOutput, retries=0
    )
    await agent.run(build_expander_user_message("سؤال", "سياق", cap=4))
    assert any("Query count for this run" in c for c in seen)
