"""ServiceReranker agent for the compliance_search loop.

Receives all service results as a flat markdown list and classifies
each as keep/drop. No unfold action — services are flat records.
Single-round classification (no multi-round loop like reg_search reranker).

Architecture:
- create_reranker_agent(): factory, returns Agent[None, ServiceRerankerOutput]
- Model: compliance_search_reranker (qwen3.5-flash) — fast, cheap, Arabic classification
- No tools, no deps type parameter
- UsageLimits: response_tokens_limit=70_000, request_limit=3
"""
from __future__ import annotations

import logging

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model

from .models import ServiceRerankerOutput
from .prompts import RERANKER_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

RERANKER_LIMITS = UsageLimits(
    response_tokens_limit=70_000,
    request_limit=3,
)


def create_reranker_agent(
    model_override: str | None = None,
) -> Agent[None, ServiceRerankerOutput]:
    """Create ServiceReranker agent — structured output, no tools.

    ``model_override`` is a tier override token (``qwen``/``deepseek``/
    ``alibaba``/``openrouter``) applied to the slot's policy; tier stays fixed.

    Returns:
        Configured Agent with ServiceRerankerOutput output type.
    """
    model = get_agent_model("compliance_search_reranker", model_override)
    return Agent(
        model,
        name="compliance_search_reranker",
        output_type=ServiceRerankerOutput,
        instructions=RERANKER_SYSTEM_PROMPT,
        retries=2,
    )
