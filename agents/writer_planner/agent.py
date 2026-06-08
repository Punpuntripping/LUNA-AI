"""Pydantic AI agent factory for the writer_planner decider.

Builds an ``Agent[WriterPlannerDeps, list[PlannerDecision | DeferredToolRequests]]``:

- Normal final emission → :class:`PlannerDecision`.
- Pause emission → ``DeferredToolRequests`` (from ``ask_user`` or
  ``present_plan_for_approval``).

The two deferred tools (``ask_user``, ``present_plan_for_approval``) are
registered via :func:`agents.writer_planner.tools.register_tools`; the
deterministic ``unfold_workspace_item`` reader is registered separately
below. This factory stays focused on model + output_type wiring + dynamic
instructions.

See `.claude/plans/writer_planner.md` § Decider construction.
"""
from __future__ import annotations

import logging

from pydantic_ai import Agent, DeferredToolRequests, RunContext
from pydantic_ai.usage import UsageLimits

from agents.tool_repository.add_user_template import register_add_user_template
from agents.tool_repository.unfold_workspace_item import register_unfold_workspace_item
from agents.utils.agent_models import ModelPolicy, get_agent_model

from .deps import WriterPlannerDeps
from .models import PlannerDecision
from .prompts import WRITER_PLANNER_SYSTEM_PROMPT, build_writer_planner_instructions
from .tools import register_tools

logger = logging.getLogger(__name__)


# Per-turn usage budget. Sized for the worst-case planner turn:
#   1 initial call
#   + 1-2 unfold_workspace_item calls (inspect content when summaries are thin)
#   + 1 present_plan_for_approval → pause → resume
#   + 2 re-plan rounds (if user rejects + reshapes)
#   + 1-2 output_retries
# Total ≈ 12 requests. 16 gives a small safety margin without inviting runaway.
#
# output_tokens_limit=40k matches the deep_search planner — tier_1 qwen has
# thinking ON by default and `output_tokens` INCLUDES reasoning tokens in the
# API response, so a tight cap crashes on complex turns.
WRITER_PLANNER_LIMITS = UsageLimits(
    output_tokens_limit=40_000,
    request_limit=16,
)


def create_writer_planner_decider(
    model_override: ModelPolicy | str | None = None,
) -> "Agent[WriterPlannerDeps, list[PlannerDecision | DeferredToolRequests]]":
    """Build the writer_planner decider agent.

    A normal run emits a :class:`PlannerDecision`. When the LLM calls
    ``ask_user`` or ``present_plan_for_approval`` the run ends early with a
    ``DeferredToolRequests`` output; the orchestrator persists the agent_runs
    row in ``status='awaiting_user'`` and resumes later via
    ``agent.run(message_history=..., deferred_tool_results=...)``.

    ``model_override`` is an optional tier override token / :class:`ModelPolicy`
    for the ``writer_planner_decider`` slot (tier stays fixed at tier_1).

    Output type uses the **list** syntax ``[PlannerDecision, DeferredToolRequests]``
    rather than a single ``Union`` — each list member becomes a separate
    output tool in the underlying API call. Cleaner schema, better selection
    accuracy. The runner type-narrows on ``isinstance`` of the output.
    """
    model = get_agent_model("writer_planner_decider", model_override)

    agent: Agent[WriterPlannerDeps, list[PlannerDecision | DeferredToolRequests]] = Agent(
        model,
        name="writer_planner_decider",
        deps_type=WriterPlannerDeps,
        output_type=[PlannerDecision, DeferredToolRequests],
        instructions=WRITER_PLANNER_SYSTEM_PROMPT,
        retries=2,
        output_retries=4,
    )

    @agent.instructions
    def _per_turn_context(ctx: RunContext[WriterPlannerDeps]) -> str:
        """Render the per-turn dynamic instruction block (summary-only — see § Core invariant)."""
        return build_writer_planner_instructions(ctx.deps)

    # Register the 2 deferred tools (ask_user,
    # present_plan_for_approval). See tools.py.
    register_tools(agent)

    # Reusable cross-agent tool: save a markdown template to the user's
    # personal "قوالبي" library. WriterPlannerDeps exposes both .supabase and
    # .user_id, so it structurally satisfies HasUserContext. See
    # agents/tool_repository/add_user_template.py.
    register_add_user_template(agent)

    # Deterministic full read of one workspace item + a used-only, [n]-keyed
    # manifest of the named sources it cites. This is the planner's ONLY
    # content-inspection tool (it replaced the item_analyzer triage path):
    # call it when a summary is too thin to judge relevance, or when the user
    # points at a specific named regulation/ruling/service inside a prior item
    # and you need its exact content + citations. WriterPlannerDeps exposes
    # .supabase / .user_id / .wi_alias_map (satisfies HasWorkspaceContext).
    register_unfold_workspace_item(agent)

    return agent


__all__ = [
    "WRITER_PLANNER_LIMITS",
    "create_writer_planner_decider",
]
