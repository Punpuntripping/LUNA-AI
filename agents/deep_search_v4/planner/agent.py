"""Pydantic AI factories for the two-phase Planner agent.

Two agents, one per LLM phase (PLANNER_REDESIGN_PLAN.md §4):

- :func:`create_planner_decider` — phase 1. ``output_type`` is
  ``[PlannerDecision, DeferredToolRequests]``: a normal run yields a
  :class:`PlannerDecision`; calling the ``ask_user`` deferred tool ends the run
  with a :class:`~pydantic_ai.DeferredToolRequests` instead. ``deps_type`` is
  :class:`PlannerDeps` (Phase C — flipped from ``None``): the decider now owns
  the comprehension surface (case_brief, recent_messages, prior_searches,
  attached_items rendered into dynamic instructions) AND a synchronously-
  resolving ``read_workspace_item`` tool. ``ask_user``'s deferred-tool pause
  semantics coexist with ``read_workspace_item``'s normal in-loop semantics on
  the same agent — only ``ask_user`` raises ``CallDeferred``.
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
    build_decider_instructions,
    build_responder_instructions,
)

logger = logging.getLogger(__name__)


PLANNER_DECIDER_LIMITS = UsageLimits(
    # 40k is "thinking can never crash us" headroom. qwen3.6-plus on DashScope
    # has thinking ON by default and `output_tokens` in the API response
    # INCLUDES reasoning tokens — so a tight cap (the old 4000) blew up on
    # complex queries (run 1779196337 — 4782 reasoning + 122 text = 4904, crash,
    # 91.9s wasted on a recovered default decision). Observed real output
    # excluding reasoning is ~200-1700 tokens.
    output_tokens_limit=40_000,
    # request_limit counts CUMULATIVE requests across pause/resume — the
    # rehydrated message_history carries the prior request count forward. The
    # budget covers (Phase C / O6): 1 initial + up to 3 `read_workspace_item`
    # tool calls + 1 `ask_user` → pause → 1 resume + 1–2 output_retries +
    # headroom = 16. Bumped from 8 in Phase C to make room for the new tool.
    # Do NOT lower it.
    request_limit=16,
)

PLANNER_RESPONDER_LIMITS = UsageLimits(
    # 40k for the same reason as the decider — reasoning counts against output.
    # Observed max single-turn output is ~3300; 40k = 12x margin.
    output_tokens_limit=40_000,
    request_limit=4,
)


def create_planner_decider(
    model_override: ModelPolicy | str | None = None,
) -> Agent[PlannerDeps, PlannerDecision | DeferredToolRequests]:
    """Build the phase-1 decider agent.

    A normal run emits a :class:`PlannerDecision`. When the LLM calls
    ``ask_user`` the run ends early with a :class:`DeferredToolRequests`; the
    caller resumes via ``agent.run(message_history=...,
    deferred_tool_results=DeferredToolResults({tool_call_id: reply}))``.

    Phase C: ``deps_type`` is now :class:`PlannerDeps` (was ``None``). The
    decider owns the comprehension surface (dynamic instructions render
    ``case_brief`` / ``recent_messages`` / ``prior_searches`` / ``attached_items``
    from deps) AND the ``read_workspace_item`` tool (a scoped, synchronously
    resolving DB read of one ``workspace_items.content_md``). The deferred
    ``ask_user`` and the normal ``read_workspace_item`` coexist on the same
    agent — only ``ask_user`` raises ``CallDeferred``.

    ``model_override`` is an optional tier override token / :class:`ModelPolicy`
    for the ``planner_decider`` slot (tier stays fixed).
    """
    model = get_agent_model("planner_decider", model_override)

    agent: Agent[PlannerDeps, PlannerDecision | DeferredToolRequests] = Agent(
        model,
        name="planner_decider",
        deps_type=PlannerDeps,
        output_type=[PlannerDecision, DeferredToolRequests],
        instructions=PLANNER_DECIDER_SYSTEM_PROMPT,
        retries=2,
        output_retries=4,
    )

    @agent.instructions
    def _comprehension(ctx: RunContext[PlannerDeps]) -> str:
        """Inject the per-turn comprehension blocks (case_brief / messages / …)."""
        return build_decider_instructions(ctx.deps)

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

    @agent.tool
    async def read_workspace_item(
        ctx: RunContext[PlannerDeps], item_id: str
    ) -> str:
        """Open a workspace item by id and return its full content_md.

        Returns the raw ``content_md`` string of one workspace_item.

        Scope: ONLY items in the current conversation. Enforced explicitly by
        filtering on both ``user_id`` and ``conversation_id`` — RLS is the
        second line of defense, not the first (the backend runs as service_role
        and bypasses RLS, so the in-tool filter is load-bearing).

        Use this BEFORE deciding mode / writing planner_brief when:
        - The user references «التقرير السابق» / «البحث الماضي» and the prior
          artifact's summary isn't enough.
        - prior_searches shows a related search with low confidence — read its
          full content to see what failed, then formulate a sharper plan.
        - User's question references «الملف» / «العقد» attached to the conversation.

        Returns: full ``content_md`` string on success; empty string ``""``
        when the item is not found, deleted, or out of scope (silently — do
        NOT retry); Arabic error string «خطأ أثناء قراءة العنصر» on actual DB
        exceptions.

        Hard cap: do not call this tool more than 3 times per turn. The decider
        should pick at most the 2–3 most relevant prior artifacts to read.
        """
        ctx.deps._events.append({
            "type": "tool_call",
            "tool": "read_workspace_item",
            "item_id": item_id,
        })
        try:
            result = (
                ctx.deps.supabase.table("workspace_items")
                .select("content_md")
                .eq("item_id", item_id)
                .eq("user_id", ctx.deps.user_id)
                .eq("conversation_id", ctx.deps.conversation_id)   # explicit scope, not RLS hope
                .is_("deleted_at", "null")
                .maybe_single()
                .execute()
            )
            if result and getattr(result, "data", None):
                content = result.data.get("content_md") or ""
                logger.info(
                    "planner.read_workspace_item: loaded %s in conv %s (%d chars)",
                    item_id, ctx.deps.conversation_id, len(content),
                )
                return content
            logger.info(
                "planner.read_workspace_item: %s not found / out of scope in conv %s",
                item_id, ctx.deps.conversation_id,
            )
            return ""
        except Exception as exc:
            logger.warning(
                "planner.read_workspace_item error for %s: %s", item_id, exc,
            )
            return f"خطأ أثناء قراءة العنصر: {type(exc).__name__}"

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
