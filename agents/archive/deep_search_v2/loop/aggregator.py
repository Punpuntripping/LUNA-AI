"""Aggregator agent for the inner search loop.

Creates a lightweight pydantic_ai Agent with structured output (AggregatorOutput).
No tools -- purely result evaluation and synthesis.
"""
from __future__ import annotations

import logging

from pydantic_ai import Agent
from pydantic_ai.models.openrouter import OpenRouterModelSettings
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model

from ..models import AggregatorOutput
from ..prompts import AGGREGATOR_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

AGGREGATOR_LIMITS = UsageLimits(
    response_tokens_limit=56_000,
    request_limit=3,
)


def create_aggregator_agent() -> Agent[None, AggregatorOutput]:
    """Create Aggregator agent -- structured output, no tools.

    Returns:
        Configured Agent with AggregatorOutput output type.
    """
    return Agent(
        get_agent_model("deep_search_v2_aggregator"),
        output_type=AggregatorOutput,
        instructions=AGGREGATOR_SYSTEM_PROMPT,
        model_settings=OpenRouterModelSettings(
            openrouter_reasoning={"effort": "high", "exclude": False},
            max_tokens=56_000,
        ),
        retries=2,
    )
