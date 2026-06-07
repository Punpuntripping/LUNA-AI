"""The 3 tools the writer_planner decider can call.

Tools are registered on the Pydantic AI agent via :func:`register_tools`.
Splitting them out of ``agent.py`` keeps the agent factory short and lets
unit tests import individual tool functions without instantiating the agent.

The 3 tools:

| Tool                          | Layer    | Deferred? | Purpose |
|-------------------------------|----------|-----------|---------|
| ``analyze_items``             | calls L4 | no        | Verdict per-WI (full/partial/none) via the shared item_analyzer. Stashes the result on ``deps.last_analyzer_output``. |
| ``ask_user``                  | -        | YES       | Pauses with ``pause_reason='clarify'``. Use only when something critical is missing. |
| ``present_plan_for_approval`` | -        | YES       | Pauses with ``pause_reason='approve_plan'``. Tracks ``present_count`` for the 3-cap. |

The user's قوالبي templates are NOT fetched via a tool — their titles are
injected into the planner's context as a ``<my_templates>`` block (see
``prompts.py``), so the planner reads them passively and picks one by its
``TPL-{n}`` alias on the final ``PlannerDecision``.

Both deferred tools raise ``CallDeferred`` to end the run with a
``DeferredToolRequests`` output. The orchestrator distinguishes them by the
tool name in the deferred payload and writes ``agent_runs.pause_reason``
accordingly.
"""
from __future__ import annotations

import logging

from pydantic_ai import Agent, CallDeferred, ModelRetry, RunContext

from agents.memory.item_analyzer import (
    AnalyzeOutput,
    AnalyzerCall,
    analyze,
    build_analyzer_deps,
)

# Runtime import (NOT TYPE_CHECKING) — Pydantic AI resolves tool function
# type hints at registration time via _function_schema.function_schema, and
# a string forward reference to WriterPlannerDeps would fail with NameError.
from .deps import WriterPlannerDeps


logger = logging.getLogger(__name__)


# Hard cap on present_plan_for_approval cycles. The 4th call auto-approves
# (no pause) so the planner is forced to emit a final PlannerDecision next
# round. See .claude/plans/writer_planner.md § Iteration cap — hard at 3.
MAX_PRESENT_CYCLES: int = 3


