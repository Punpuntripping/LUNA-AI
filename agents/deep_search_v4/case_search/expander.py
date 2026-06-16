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

from agents.utils.agent_models import get_agent_model, AGENT_MODELS, resolve_chain
from agents.model_registry import MODEL_REGISTRY

from .prompts import (
    DEFAULT_EXPANDER_PROMPT,
    get_expander_prompt,
    is_sectioned_prompt,
)
from .models import ExpanderOutput, ExpanderOutputV2

logger = logging.getLogger(__name__)

EXPANDER_LIMITS = UsageLimits(
    # 15k absorbs uncapped thinking + sectioned query expansion output.
    # The old 2000 cap crashed every case_led run we observed (2519, 3302 tokens
    # generated against the cap — UsageLimitExceeded with degraded retrieval).
    # (`response_tokens_limit` was the deprecated alias — switched.)
    output_tokens_limit=15_000,
    request_limit=3,
)


def get_expander_model_id() -> str:
    """Return the model ID of the expander's primary (chain head) model."""
    key = resolve_chain(AGENT_MODELS["case_search_expander"])[0]
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
    model_override: str | None = None,
) -> Agent[None, Any]:
    """Create QueryExpander agent with the selected prompt variant.

    The returned Agent's `output_type` is resolved from `prompt_key`:
    - Legacy prompts → ExpanderOutput
    - Sectioned prompts (prompt_3+) → ExpanderOutputV2

    Reasoning effort is fixed at medium by the ``case_search_expander`` slot
    policy (``_FLASH_MEDIUM``), baked per provider/family by the tier system
    (``agent_models._reasoning_settings``). No agent-level reasoning settings
    are attached here.

    Args:
        prompt_key: Key into EXPANDER_PROMPTS dict (e.g. "prompt_2", "prompt_3").
        model_override: Tier override token (``qwen``/``deepseek``/``alibaba``/
            ``openrouter``) applied to the slot's policy; tier stays fixed.

    Returns:
        Configured Agent with the appropriate output model.
    """
    system_prompt = get_expander_prompt(prompt_key)
    output_type = resolve_output_type(prompt_key)

    model = get_agent_model("case_search_expander", model_override)

    return Agent(
        model,
        name="case_search_expander",
        output_type=output_type,
        instructions=system_prompt,
        retries=2,
    )
