"""The 2 tools the writer_planner decider can call.

Tools are registered on the Pydantic AI agent via :func:`register_tools`.
Splitting them out of ``agent.py`` keeps the agent factory short and lets
unit tests import individual tool functions without instantiating the agent.

The 2 tools:

| Tool                          | Deferred? | Purpose |
|-------------------------------|-----------|---------|
| ``ask_user``                  | YES       | Pauses with ``pause_reason='clarify'``. Only when the document type itself is unknowable. |
| ``present_plan_for_approval`` | YES       | Pauses with ``pause_reason='approve_plan'``. Tracks ``present_count`` for the 3-cap. |

Plus the shared, cross-agent ``unfold_workspace_item`` (registered in
``agent.py``): a deterministic full read of ONE workspace item + its
used-only ``[n]``-keyed cited-source manifest. The planner calls it to
inspect content when a summary is too thin to judge relevance — it replaced
the old item_analyzer triage path entirely. Selected items are inlined into
the WriterPackage by the runner (full content), so there is no LLM triage
step on the writer_planner anymore.

Templates are NOT fetched via a tool — the catalog (user's own + ours) is
injected into the planner's context as a ``<templates_catalog>`` block (see
``prompts.py``), so the planner reads them passively and picks one by its
``TPL-{n}`` alias on the final ``PlannerDecision``.

Both deferred tools raise ``CallDeferred`` to end the run with a
``DeferredToolRequests`` output. The orchestrator distinguishes them by the
tool name in the deferred payload and writes ``agent_runs.pause_reason``
accordingly.
"""
from __future__ import annotations

import logging

from pydantic_ai import Agent, CallDeferred, RunContext

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
    """Register the 2 deferred planner tools on the given Pydantic AI agent.

    Called once from ``agent.py::create_writer_planner_decider`` right after
    the ``Agent(...)`` constructor. Splits out so the registration list is
    one place; the agent factory stays focused on model + output_type wiring.

    ``unfold_workspace_item`` (the deterministic content reader) is registered
    separately in ``agent.py`` via the shared tool_repository helper.
    """

    # -----------------------------------------------------------------------
    # 1. ask_user — deferred. Pauses with pause_reason='clarify'.
    # -----------------------------------------------------------------------
    @agent.tool_plain
    async def ask_user(question: str) -> str:  # noqa: RUF029
        """Ask the user ONE clarifying question; pauses the run until they reply.

        Rare. Use it ONLY when you cannot tell what kind of document is wanted
        at all. Missing names, trade names, ID numbers, amounts, dates or court
        details are NOT a reason: they go in the plan (as placeholders under
        «ما سيُترك فارغاً لتعبئته») via ``present_plan_for_approval``.

        When raised, the run terminates with a DeferredToolRequests output.
        The orchestrator persists the agent_runs row with
        pause_reason='clarify' and surfaces the question_text in chat.

        Args:
            question: A single concise question in the user's language.

        Returns:
            The user's reply text (delivered on resume via DeferredToolResults).
        """
        raise CallDeferred

    # -----------------------------------------------------------------------
    # 2. present_plan_for_approval — deferred. pause_reason='approve_plan'.
    #    Increments deps.present_count; 4th call auto-approves (no pause).
    # -----------------------------------------------------------------------
    @agent.tool
    async def present_plan_for_approval(
        ctx: RunContext[WriterPlannerDeps],
        plan_md: str,
    ) -> str:
        """Present THE plan of a new document; pauses until the user replies.

        This is the single pause of a drafting request. plan_md is short
        markdown in the user's language, in this order (drop empty sections):
          1. ## النماذج المقترحة — the template(s) from <templates_catalog>,
             title verbatim + «(قالب عام)» / «(قالب خاص)»; two numbered options
             only when two genuinely fit; «لم يُستخدم قالب» when none does.
          2. ## الأطراف — each party with its role (placeholder if unnamed).
          3. ## ما سيتضمنه المستند — claims / sections / requests, short.
          4. ## ما سيُترك فارغاً لتعبئته — details nobody gave.
          5. ## مستندات أشرت إليها ولم تُرفق — when applicable.
          End with «هل أبدأ الكتابة؟».

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
