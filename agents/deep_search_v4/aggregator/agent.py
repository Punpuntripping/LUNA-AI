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

from pydantic_ai import Agent, TextOutput
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model
from agents.utils.structured_output import make_json_salvager

from .models import AggregatorLLMOutput
from .prompts import get_aggregator_prompt

logger = logging.getLogger(__name__)

# flash-max occasionally finalises as text (``<thinking>…</thinking>{json}``)
# instead of calling the output tool. This salvages the JSON instead of forcing
# a retry that re-sends the whole (large) synthesis prompt. See
# agents/utils/structured_output.py.
_AGG_RETRY_MSG = (
    "Return the output as a single valid JSON object conforming to the schema "
    "(synthesis_md, used_refs, gaps, confidence) only — with no text or "
    "<thinking> tag outside the JSON. The `synthesis_md` value must be in Arabic."
)


def _aggregator_text_output() -> TextOutput:
    """``TextOutput`` salvage member for the aggregator's ``output_type`` union."""
    return TextOutput(make_json_salvager(AggregatorLLMOutput, retry_msg=_AGG_RETRY_MSG))


AGGREGATOR_LIMITS = UsageLimits(
    # 100k = far above any plausible aggregator output. Synthesis at
    # detail_level=high is genuinely unpredictable and benefits from uncapped
    # reasoning; observed output max ~5500 tokens with ~3500 reasoning. The
    # explicit ceiling is documentation more than constraint — the provider's
    # own `max_tokens` for qwen3.6-plus (~16-32k) bounds us first.
    # (`response_tokens_limit` was the deprecated alias — switched to the
    # current `output_tokens_limit` name.)
    output_tokens_limit=100_000,
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
        output_type=[AggregatorLLMOutput, _aggregator_text_output()],
        instructions=system_prompt,
        retries=2,
    )

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
    draft = Agent(
        get_agent_model("aggregator"),
        name="aggregator_draft",
        output_type=[AggregatorLLMOutput, _aggregator_text_output()],
        instructions=get_aggregator_prompt("prompt_3_draft"),
        retries=1,
    )
    critique = Agent(
        get_agent_model("aggregator"),
        name="aggregator_critique",
        output_type=[AggregatorLLMOutput, _aggregator_text_output()],
        instructions=get_aggregator_prompt("prompt_3_critique"),
        retries=1,
    )
    rewrite = Agent(
        get_agent_model("aggregator"),
        name="aggregator_rewrite",
        output_type=[AggregatorLLMOutput, _aggregator_text_output()],
        instructions=get_aggregator_prompt("prompt_3_rewrite"),
        retries=1,
    )
    return draft, critique, rewrite
