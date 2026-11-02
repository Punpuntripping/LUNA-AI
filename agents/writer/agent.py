"""Pydantic AI agent factory for agent_writer.

Single-shot: one LLM call, structured WriterLLMOutput.

Instruction stack (concatenated by Pydantic AI in registration order):

  1. Static ``instructions=get_writer_prompt(subtype)`` — the subtype-specific
     drafting rules (role + subtype body + JSON output contract).
  2. ``@agent.instructions today_block`` — today's date, for deadlines and
     elapsed time. It changes once a day, so it sits right after the static
     prompt.
  3. ``@agent.instructions package_content_block`` — renders
     ``ctx.deps.package`` (the planner's WriterPackage) as a
     ``<package>...</package>`` system block. Empty in the legacy path.
  4. ``@agent.instructions workspace_envelope_block`` — renders the per-turn
     human frame (describe_query, task_label, revision marker, style prefs).
     In the legacy path (``deps.package is None``) falls back to
     ``format_writer_context`` so ``attached_items`` still get rendered.
  5. ``@agent.instructions conversation_memory_block`` — the
     ``<conversation_memory>`` block and how it applies to a draft. After the
     package and the envelope, so the prefix above it is untouched. Empty when
     the conversation has no memory.
  6. ``@agent.instructions welcome_block`` — رسائل الترحيب: the opening line
     for ``chat_summary`` when this turn is the user's first. Empty otherwise.

See ``.claude/plans/writer_redesign.md`` § Dynamic instructions for the
rationale on splitting into two callables.

The runner handles fallback and persistence.
"""
from __future__ import annotations

import logging

from pydantic_ai import Agent, RunContext, TextOutput
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model
from agents.utils.today import today_line
from agents.utils.usage_limits import OUTPUT_TOKENS_CAP
from agents.utils.structured_output import make_json_salvager
from agents.utils.welcome import WELCOME_CHAR_ALLOWANCE

from .context import format_writer_context, format_writer_envelope
from .deps import WriterDeps
from .models import WriterLLMOutput
from .prompts import get_writer_prompt, render_package_for_system_prompt

logger = logging.getLogger(__name__)

# flash-max sometimes finalises as text (``<thinking>…</thinking>{json}``)
# instead of calling the output tool. Salvage the JSON rather than retrying the
# whole (long) drafting prompt. See agents/utils/structured_output.py.
_WRITER_RETRY_MSG = (
    "Return the output as a valid JSON object matching the schema (title_ar, "
    "sections, citations_used, confidence, notes_ar, chat_summary, key_findings) "
    "only — with no text and no <thinking> tag outside the JSON."
)


# Shared runaway cap (agents/utils/usage_limits.py). It counts REASONING tokens
# and is only checked once the whole response has arrived — the old 30k cap
# discarded finished ~40k-token memos (prod 2026-10-10: 6/12 writer runs).
WRITER_LIMITS = UsageLimits(
    output_tokens_limit=OUTPUT_TOKENS_CAP,
    request_limit=2,
)

#: Printed under the <conversation_memory> block, never without it. Each line
#: settles a static rule of this agent that the block alone leaves open:
#:
#: - "fill placeholders from the package": a memory value is not in the package.
#: - "use <parties> verbatim", and the approved <plan>: the planner builds both,
#:   and a value the user corrected while approving the plan is newer than the
#:   entry (plan D7). The planner states such a correction explicitly.
#: - the exhibit `label_ar`: court-facing text the model writes itself, and the
#:   chat guide repeats it.
#: - "cite only what appears in <references> or <sources>": a position the user
#:   insisted on has no source and takes no citation.
#: - the block's "your reply to the user": `chat_summary` is dropped on the
#:   unified responder path and cut at 500 chars on the other, and `notes_ar`
#:   reaches no chat on the other. `key_findings` reaches the bubble on both
#:   (orchestrator ``_bubble_tokens``), and the first item survives the cap of 5.
_MEMORY_DRAFTING_NOTE = """\
How this memory applies to your draft:
- A value stated in it is given, not missing: use it to fill `[…]` placeholders and missing details, exactly as you would a value from the package.
- A fact entry outranks everything in `<package>`, including `<parties>`, `<plan>`, `<sources>`, `<facts>` and `<prior_draft>`, unless `<user_request>` or `<plan>` says the user corrected that value afterwards. State it plainly, without `(n)`.
- The rule against the value a fact entry replaced covers everything you write: the body, each exhibit's `label_ar`, the exhibits list, `chat_summary`, `key_findings` and `notes_ar`.
- A position the user insisted on is stated without `(n)`. The rule to cite only what appears in `<references>` or `<sources>` limits citations; it does not stop you stating it.
- A conflict between your sources and a law entry never goes in the document. Your reply to the user about it is the FIRST item of `key_findings`, which the user always reads in the chat; `chat_summary` and `notes_ar` may not reach them."""


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
        name="writer_executor",
        deps_type=WriterDeps,
        output_type=[
            WriterLLMOutput,
            TextOutput(make_json_salvager(WriterLLMOutput, retry_msg=_WRITER_RETRY_MSG)),
        ],
        instructions=system_prompt,
        retries=2,
    )

    # Registration order matters: Pydantic AI's ``@agent.instructions``
    # decorator concatenates dynamic returns AFTER the static ``instructions=``
    # argument, in the order the callables were registered. ``today_block``
    # goes first, right after the static prompt. ``package_content_block``
    # follows it so the structured package precedes the human envelope frame
    # in the assembled system prompt — the model reads the structured
    # context, then the lighter-weight frame.
    @agent.instructions
    def today_block() -> str:
        """Today's date. See ``agents/utils/today.py``."""
        return today_line()

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

    @agent.instructions
    async def conversation_memory_block(ctx: RunContext[WriterDeps]) -> str:
        """The rendered ``<conversation_memory>`` block, or ``""`` without one.

        Already rendered, encoded and escaped, so it is printed as it is:
        ``render_package_for_system_prompt`` is the writer's encode point and
        this block does not pass through it.
        """
        memory = ctx.deps.conversation_memory_md
        if not memory:
            return ""
        return f"{memory}\n\n{_MEMORY_DRAFTING_NOTE}"

    @agent.instructions
    async def welcome_block(ctx: RunContext[WriterDeps]) -> str:
        """Open ``chat_summary`` with the welcome line, when the turn earns one.

        A drafting request («اكتب لي عقد عمل») is a perfectly ordinary first
        message, and on that turn nothing the router said reaches the user —
        this executor's ``chat_summary`` is the whole reply.
        """
        return ctx.deps.welcome_instruction or ""

    @agent.output_validator
    async def _validate_summary_length(
        ctx: RunContext[WriterDeps],
        value: WriterLLMOutput,
    ) -> WriterLLMOutput:
        # A welcomed turn gets the greeting's characters ON TOP of the summary
        # budget. Truncating at a flat 500 would keep the greeting and chop the
        # end of the summary it was prepended to.
        limit = 500 + (WELCOME_CHAR_ALLOWANCE if ctx.deps.welcome_instruction else 0)
        if len(value.chat_summary) > limit:
            value.chat_summary = value.chat_summary[:limit].rstrip()
        if len(value.key_findings) > 5:
            value.key_findings = value.key_findings[:5]
        return value

    return agent


__all__ = ["create_writer_agent", "WRITER_LIMITS"]
