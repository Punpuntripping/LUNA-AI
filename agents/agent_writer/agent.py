"""Pydantic AI agent factory for agent_writer.

Single-shot: one LLM call, structured WriterLLMOutput.
The static ``instructions`` carry the subtype-specific drafting rules.
A ``@agent.system_prompt`` callable appends the dynamic workspace context
(briefing, attached items, revision target, style prefs) from ``WriterDeps``
at run time — so the runner never concatenates context into the user message.
The runner handles fallback and persistence.
"""
from __future__ import annotations

import logging

from pydantic_ai import Agent, RunContext
from pydantic_ai.usage import UsageLimits

from agents.model_registry import create_model

from .context import format_writer_context
from .deps import WriterDeps
from .models import WriterLLMOutput
from .prompts import get_writer_prompt

logger = logging.getLogger(__name__)


WRITER_LIMITS = UsageLimits(
    response_tokens_limit=30_000,    # higher than aggregator -- generation
    request_limit=2,
)


def create_writer_agent(
    deps: WriterDeps | None = None,
    *,
    subtype: str = "memo",
    model_name: str | None = None,
) -> Agent[WriterDeps, WriterLLMOutput]:
    """Build a Pydantic AI agent for the requested writing subtype.

    Args:
        deps: Optional WriterDeps; when provided, the model_name defaults to
            ``deps.primary_model`` so the runner's fallback decision (primary
            vs fallback) drives which agent is created.
        subtype: One of WriterSubtype literals -- selects the static system
            prompt that encodes subtype-specific drafting rules.
        model_name: Override the registry key. When ``None`` and ``deps`` is
            provided, falls back to ``deps.primary_model``; otherwise to
            "qwen3.6-plus".

    Returns:
        Agent[WriterDeps, WriterLLMOutput] ready to
        ``run(task_statement, deps=writer_deps)``.
        The static ``instructions`` carries the subtype rules.
        The ``@agent.system_prompt`` decorator appends the workspace context
        block (briefing + attached_items + style prefs) from deps at run time.
        Failures of model lookup raise immediately -- the runner is responsible
        for catching and falling back.
    """
    system_prompt = get_writer_prompt(subtype)
    chosen_model = (
        model_name
        or (deps.primary_model if deps is not None else None)
        or "qwen3.6-plus"
    )
    model = create_model(chosen_model)

    agent: Agent[WriterDeps, WriterLLMOutput] = Agent(
        model,
        deps_type=WriterDeps,
        output_type=WriterLLMOutput,
        instructions=system_prompt,
        retries=2,
    )

    @agent.system_prompt
    async def inject_workspace_context(ctx: RunContext[WriterDeps]) -> str:
        """Append router-selected attached_items + briefing + revision target
        as a second system block. Re-evaluated on every run (including resumes
        via message_history in Wave 10+)."""
        return format_writer_context(
            attached_items=ctx.deps.attached_items,
            briefing=ctx.deps.briefing,
            revising_item_id=ctx.deps.revising_item_id,
            detail_level=ctx.deps.detail_level,
            tone=ctx.deps.tone,
        )

    @agent.output_validator
    async def _validate_summary_length(
        ctx: RunContext[WriterDeps],
        value: WriterLLMOutput,
    ) -> WriterLLMOutput:
        if len(value.chat_summary) > 500:
            value.chat_summary = value.chat_summary[:500].rstrip()
        if len(value.key_findings) > 5:
            value.key_findings = value.key_findings[:5]
        return value

    return agent


__all__ = ["create_writer_agent", "WRITER_LIMITS"]
