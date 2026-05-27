"""Pydantic AI agent factories for the item_analyzer (Layer-4 Memory).

Two factories, one per output family:

- ``create_refs_analyzer(caller_id)``  → refs-family agent
  (output: ``RefsAnalyzeOutput``)
- ``create_meta_analyzer(caller_id)``  → meta-family agent
  (output: ``MetaAnalyzeOutput``)

Both funnel through ``_build_analyzer`` so the only thing a flow varies is
the system prompt + the output type. Mirrors the house pattern from
``agents/memory/artifact_summarizer/agent.py`` (private ``_build_*`` builder
+ two public factories that pin different prompts/output types).

Reasoning (``extra_body.enable_thinking``) is intentionally **OFF** here:
the analyzer's outputs are short discriminated-union verdicts, and reasoning
mode burns tokens with no measurable quality lift for this slot. See
``.claude/plans/item_analyzer_v2.md`` §6 for the rationale.
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model

from .deps import CallerId
from .models import _MetaAnalyzeOutputLLM, _RefsAnalyzeOutputLLM
from .prompt_registry import meta_prompt_for_caller, refs_prompt_for_caller

logger = logging.getLogger(__name__)


# Generous output cap — ``partial`` verdicts can carry long ``distilled``
# slices when the source WI is a dense legal document. 32k is the tier_2
# ceiling and matches the budget advertised in the writer-planner prompts.
# request_limit=2 covers the single retry permitted by the agent.
ANALYZER_LIMITS = UsageLimits(
    output_tokens_limit=32_000,
    request_limit=2,
)


def _build_analyzer(instructions: str, output_type: Any) -> Agent[None, Any]:
    """Private builder — single source of truth for the analyzer agent config.

    HARD CONSTRAINT: every flow of the item_analyzer must use IDENTICAL
    settings — same model, same ``retries``, no ``model_settings`` (reasoning
    OFF). The ONLY things a flow varies are the ``instructions`` (system
    prompt) and the ``output_type``. Both public factories below funnel
    through here so the config can never drift.
    """
    return Agent(
        get_agent_model("item_analyzer"),
        name="item_analyzer",
        output_type=output_type,
        instructions=instructions,
        retries=1,
    )


def create_refs_analyzer(caller_id: CallerId) -> Agent[None, _RefsAnalyzeOutputLLM]:
    """Build the refs-family analyzer agent for ``caller_id``.

    Resolves the system prompt via ``refs_prompt_for_caller`` — an unwired
    caller raises ``NotImplementedError`` at this point (programmer bug).

    The bound ``output_type`` is the **internal** alias-bearing shape
    (``_RefsAnalyzeOutputLLM``): the LLM emits ``wi="WI-{seq}"`` aliases
    instead of raw UUIDs. The runner resolves aliases → UUIDs and produces
    the public ``RefsAnalyzeOutput`` shape before returning to the caller.
    See ``.claude/plans/agent_communication_protocol.md``.
    """
    return _build_analyzer(
        instructions=refs_prompt_for_caller(caller_id),
        output_type=_RefsAnalyzeOutputLLM,
    )


def create_meta_analyzer(caller_id: CallerId) -> Agent[None, _MetaAnalyzeOutputLLM]:
    """Build the meta-family analyzer agent for ``caller_id``.

    Same alias-surface contract as ``create_refs_analyzer`` — the LLM sees
    and emits ``wi="WI-{seq}"`` aliases, the runner converts to UUIDs.
    """
    return _build_analyzer(
        instructions=meta_prompt_for_caller(caller_id),
        output_type=_MetaAnalyzeOutputLLM,
    )
