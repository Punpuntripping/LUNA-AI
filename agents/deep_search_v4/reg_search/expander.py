"""QueryExpander agent factory for reg_search.

Creates a stateless pydantic_ai Agent with structured output (ExpanderOutput).
No tools, no deps -- purely query expansion based on focus_instruction.
Prompt variant selected via prompt_key parameter.
"""
from __future__ import annotations

import logging

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model, AGENT_MODELS, resolve_chain
from agents.model_registry import MODEL_REGISTRY

from .prompts import get_expander_prompt, EXPANDER_PROMPT_THINKING
from .models import ExpanderOutput

logger = logging.getLogger(__name__)

EXPANDER_LIMITS = UsageLimits(
    # 15k absorbs uncapped thinking + actual query expansion output.
    # Observed max output ~3950, max reasoning ~3340 → typical total ~7k.
    # (`response_tokens_limit` was the deprecated alias — switched to the
    # current `output_tokens_limit` name.)
    output_tokens_limit=15_000,
    request_limit=3,
)


def get_expander_model_id() -> str:
    """Return the model ID of the expander's primary (chain head) model."""
    key = resolve_chain(AGENT_MODELS["reg_search_expander"])[0]
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
        model_override: Tier override token (``qwen``/``deepseek``/``alibaba``/
            ``openrouter``) applied to the slot's policy; tier stays fixed.

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

    model = get_agent_model("reg_search_expander", model_override)

    return Agent(
        model,
        name="reg_search_expander",
        output_type=ExpanderOutput,
        instructions=system_prompt,
        retries=2,
        model_settings=model_settings if model_settings else None,
    )
