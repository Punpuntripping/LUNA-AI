"""Runner -- orchestrates LLM call -> publish for one writing-executor turn.

Two input contracts:

1. **WriterInput** (legacy) — flat shape with user_request +
   research_items + workspace_context. The runner builds the legacy XML
   user message, runs the LLM, calls ``publish_writer_result``,
   and returns a ``WriterOutput``. Used by callers that haven't
   moved to the WriterPackage / writer_planner handoff yet.

2. **WriterPackage** (planner handoff) — structured shape with intent_ar +
   analyzed_items (full/partial verdicts) + plan_md + system_templates +
   style. The runner stashes the package on ``deps`` (so the agent's
   ``package_content_block`` dynamic instruction renders it as a system
   block), builds a *minimal* user message (intent + directive only) via
   ``build_writer_user_message_minimal``, runs the LLM, and returns a
   ``WriterLLMOutput`` **without publishing**. Publication is the
   writer_planner runner's responsibility (Layer-2 Major owns the
   workspace_item write per .claude/plans/wave_9_agent_runs.md).

The runner branches via ``isinstance`` at the top of
``handle_writer_turn`` and dispatches to the appropriate inner
helper.

Pipeline (legacy path)::

    populate_deps_from_input -> build_user_message -> primary LLM (with
    fallback, deps= injected) -> publish_writer_result

Pipeline (package path)::

    stash deps.package -> default envelope fields ->
    build_writer_user_message_minimal -> primary LLM (with fallback;
    package_content_block renders deps.package as a system block) ->
    return LLMOutput (planner publishes upstream)
"""
from __future__ import annotations

import logging
import time
from typing import Any, overload

from .agent import WRITER_LIMITS, create_writer_agent
from .deps import WriterDeps
from .models import (
    WriterPackage,
    WriterInput,
    WriterLLMOutput,
    WriterOutput,
)
from .prompts import (
    build_writer_user_message,
    build_writer_user_message_minimal,
)
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
    The new fields (describe_query=user_request, attached_items from
    WorkspaceItemSnapshot, revising_item_id, detail_level, tone) are mapped
    here so they reach the @agent.system_prompt callable without touching the
    user message string.
    """
    # describe_query: user_request is the canonical task statement.
    if not deps.describe_query:
        deps.describe_query = input.user_request or ""

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


def _populate_deps_from_package(package: WriterPackage, deps: WriterDeps) -> None:
    """Defensive envelope-field defaulting from the package.

    The PRIMARY channel for package content is now ``deps.package`` (stashed
    in ``_handle_package_turn``) which feeds the ``package_content_block``
    dynamic instruction in ``agent.py``. This helper only fills in the
    envelope fields (``describe_query`` / ``detail_level`` / ``tone``) when
    the caller hasn't already set them on ``deps``.

    ``revising_item_id`` is set by the writer_planner runner when it builds
    ``exec_deps`` (see ``agents/writer_planner/runner.py``); a defensive
    fallback to ``package.prior_draft().item_id`` remains for the rare case
    where the planner skipped it.

    ``deps.attached_items`` is NOT populated from ``package.analyzed_items``
    — the package itself flows through the dynamic instruction, so populating
    attached_items as well would double-render. See ``.claude/plans/
    writer_redesign.md`` Open Question #4.
    """
    if not deps.describe_query:
        deps.describe_query = package.intent_ar or ""

    # Style prefs — only override default sentinels ("standard" / "neutral").
    if deps.detail_level == "standard":
        deps.detail_level = package.style.detail_level
    if deps.tone == "neutral":
        deps.tone = package.style.tone

    # Defensive fallback ONLY — the planner runner already sets
    # ``revising_item_id`` when it builds ``exec_deps``.
    if deps.revising_item_id is None:
        prior = package.prior_draft()
        if prior is not None:
            deps.revising_item_id = prior.item_id


# ---------------------------------------------------------------------------
# Public entrypoint with isinstance-branched dispatch
# ---------------------------------------------------------------------------


@overload
async def handle_writer_turn(
    input: WriterInput,
    deps: WriterDeps,
) -> WriterOutput: ...


@overload
async def handle_writer_turn(
    input: WriterPackage,
    deps: WriterDeps,
) -> WriterLLMOutput: ...


async def handle_writer_turn(
    input: WriterInput | WriterPackage,
    deps: WriterDeps,
) -> WriterOutput | WriterLLMOutput:
    """Run one writing-executor turn end-to-end.

    Branches on input type:
      - WriterPackage → planner handoff path: build new XML, run LLM, return
        the raw ``WriterLLMOutput``. Does NOT publish. The
        writer_planner runner publishes upstream.
      - WriterInput → legacy path: build legacy XML, run LLM,
        publish, return ``WriterOutput``.
    """
    if isinstance(input, WriterPackage):
        return await _handle_package_turn(input, deps)
    return await _handle_legacy_turn(input, deps)


# ---------------------------------------------------------------------------
# Package path — no publish, returns LLMOutput
# ---------------------------------------------------------------------------


async def _handle_package_turn(
    package: WriterPackage,
    deps: WriterDeps,
) -> WriterLLMOutput:
    """Planner-handoff path: drafts from a WriterPackage, returns raw LLM output.

    Per ``.claude/plans/writer_redesign.md`` § Runner changes:
      1. Stash the package on ``deps`` so the ``package_content_block``
         dynamic instruction can render it as a system block.
      2. Default the envelope fields (describe_query / detail_level / tone)
         from the package — defensive only; the planner runner usually sets
         them when it builds ``exec_deps``.
      3. Build the *minimal* user message (intent + directive only). All
         structured content rides the system prompt now.
      4. Run the LLM with the standard primary→fallback chain.

    The writer_planner runner is responsible for ``publish_writer_result``
    after this returns.
    """
    t0 = time.perf_counter()

    # 1. PRIMARY channel: stash the package on deps so the dynamic
    #    instruction callables can render it.
    deps.package = package

    # 2. Defensive envelope-field defaulting from the package.
    _populate_deps_from_package(package, deps)

    # 3. Minimal user message — intent + one-line directive. The package
    #    itself flows through ``package_content_block`` on the system side.
    user_message = build_writer_user_message_minimal(package)

    llm_output, model_used = await _run_writer(package.subtype, user_message, deps)

    # Track the model that actually produced the output for downstream telemetry.
    deps.primary_model = model_used  # type: ignore[assignment]

    duration = time.perf_counter() - t0
    logger.info(
        "writing_executor: package draft complete subtype=%s sections=%d "
        "items=%d duration=%.2fs (publish deferred to planner)",
        package.subtype,
        len(llm_output.sections),
        len(package.analyzed_items),
        duration,
    )
    return llm_output


# ---------------------------------------------------------------------------
# Legacy path — publishes and returns WriterOutput (unchanged)
# ---------------------------------------------------------------------------


async def _handle_legacy_turn(
    input: WriterInput,
    deps: WriterDeps,
) -> WriterOutput:
    """Legacy WriterInput path — publishes a workspace_item row."""
    t0 = time.perf_counter()

    _populate_deps_from_input(input, deps)

    user_message = build_writer_user_message(input)

    llm_output, model_used = await _run_writer(input.subtype, user_message, deps)

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
    callable (``inject_workspace_context``) can access describe_query,
    attached_items, revising_item_id, detail_level, and tone at run time.

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
