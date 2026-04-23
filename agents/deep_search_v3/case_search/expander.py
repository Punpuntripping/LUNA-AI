"""QueryExpander agent factory for case_search.

Creates a stateless pydantic_ai Agent with structured output.
No tools, no deps -- purely query expansion based on focus_instruction.

Output shape depends on the prompt variant:
- Legacy prompts (prompt_1, prompt_2): flat ExpanderOutput (list[str] queries)
- Sectioned prompts (prompt_3+):       ExpanderOutputV2 (typed queries + sectors)

`create_expander_agent` returns the Agent typed with whichever output model
matches the requested prompt_key, so callers can `run(...)` and receive the
right shape without branching.
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model, AGENT_MODELS
from agents.model_registry import MODEL_REGISTRY

from .prompts import (
    DEFAULT_EXPANDER_PROMPT,
    EXPANDER_PROMPT_THINKING,
    get_expander_prompt,
    is_sectioned_prompt,
)
from .models import ExpanderOutput, ExpanderOutputV2

logger = logging.getLogger(__name__)

EXPANDER_LIMITS = UsageLimits(
    response_tokens_limit=2_000,
    request_limit=3,
)


def get_expander_model_id() -> str:
    """Return the actual model ID string used for the expander."""
    key = AGENT_MODELS.get("case_search_expander", "")
    config = MODEL_REGISTRY.get(key)
    return config.model_id if config else key


def resolve_output_type(prompt_key: str) -> type:
    """Return the output Pydantic model that matches the given prompt key.

    Kept separate so callers (tests, logging, branching graph nodes) can
    reuse the same dispatch logic without reconstructing the Agent.
    """
    return ExpanderOutputV2 if is_sectioned_prompt(prompt_key) else ExpanderOutput


def create_expander_agent(
    prompt_key: str = DEFAULT_EXPANDER_PROMPT,
    thinking_effort: str | None = None,
    model_override: str | None = None,
) -> Agent[None, Any]:
    """Create QueryExpander agent with the selected prompt variant.

    The returned Agent's `output_type` is resolved from `prompt_key`:
    - Legacy prompts → ExpanderOutput
    - Sectioned prompts (prompt_3+) → ExpanderOutputV2

    Args:
        prompt_key: Key into EXPANDER_PROMPTS dict (e.g. "prompt_2", "prompt_3").
        thinking_effort: Reasoning effort level -- "low", "medium", "high", "none",
            or None to use the per-prompt default from EXPANDER_PROMPT_THINKING.
        model_override: Registry key to use instead of the default.

    Returns:
        Configured Agent with the appropriate output model.
    """
    system_prompt = get_expander_prompt(prompt_key)
    output_type = resolve_output_type(prompt_key)

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
    model = create_model(model_override) if model_override else get_agent_model("case_search_expander")

    return Agent(
        model,
        output_type=output_type,
        instructions=system_prompt,
        retries=2,
        model_settings=model_settings if model_settings else None,
    )
