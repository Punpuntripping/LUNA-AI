"""Pydantic AI factory for the v4 Planner agent.

The planner emits a :class:`~.models.PlannerOutput` on a normal run.  When the
LLM cannot derive a plan without clarification it calls the ``ask_user``
deferred tool, which causes the agent run to terminate with a
:class:`~pydantic_ai.DeferredToolRequests` output instead.  The caller must
``isinstance``-check the result and resume via
``agent.run(message_history=..., deferred_tool_results=DeferredToolResults({...}))``.

The default model is :envvar:`LUNA_PLANNER_MODEL` if set, else ``qwen3-flash``.
"""
from __future__ import annotations

import logging
import os

from pydantic_ai import Agent, CallDeferred, DeferredToolRequests
from pydantic_ai.usage import UsageLimits

from agents.model_registry import create_model

from .models import PlannerOutput
from .prompts import PLANNER_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


PLANNER_DEFAULT_MODEL = "qwen3.6-plus"

PLANNER_LIMITS = UsageLimits(
    response_tokens_limit=4_000,
    # request_limit counts CUMULATIVE requests across pause/resume because the
    # rehydrated message_history carries forward the prior request count.
    # 4 was tight (initial model call + ask_user + resume ≈ borderline);
    # 8 leaves headroom for chained pauses + output_retries on resume.
    request_limit=8,
)


def create_planner_agent(
    model_name: str | None = None,
) -> Agent[None, PlannerOutput | DeferredToolRequests]:
    """Build a Pydantic AI agent that emits a :class:`PlannerOutput`.

    When the LLM raises ``ask_user``, the run terminates early and the output
    is a :class:`~pydantic_ai.DeferredToolRequests` instance instead.

    Args:
        model_name: Explicit model registry key. When ``None`` we fall back to
            :envvar:`LUNA_PLANNER_MODEL`, then :data:`PLANNER_DEFAULT_MODEL`.

    Returns:
        Agent ready to ``run(user_message)``. Failures of model lookup raise
        immediately — the runner is responsible for catching and degrading.
    """
    resolved = model_name or os.getenv("LUNA_PLANNER_MODEL") or PLANNER_DEFAULT_MODEL
    logger.debug("create_planner_agent: resolved model=%s", resolved)
    model = create_model(resolved)

    agent: Agent[None, PlannerOutput | DeferredToolRequests] = Agent(
        model,
        name="planner_agent",
        output_type=[PlannerOutput, DeferredToolRequests],
        instructions=PLANNER_SYSTEM_PROMPT,
        retries=2,
        output_retries=4,
    )

    @agent.tool_plain
    async def ask_user(question: str) -> str:  # noqa: RUF029
        """Ask the user a clarifying question; pauses the run until they reply.

        Use this ONLY when ambiguity blocks plan derivation (e.g. the user said
        'بحث في القضايا' without naming a sector, or said 'recent' without a
        time window that would change executor selection).  Do NOT use for
        things you can plan around or research yourself.

        When raised, the agent run terminates with a
        ``DeferredToolRequests`` output.  The caller resumes by calling
        ``agent.run(message_history=...,
        deferred_tool_results=DeferredToolResults({tool_call_id: user_reply}))``.

        Args:
            question: A single concise Arabic question for the user.

        Returns:
            The user's reply text (delivered on resume via DeferredToolResults).
        """
        raise CallDeferred

    return agent


__all__ = [
    "create_planner_agent",
    "PLANNER_DEFAULT_MODEL",
    "PLANNER_LIMITS",
]
