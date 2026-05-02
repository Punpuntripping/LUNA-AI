"""Runner -- orchestrates LLM call -> publish for one writer turn.

Pipeline::

    populate_deps_from_input -> build_user_message -> primary LLM (with
    fallback, deps= injected) -> publish_writer_result

The publisher is responsible for assembling content_md, persisting the row,
emitting SSE events, and acquiring/releasing the stopgap lock metadata.
This runner stays thin: map WriterInput → WriterDeps context fields, build
the research-only user message, call LLM (with fallback chain), hand the
structured output to the publisher.

Context injection seam (Task 5b)
---------------------------------
Workspace context (attached_items, briefing, revising_item_id, detail_level,
tone) is now carried in WriterDeps and injected via the agent's
``@agent.system_prompt`` callable rather than being concatenated into the user
message.  The user message contains only the research references so the LLM
can still resolve ``(n)`` citations deterministically.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from .agent import WRITER_LIMITS, create_writer_agent
from .deps import WriterDeps
from .models import WriterInput, WriterLLMOutput, WriterOutput
from .prompts import build_writer_user_message
from .publisher import publish_writer_result

logger = logging.getLogger(__name__)


def _populate_deps_from_input(input: WriterInput, deps: WriterDeps) -> None:
    """Copy workspace context fields from WriterInput onto WriterDeps in-place.

    This is the bridge between the legacy WriterInput contract (used by
    existing callers) and the Task-5b deps-based context-injection pattern.
    When the orchestrator (Task 7) passes a MajorAgentInput directly to the
    runner it will set deps fields itself; this helper keeps backward compat.

    WriterInput.workspace_context carries the old-style WorkspaceContextBlock /
    dict.  Those are kept as-is for the research user message builder.
    The new fields (briefing=user_request, attached_items from WorkspaceItemSnapshot,
    revising_item_id, detail_level, tone) are mapped here so they reach the
    @agent.system_prompt callable without touching the user message string.
    """
    # Briefing: user_request is the canonical task statement.
    if not deps.briefing:
        deps.briefing = input.user_request or ""

    # attached_items: WriterInput does not carry WorkspaceItemSnapshot objects
    # directly (that is Wave 10+ via MajorAgentInput).  Leave deps.attached_items
    # as-is; callers that already set it (e.g. orchestrator) won't be overwritten.

    # Revision target
    if deps.revising_item_id is None and input.revising_item_id:
        deps.revising_item_id = input.revising_item_id

    # Style prefs
    if deps.detail_level == "standard" and input.detail_level:
        deps.detail_level = input.detail_level
    if deps.tone == "neutral" and input.tone:
        deps.tone = input.tone


async def handle_writer_turn(
    input: WriterInput,
    deps: WriterDeps,
) -> WriterOutput:
    """Run one writer turn end-to-end.

    Args:
        input: User request + research context + workspace context.
        deps: Supabase client + model selection + SSE emit hook.
            Workspace context fields (briefing, attached_items, etc.) on deps
            take precedence; any gaps are backfilled from input by
            ``_populate_deps_from_input``.

    Returns:
        WriterOutput populated by the publisher.
    """
    t0 = time.perf_counter()

    # Bridge: ensure deps carries workspace context from input for the dynamic
    # system_prompt callable.  Must run before agent construction.
    _populate_deps_from_input(input, deps)

    # User message now carries only the task statement + research refs.
    # The workspace_context / preferences sections have moved to the system
    # prompt via @agent.system_prompt (inject_workspace_context).
    user_message = build_writer_user_message(input)

    llm_output, model_used = await _run_writer(input.subtype, user_message, deps)

    # Track the model that actually produced the output so the publisher's
    # metadata.model_used field carries the truth (primary vs fallback).
    deps.primary_model = model_used  # type: ignore[assignment]

    output = await publish_writer_result(llm_output, input, deps)

    duration = time.perf_counter() - t0
    logger.info(
        "agent_writer: published item_id=%s subtype=%s sections=%d duration=%.2fs",
        output.item_id, input.subtype, len(llm_output.sections), duration,
    )

    return output


# ---------------------------------------------------------------------------
# LLM call helpers
# ---------------------------------------------------------------------------


async def _run_writer(
    subtype: str,
    user_message: str,
    deps: WriterDeps,
) -> tuple[WriterLLMOutput, str]:
    """Primary -> fallback chain. Always returns a valid WriterLLMOutput.

    ``deps`` is passed to ``agent.run()`` so the ``@agent.system_prompt``
    callable (``inject_workspace_context``) can access briefing, attached_items,
    revising_item_id, detail_level, and tone at run time.

    On hard failure of both primary and fallback, returns a degraded
    placeholder so the pipeline can still produce something for the user.
    """
    primary_model = deps.primary_model
    fallback_model = deps.fallback_model

    try:
        agent = create_writer_agent(deps, subtype=subtype, model_name=primary_model)
        result = await agent.run(user_message, deps=deps, usage_limits=WRITER_LIMITS)
        return _coerce_output(result), primary_model
    except Exception as exc:
        logger.warning(
            "agent_writer: primary model %s failed (%s); falling back to %s",
            primary_model, exc, fallback_model,
        )
        try:
            agent = create_writer_agent(deps, subtype=subtype, model_name=fallback_model)
            result = await agent.run(user_message, deps=deps, usage_limits=WRITER_LIMITS)
            return _coerce_output(result), fallback_model
        except Exception as exc2:
            logger.error(
                "agent_writer: fallback also failed: %s", exc2, exc_info=True
            )
            placeholder = WriterLLMOutput(
                title_ar="مسوّدة غير مكتملة",
                sections=[
                    {
                        "heading_ar": "## ملاحظة",
                        "body_md": "تعذّر توليد المستند. يرجى إعادة المحاولة لاحقاً.",
                    }
                ],
                citations_used=[],
                confidence="low",
                notes_ar=[f"model_failure: {exc2.__class__.__name__}"],
            )
            return placeholder, fallback_model


def _coerce_output(result: Any) -> WriterLLMOutput:
    """Pull a WriterLLMOutput off whatever the agent's run() returned.

    Pydantic AI normally exposes ``result.output``; tolerate stubs that
    return the WriterLLMOutput directly.
    """
    if isinstance(result, WriterLLMOutput):
        return result
    output = getattr(result, "output", None)
    if isinstance(output, WriterLLMOutput):
        return output
    raise TypeError(
        f"agent_writer: unexpected agent.run() result type: {type(result)!r}"
    )


__all__ = ["handle_writer_turn"]
