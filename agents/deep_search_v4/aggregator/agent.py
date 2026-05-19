"""Pydantic AI agent factories for the aggregator.

Two factory functions:
- `create_aggregator_agent(prompt_key, model_name)` — single-shot synthesizer.
- `create_dcr_agents(model_name)` — tuple of (draft, critique, rewrite) agents
  used by prompt_3 (Draft-Critique-Rewrite chain).

Both resolve their model via the ``aggregator`` tier slot
(:func:`agents.utils.agent_models.get_agent_model`), which yields a provider
FallbackModel. ``model_name`` is a provenance label only — it does NOT select a
model. The runner's validation-based retry (primary → fallback prompt) is a
separate, complementary mechanism.
"""
from __future__ import annotations

import logging

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model

from .models import AggregatorLLMOutput
from .prompts import get_aggregator_prompt

logger = logging.getLogger(__name__)


AGGREGATOR_LIMITS = UsageLimits(
    response_tokens_limit=70_000,
    request_limit=3,
)


def create_aggregator_agent(
    prompt_key: str = "prompt_1",
    model_name: str | None = None,
) -> Agent[None, AggregatorLLMOutput]:
    """Single-shot aggregator agent.

    Args:
        prompt_key: Variant key into AGGREGATOR_PROMPTS ("prompt_1", "prompt_2",
            "prompt_4", or the three DCR stage keys when called by the runner).
        model_name: Provenance label only — does NOT select a model. The model
            is always the ``aggregator`` tier FallbackModel.

    Returns:
        Agent configured with the selected prompt and AggregatorLLMOutput type.
    """
    system_prompt = get_aggregator_prompt(prompt_key)
    model = get_agent_model("aggregator")

    agent: Agent[None, AggregatorLLMOutput] = Agent(
        model,
        name="aggregator",
        output_type=AggregatorLLMOutput,
        instructions=system_prompt,
        retries=2,
    )

    @agent.output_validator
    async def _validate_summary_length(
        ctx,  # RunContext[None]
        value: AggregatorLLMOutput,
    ) -> AggregatorLLMOutput:
        if len(value.chat_summary) > 500:
            value.chat_summary = value.chat_summary[:500].rstrip()
        if len(value.key_findings) > 5:
            value.key_findings = value.key_findings[:5]
        return value

    return agent


# -- Draft-Critique-Rewrite chain (prompt_3 only) -----------------------------


class _CritiqueOutput(AggregatorLLMOutput):
    """Reuses AggregatorLLMOutput for schema — critique stage fills gaps/confidence
    with its assessment and leaves synthesis_md empty. Simpler than introducing
    a third model; the runner handles the stage-specific semantics."""


def create_dcr_agents(
    model_name: str | None = None,
) -> tuple[
    Agent[None, AggregatorLLMOutput],
    Agent[None, AggregatorLLMOutput],
    Agent[None, AggregatorLLMOutput],
]:
    """Three agents for the Draft → Critique → Rewrite chain.

    All three use the ``aggregator`` tier model. ``model_name`` is a provenance
    label only — it does NOT select a model. If any stage fails, the runner
    falls back whole-chain to single-shot — stages are not individually retried.
    """
    def _attach_summary_validator(
        a: Agent[None, AggregatorLLMOutput],
    ) -> Agent[None, AggregatorLLMOutput]:
        @a.output_validator
        async def _validate_summary_length(
            ctx,  # RunContext[None]
            value: AggregatorLLMOutput,
        ) -> AggregatorLLMOutput:
            if len(value.chat_summary) > 500:
                value.chat_summary = value.chat_summary[:500].rstrip()
            if len(value.key_findings) > 5:
                value.key_findings = value.key_findings[:5]
            return value
        return a

    draft = _attach_summary_validator(Agent(
        get_agent_model("aggregator"),
        name="aggregator_draft",
        output_type=AggregatorLLMOutput,
        instructions=get_aggregator_prompt("prompt_3_draft"),
        retries=1,
    ))
    critique = _attach_summary_validator(Agent(
        get_agent_model("aggregator"),
        name="aggregator_critique",
        output_type=AggregatorLLMOutput,
        instructions=get_aggregator_prompt("prompt_3_critique"),
        retries=1,
    ))
    rewrite = _attach_summary_validator(Agent(
        get_agent_model("aggregator"),
        name="aggregator_rewrite",
        output_type=AggregatorLLMOutput,
        instructions=get_aggregator_prompt("prompt_3_rewrite"),
        retries=1,
    ))
    return draft, critique, rewrite
