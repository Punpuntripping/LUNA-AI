"""QueryExpander agent for the inner search loop.

Creates a lightweight pydantic_ai Agent with structured output (ExpanderOutput).
No tools -- purely query expansion based on the sub-question and context.
On round 2+, weak_axes feedback is injected as dynamic instructions.
"""
from __future__ import annotations

import logging

from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openrouter import OpenRouterModelSettings
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model

from ..models import ExpanderOutput, WeakAxis
from ..prompts import EXPANDER_SYSTEM_PROMPT, build_expander_dynamic_instructions

logger = logging.getLogger(__name__)

EXPANDER_LIMITS = UsageLimits(
    response_tokens_limit=56_000,
    request_limit=3,
)


def create_expander_agent(
    weak_axes: list[WeakAxis] | None = None,
    strong_results_summary: str | None = None,
) -> Agent[None, ExpanderOutput]:
    """Create QueryExpander agent with optional weak_axes dynamic instructions.

    Args:
        weak_axes: Weak axes from aggregator (round 2+ only).
        strong_results_summary: Summary of strong results to preserve.

    Returns:
        Configured Agent with ExpanderOutput output type.
    """
    agent = Agent(
        get_agent_model("deep_search_v2_expander"),
        output_type=ExpanderOutput,
        instructions=EXPANDER_SYSTEM_PROMPT,
        model_settings=OpenRouterModelSettings(
            openrouter_reasoning={"effort": "medium", "exclude": False},
            max_tokens=56_000,
        ),
        retries=2,
    )

    if weak_axes:
        @agent.instructions
        def _inject_weak_axes(ctx: RunContext[None]) -> str:
            return build_expander_dynamic_instructions(
                weak_axes, strong_results_summary,
            )

    return agent
