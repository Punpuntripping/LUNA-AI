"""QueryExpander agent for the compliance_search loop.

Creates a lightweight pydantic_ai Agent with structured output (ExpanderOutput).
No tools -- purely query expansion based on focus_instruction and context.
On round 2+, weak_axes feedback is injected as dynamic instructions.
"""
from __future__ import annotations

import logging

from pydantic_ai import Agent, RunContext
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model

from .models import ExpanderOutput, WeakAxis
from .prompts import EXPANDER_SYSTEM_PROMPT, build_expander_dynamic_instructions

logger = logging.getLogger(__name__)

EXPANDER_LIMITS = UsageLimits(
    response_tokens_limit=70_000,
    request_limit=3,
)


def create_expander_agent(
    weak_axes: list[WeakAxis] | None = None,
) -> Agent[None, ExpanderOutput]:
    """Create QueryExpander agent with optional weak_axes dynamic instructions.

    Args:
        weak_axes: Weak axes from Aggregator (round 2+ only).
            When provided, dynamic instructions guide re-expansion
            toward the identified gaps.

    Returns:
        Configured Agent with ExpanderOutput output type.
    """
    agent = Agent(
        get_agent_model("compliance_search_expander"),
        name="compliance_search_expander",
        output_type=ExpanderOutput,
        instructions=EXPANDER_SYSTEM_PROMPT,
        retries=2,
    )

    if weak_axes:
        dynamic_text = build_expander_dynamic_instructions(weak_axes)

        @agent.instructions
        def _inject_weak_axes(ctx: RunContext[None]) -> str:
            return dynamic_text

    return agent
