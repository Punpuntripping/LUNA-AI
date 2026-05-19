"""Pydantic AI factories for the two-phase Planner agent.

Two agents, one per LLM phase (PLANNER_REDESIGN_PLAN.md §4):

- :func:`create_planner_decider` — phase 1. ``output_type`` is
  ``[PlannerDecision, DeferredToolRequests]``: a normal run yields a
  :class:`PlannerDecision`; calling the ``ask_user`` deferred tool ends the run
  with a :class:`~pydantic_ai.DeferredToolRequests` instead. ``deps_type`` is
  ``None`` — phase 1 is a pure classification call with no infra deps.
- :func:`create_planner_responder` — phase 3. ``deps_type`` is
  :class:`PlannerDeps`; a dynamic ``@instructions`` callback injects the trimmed
  artifact digest + mode framing. Output is :class:`PlannerResponse`.

Both phases resolve their model through the tier system
(:func:`agents.utils.agent_models.get_agent_model`) via the ``planner_decider``
and ``planner_responder`` slots. The decider is a tiny classification call; the
responder writes Arabic prose — distinct slots allow separate tiers later.
"""
from __future__ import annotations

import logging

from pydantic_ai import Agent, CallDeferred, DeferredToolRequests, RunContext
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import ModelPolicy, get_agent_model

from .deps import PlannerDeps
from .models import PlannerDecision, PlannerResponse
from .prompts import (
    PLANNER_DECIDER_SYSTEM_PROMPT,
    PLANNER_RESPONDER_SYSTEM_PROMPT,
    build_responder_instructions,
)

logger = logging.getLogger(__name__)


PLANNER_DECIDER_LIMITS = UsageLimits(
    output_tokens_limit=4_000,
    # request_limit counts CUMULATIVE requests across pause/resume — the
    # rehydrated message_history carries the prior request count forward. The
    # budget covers: initial call + ask_user + resume call + output_retries.
    # 4 was too tight and broke resume once; 8 leaves headroom. Do NOT lower it.
    request_limit=8,
)

PLANNER_RESPONDER_LIMITS = UsageLimits(
    output_tokens_limit=8_000,
    request_limit=4,
)


def create_planner_decider(
    model_override: ModelPolicy | str | None = None,
) -> Agent[None, PlannerDecision | DeferredToolRequests]:
    """Build the phase-1 decider agent.

    A normal run emits a :class:`PlannerDecision`. When the LLM calls
    ``ask_user`` the run ends early with a :class:`DeferredToolRequests`; the
    caller resumes via ``agent.run(message_history=...,
    deferred_tool_results=DeferredToolResults({tool_call_id: reply}))``.

    ``model_override`` is an optional tier override token / :class:`ModelPolicy`
    for the ``planner_decider`` slot (tier stays fixed).
    """
    model = get_agent_model("planner_decider", model_override)

    agent: Agent[None, PlannerDecision | DeferredToolRequests] = Agent(
        model,
        name="planner_decider",
        output_type=[PlannerDecision, DeferredToolRequests],
        instructions=PLANNER_DECIDER_SYSTEM_PROMPT,
        retries=2,
        output_retries=4,
    )

    @agent.tool_plain
    async def ask_user(question: str) -> str:  # noqa: RUF029
        """Ask the user one clarifying question; pauses the run until they reply.

        Use this ONLY when the query names a corpus/domain but no concrete legal
        question, so no useful retrieval can be derived (e.g. «ابحث في القضايا
        البنكية»). Do NOT use it for anything you can plan around or research.

        When raised, the run terminates with a ``DeferredToolRequests`` output.
        The caller resumes via ``agent.run(message_history=...,
        deferred_tool_results=DeferredToolResults({tool_call_id: user_reply}))``.

        Args:
            question: A single concise Arabic question for the user.

        Returns:
            The user's reply text (delivered on resume via DeferredToolResults).
        """
        raise CallDeferred

    return agent


def create_planner_responder(
    model_override: ModelPolicy | str | None = None,
) -> Agent[PlannerDeps, PlannerResponse]:
    """Build the phase-3 responder agent.

    Emits a :class:`PlannerResponse` (chat summary + suggestion). A dynamic
    ``@instructions`` callback reads ``ctx.deps._agg_output`` + ``_decision`` and
    injects the trimmed artifact digest + mode-specific chat-summary framing.

    ``model_override`` is an optional tier override token / :class:`ModelPolicy`
    for the ``planner_responder`` slot (tier stays fixed).
    """
    model = get_agent_model("planner_responder", model_override)

    agent: Agent[PlannerDeps, PlannerResponse] = Agent(
        model,
        name="planner_responder",
        deps_type=PlannerDeps,
        output_type=PlannerResponse,
        instructions=PLANNER_RESPONDER_SYSTEM_PROMPT,
        retries=2,
        output_retries=4,
    )

    @agent.instructions
    def _artifact_digest(ctx: RunContext[PlannerDeps]) -> str:
        """Inject the per-turn artifact digest + mode framing (see prompts.py)."""
        return build_responder_instructions(ctx.deps)

    return agent


__all__ = [
    "PLANNER_DECIDER_LIMITS",
    "PLANNER_RESPONDER_LIMITS",
    "create_planner_decider",
    "create_planner_responder",
]
