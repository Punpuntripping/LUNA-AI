"""Pydantic AI agent factory for agent_writer.

Single-shot: one LLM call, structured WriterLLMOutput.

Instruction stack (concatenated by Pydantic AI in registration order):

  1. Static ``instructions=get_writer_prompt(subtype)`` — the subtype-specific
     drafting rules (role + subtype body + JSON output contract).
  2. ``@agent.instructions package_content_block`` — renders
     ``ctx.deps.package`` (the planner's WriterPackage) as a
     ``<package>...</package>`` system block. Empty in the legacy path.
  3. ``@agent.instructions workspace_envelope_block`` — renders the per-turn
     human frame (describe_query, task_label, revision marker, style prefs).
     In the legacy path (``deps.package is None``) falls back to
     ``format_writer_context`` so ``attached_items`` still get rendered.

See ``.claude/plans/writer_redesign.md`` § Dynamic instructions for the
rationale on splitting into two callables.

The runner handles fallback and persistence.
"""
from __future__ import annotations

import logging

from pydantic_ai import Agent, RunContext
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model

from .context import format_writer_context, format_writer_envelope
from .deps import WriterDeps
from .models import WriterLLMOutput
from .prompts import get_writer_prompt, render_package_for_system_prompt

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
        deps: Optional WriterDeps (workspace context).
        subtype: One of WriterSubtype literals -- selects the static system
            prompt that encodes subtype-specific drafting rules.
        model_name: Provenance label only — does NOT select a model. The model
            is always the ``agent_writer`` tier FallbackModel. Retained so the
            runner can tag the primary vs fallback drafting pass.

    Returns:
        Agent[WriterDeps, WriterLLMOutput] ready to
        ``run(task_statement, deps=writer_deps)``.
        The static ``instructions`` carries the subtype rules. Two
        ``@agent.instructions`` callables append the package-content block
        and the workspace-envelope block from ``deps`` at run time.

    The model is the ``agent_writer`` tier slot — a provider FallbackModel.
    """
    system_prompt = get_writer_prompt(subtype)
    model = get_agent_model("agent_writer")

    agent: Agent[WriterDeps, WriterLLMOutput] = Agent(
        model,
        deps_type=WriterDeps,
        output_type=WriterLLMOutput,
        instructions=system_prompt,
        retries=2,
    )

    # Registration order matters: Pydantic AI's ``@agent.instructions``
    # decorator concatenates dynamic returns AFTER the static ``instructions=``
    # argument, in the order the callables were registered. We register
    # ``package_content_block`` FIRST so the structured package precedes the
    # human envelope frame in the assembled system prompt — the model reads
    # the structured context, then the lighter-weight frame.
    @agent.instructions
    async def package_content_block(ctx: RunContext[WriterDeps]) -> str:
        """Render the planner's WriterPackage as a ``<package>...</package>``
        system block. Returns ``""`` when ``deps.package`` is None (legacy
        path, unit tests with no package)."""
        pkg = ctx.deps.package
        if pkg is None:
            return ""
        return render_package_for_system_prompt(pkg)

    @agent.instructions
    async def workspace_envelope_block(ctx: RunContext[WriterDeps]) -> str:
        """Render the per-turn human frame: describe_query, task_label,
        revision marker, style prefs.

        In the **package path** (``deps.package is not None``): uses
        ``format_writer_envelope`` — the package's analyzed_items already
        carry every attached-item body, so we deliberately skip the
        ``attached_items`` section to avoid double-rendering.

        In the **legacy path** (``deps.package is None``): falls back to
        ``format_writer_context`` so router-supplied ``attached_items``
        keep getting rendered.
        """
        if ctx.deps.package is not None:
            return format_writer_envelope(
                describe_query=ctx.deps.describe_query,
                task_label=ctx.deps.task_label,
                revising_item_id=ctx.deps.revising_item_id,
                detail_level=ctx.deps.detail_level,
                tone=ctx.deps.tone,
            )
        return format_writer_context(
            attached_items=ctx.deps.attached_items,
            describe_query=ctx.deps.describe_query,
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
