"""QueryExpander agent factory for reg_search.

Creates a stateless pydantic_ai Agent with structured output (ExpanderOutput).
No tools, no deps -- purely query expansion based on focus_instruction.
Prompt variant selected via prompt_key parameter.
"""
from __future__ import annotations

import logging

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model, AGENT_MODELS
from agents.model_registry import MODEL_REGISTRY

from .prompts import get_expander_prompt, EXPANDER_PROMPT_THINKING
from .models import ExpanderOutput

logger = logging.getLogger(__name__)

EXPANDER_LIMITS = UsageLimits(
    response_tokens_limit=70_000,
    request_limit=3,
)


def get_expander_model_id() -> str:
    """Return the actual model ID string used for the expander (e.g. 'google/gemma-4-31b-it')."""
    key = AGENT_MODELS.get("reg_search_expander", "")
    config = MODEL_REGISTRY.get(key)
    return config.model_id if config else key


def create_expander_agent(
    prompt_key: str = "prompt_1",
    thinking_effort: str | None = None,
    model_override: str | None = None,
) -> Agent[None, ExpanderOutput]:
    """Create QueryExpander agent with the selected prompt variant.

    Args:
        prompt_key: Key into EXPANDER_PROMPTS dict (e.g. "prompt_1").
        thinking_effort: Reasoning effort level — "low", "medium", "high", "none",
            or None to use the per-prompt default from EXPANDER_PROMPT_THINKING.
            Pass "none" to disable reasoning entirely.
        model_override: Registry key to use instead of the default (e.g. "or-qwen3.5-397b").

    Returns:
        Configured Agent with ExpanderOutput result type.
    """
    system_prompt = get_expander_prompt(prompt_key)

    # Resolve thinking effort: explicit override → per-prompt default
    if thinking_effort is None:
        thinking_effort = EXPANDER_PROMPT_THINKING.get(prompt_key)

    model_settings: dict = {}
    if thinking_effort and thinking_effort != "none":
        model_settings = {
            "extra_body": {
                "enable_thinking": True,
            },
        }

    from agents.model_registry import create_model
    model = create_model(model_override) if model_override else get_agent_model("reg_search_expander")

    return Agent(
        model,
        output_type=ExpanderOutput,
        instructions=system_prompt,
        retries=2,
        model_settings=model_settings if model_settings else None,
    )