def register_tools(
    agent: Agent[WriterPlannerDeps, list],
) -> None:
    """Register the 3 planner tools on the given Pydantic AI agent.

    Called once from ``agent.py::create_writer_planner_decider`` right after
    the ``Agent(...)`` constructor. Splits out so the registration list is
    one place; the agent factory stays focused on model + output_type wiring.
    """

    # -----------------------------------------------------------------------
    # 1. analyze_items — calls the shared item_analyzer (Layer-4 Memory).
    # -----------------------------------------------------------------------
    @agent.tool
    async def analyze_items(
        ctx: RunContext[WriterPlannerDeps],
        query: str,
        targeted_wi: list[str],
    ) -> AnalyzeOutput:
        """Ask the item analyzer to verdict each workspace_item against this query.

        Returns one verdict per resolvable WI:
          - need='full'    → the entire content_md is on-topic; the runner will
                             unfold it into the WriterPackage.
          - need='partial' → only a slice is on-topic; the analyzer's
                             `distilled` field carries it. For refs-family WIs
                             the runner resolves `refs_needed` via
                             references_service; for meta-family WIs it uses
                             `extracted_metadata` verbatim.
          - need='none'    → irrelevant; drop the WI from the package.

        Make ONE call per turn with all WIs you want triaged (mixed kinds are
        fine — the analyzer's runner partitions internally). Calling it once
        per kind or once per item wastes tokens.

        rational / overall_rational are PLANNER-FACING — use them to shape
        your plan_md and your final rationale. They are NOT passed to the
        writing executor.

        Args:
            query: Your question for the analyzer, verbatim Arabic. Frame it
                from the writing executor's perspective — what would the
                executor want to know about these WIs to draft well?
            targeted_wi: One or more ``WI-{seq}`` aliases (e.g.
                ``["WI-1", "WI-3"]``) drawn from the labels rendered in
                <attached_items> / <prior_artifacts>. Raw UUIDs are NOT
                accepted on this surface — use the aliases shown in your
                context. An unknown alias raises a retry asking you to
                pick from the available labels.

        Returns:
            AnalyzeOutput with one WIVerdict per resolvable id (ordered to
            match the input). The result is also stashed on
            ``ctx.deps.last_analyzer_output`` so the runner can walk verdicts
            after you emit your final PlannerDecision.
        """
        deps = ctx.deps
        if not targeted_wi:
            empty = AnalyzeOutput(query_echo=query, items=[], overall_rational=None)
            deps.last_analyzer_output = empty
            return empty

        # Resolve every WI-{seq} alias → workspace_items.item_id UUID before
        # invoking the analyzer (analyzer is a Layer-4 sibling — its API
        # operates on raw UUIDs). Unknown aliases raise ModelRetry so the
        # LLM sees an Arabic error and can self-correct with a valid label.
        resolved_ids: list[str] = []
        for alias in targeted_wi:
            resolved = deps.resolve_wi_alias(alias)
            if resolved is None:
                raise ModelRetry(
                    f"العنصر {alias} غير موجود في هذه المحادثة. "
                    f"استخدم رمزاً من <attached_items> أو <prior_artifacts> "
                    f"(WI-1, WI-2, ...)."
                )
            resolved_ids.append(resolved)

        analyzer_deps = build_analyzer_deps(
            supabase=deps.supabase,
            http_client=deps.http_client,
            user_id=deps.user_id,
            conversation_id=deps.conversation_id,
            caller_id="writer_planner",
        )
        try:
            result = await analyze(
                AnalyzerCall(query=query, targeted_wi=resolved_ids),
                analyzer_deps,
            )
        except Exception as exc:
            logger.warning(
                "writer_planner.analyze_items: analyzer failed (%s) — "
                "returning empty AnalyzeOutput so the planner can proceed",
                exc,
            )
            result = AnalyzeOutput(query_echo=query, items=[], overall_rational=None)

        # Stash for the runner's verdict-walk after the LLM emits PlannerDecision.
        deps.last_analyzer_output = result
        return result

    # -----------------------------------------------------------------------
    # 2. ask_user — deferred. Pauses with pause_reason='clarify'.
    # -----------------------------------------------------------------------
    @agent.tool_plain
    async def ask_user(question: str) -> str:  # noqa: RUF029
        """Ask the user ONE clarifying question; pauses the run until they reply.

        Use this ONLY when a critical fact for drafting is missing AND cannot
        be inferred from <attached_items>, <prior_artifacts>, or the user
        message itself. Examples of valid use:
          - User said «اكتب العقد» but no party names appear anywhere.
          - User asked for a memo on «القضية» but no case identifier appears.

        Do NOT use ask_user for anything you can plan around or where the
        examine-before-asking protocol shows the answer is already on screen.
        The Saudi-lawyer contract example («اكتب لي العقد بالأرقام: 40K،
        20+20، تاريخ 1447/1/18» + PDF template + 2 image sources) requires
        ZERO clarification questions. Get the bar that high.

        When raised, the run terminates with a DeferredToolRequests output.
        The orchestrator persists the agent_runs row with
        pause_reason='clarify' and surfaces the question_text in chat.

        Args:
            question: A single concise Arabic question. Single question per
                pause — don't ask compound questions.

        Returns:
            The user's reply text (delivered on resume via DeferredToolResults).
        """
        raise CallDeferred

    # -----------------------------------------------------------------------
    # 3. present_plan_for_approval — deferred. pause_reason='approve_plan'.
    #    Increments deps.present_count; 4th call auto-approves (no pause).
    # -----------------------------------------------------------------------
    @agent.tool
    async def present_plan_for_approval(
        ctx: RunContext[WriterPlannerDeps],
        plan_md: str,
    ) -> str:
        """Present a plan_md to the user for approval; pauses until they reply.

        Use when strategy is genuinely unclear AFTER examining
        <attached_items>, <prior_artifacts>, and the user's message. Do NOT
        use for clean turns (subtype + template + parameters all present);
        emit a final PlannerDecision directly instead.

        The plan_md should be short Arabic markdown:
          1. ## النوع: what kind of document you'll draft.
          2. ## المرجع: which item plays which role (template, source, ...).
             If you will draft from one of the user's قوالبي templates, NAME it
             here («القالب: <العنوان>») — and when two titles plausibly fit,
             list both and ask the user to choose one.
          3. ## المعطيات: parties, dates, amounts you'll fill in.
          4. ## الإخراج: a 2-3 line summary of what the output will look like.

        Hard cap: 3 present cycles per turn (``MAX_PRESENT_CYCLES``). The 4th
        call auto-approves with this plan_md and returns 'موافق' without
        pausing — DO NOT rely on this; aim to land approval on the first
        present.

        When raised (cycles 1-3), the run terminates with a
        DeferredToolRequests output. The orchestrator persists the agent_runs
        row with pause_reason='approve_plan'.

        Args:
            plan_md: Markdown plan in Arabic. Surfaced verbatim in chat.

        Returns:
            The user's reply text on resume, OR the literal string
            'موافق-تلقائي' if the auto-approve cap triggered.
        """
        # Cap check BEFORE incrementing — present_count tracks completed cycles.
        if ctx.deps.present_count >= MAX_PRESENT_CYCLES:
            logger.warning(
                "writer_planner.present_plan_for_approval: cap reached "
                "(%d / %d) — auto-approving without pause",
                ctx.deps.present_count,
                MAX_PRESENT_CYCLES,
            )
            ctx.deps.present_count += 1
            return "موافق-تلقائي"

        ctx.deps.present_count += 1
        raise CallDeferred


__all__ = ["register_tools", "MAX_PRESENT_CYCLES"]
