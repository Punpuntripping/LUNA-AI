"""Pydantic AI factory for the sector_picker agent.

A thin classification agent — no tools, no DB access, no retrieval. Reads the
user query (+ optional planner_brief + context_blocks) via the system prompt
and emits a :class:`SectorPickerOutput`. Resolves its model through the tier
system: ``sector_picker`` slot, tier_2 with ``deepseek`` as the primary
family (cheap, fast, reasoning-capable).
"""
from __future__ import annotations

import logging

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import ModelPolicy, get_agent_model

from .deps import SectorPickerDeps
from .models import SectorPickerOutput
from .prompts import SECTOR_PICKER_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


SECTOR_PICKER_LIMITS = UsageLimits(
    # Reasoning tokens count against output on DashScope / OpenRouter — keep a
    # generous ceiling so a thinking-on call cannot crash the run. Observed
    # output excluding reasoning is tiny (<200 tokens — the schema is two
    # fields). 20k is "thinking can never crash us" headroom.
    output_tokens_limit=20_000,
    # No tools, one normal request + validation retries.
    request_limit=4,
)


def create_sector_picker(
    model_override: ModelPolicy | str | None = None,
) -> Agent[SectorPickerDeps, SectorPickerOutput]:
    """Build the sector_picker agent.

    ``model_override`` is an optional tier override token / :class:`ModelPolicy`
    for the ``sector_picker`` slot (tier stays fixed at tier_2).
    """
    model = get_agent_model("sector_picker", model_override)

    agent: Agent[SectorPickerDeps, SectorPickerOutput] = Agent(
        model,
        name="sector_picker",
        deps_type=SectorPickerDeps,
        output_type=SectorPickerOutput,
        instructions=SECTOR_PICKER_SYSTEM_PROMPT,
        retries=2,
        # All-or-nothing schema validation (any invalid sector name fails the
        # whole output). Give the model 2 retries to produce a fully-valid
        # list; if it still cannot, the runner degrades the call to None
        # (run unfiltered — the safe fallback).
        output_retries=2,
    )

    return agent


__all__ = ["SECTOR_PICKER_LIMITS", "create_sector_picker"]
