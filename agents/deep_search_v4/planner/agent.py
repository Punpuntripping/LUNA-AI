"""Pydantic AI factories for the two-phase Planner agent.

Two agents, one per LLM phase (PLANNER_REDESIGN_PLAN.md §4):

- :func:`create_planner_decider` — phase 1. ``output_type`` is
  ``[PlannerDecision, DeferredToolRequests]``: a normal run yields a
  :class:`PlannerDecision`; calling the ``ask_user`` deferred tool ends the run
  with a :class:`~pydantic_ai.DeferredToolRequests` instead. ``deps_type`` is
  :class:`PlannerDeps`: the decider owns the comprehension surface (case_brief,
  recent_messages, prior_searches, attached_items — including full
  attachment ``content_md`` — rendered into dynamic instructions). ``ask_user``
  is its only tool, raising ``CallDeferred`` to pause for a clarifying question.
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
import re

from pydantic_ai import Agent, CallDeferred, DeferredToolRequests, ModelRetry, RunContext
from pydantic_ai.usage import UsageLimits

from agents.tool_repository.unfold_workspace_item import register_unfold_workspace_item
from agents.utils.agent_models import ModelPolicy, get_agent_model

from .deps import PlannerDeps
from .models import PlannerDecision, PlannerResponse
from .prompts import (
    PLANNER_DECIDER_SYSTEM_PROMPT,
    PLANNER_RESPONDER_SYSTEM_PROMPT,
    build_decider_instructions,
    build_responder_instructions,
)


# ── WI alias resolver (migration 052 / agent communication protocol) ──────────
# Mirrors agents/router/router.py — kept inline here so the planner package
# stays self-contained.

_WI_ALIAS_RE = re.compile(r"^WI-(\d+)$", re.IGNORECASE)
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _resolve_wi_alias(alias: str, alias_map: dict[int, str]) -> str | None:
    """Resolve ``"WI-{seq}"`` → workspace_items.item_id UUID.

    Accepts a raw UUID verbatim (defence-in-depth for older callers).
    Returns ``None`` if the alias is malformed or unknown.
    """
    if not alias:
        return None
    s = alias.strip()
    m = _WI_ALIAS_RE.match(s)
    if m:
        try:
            seq = int(m.group(1))
        except ValueError:
            return None
        return alias_map.get(seq)
    if _UUID_RE.match(s):
        return s
    return None

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
    # budget covers: 1 initial + 1 `ask_user` → pause → 1 resume + a few
    # output_retries + headroom. 16 leaves comfortable margin (request_limit
    # counts CUMULATIVE requests across pause/resume — the rehydrated
    # message_history carries the prior count forward). Do NOT lower it.
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

    ``deps_type`` is :class:`PlannerDeps`. The decider owns the comprehension
    surface (dynamic instructions render ``case_brief`` / ``recent_messages`` /
    ``prior_searches`` / ``attached_items`` — with full attachment
    ``content_md`` — from deps). Its only tool is the deferred ``ask_user``,
    which raises ``CallDeferred`` to pause for a clarifying question.

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
        """Ask one clarifying question — or reflect your understanding back for
        confirmation — pausing the run until the user replies.

        Use it WHENEVER planning genuinely needs the user's input — whenever a
        sharper search or a truer reading of their situation depends on it.
        Don't hesitate when it serves the user. Common situations:
          1. The query names a corpus/domain but no concrete legal question, so
             no useful retrieval can be derived (e.g. «ابحث في القضايا البنكية»).
          2. A named entity OR person whose role/relation to the question you
             are *assuming* rather than being told — reflect your reading back
             for the user to correct, or ask. This covers: **any** named
             company / brand / app (what is it to them — opponent, lessor,
             seller, insurer, employer, platform?); a government body that
             isn't clearly identifiable or whose role here is unclear (unlike
             a well-known service such as ناجز whose role is obvious); a named
             individual whose relation is unclear; and plain who-is-suing-whom
             ambiguity. Skip only when the relation is stated, the body is
             well-known and unambiguous here, or the mention is incidental and
             doesn't affect the search.
          3. The message is long and bundles several distinct legal aspects
             such that there is risk you have misread the situation — reflect a
             brief restatement of your understanding and the aspects you will
             cover, and ask the user to confirm or correct before launching a
             full search.
        These are examples, not a closed list — any other point where planning
        needs user input to be more accurate is fair game. No need to ask about
        things you can confidently infer or that don't change the plan (e.g.
        tone).

        When raised, the run terminates with a ``DeferredToolRequests`` output.
        The caller resumes via ``agent.run(message_history=...,
        deferred_tool_results=DeferredToolResults({tool_call_id: user_reply}))``.

        Args:
            question: A single concise Arabic message — a clarifying question,
                or a brief restatement-for-confirmation of the user's situation.

        Returns:
            The user's reply text (delivered on resume via DeferredToolResults).
        """
        raise CallDeferred

    # Deterministic read tool: unfold a prior search / attached item into its
    # content_md + a used-only, [n]-keyed manifest of the named sources it
    # cites. Lets the decider anchor query_restatement / planner_brief on a
    # specific named regulation/ruling/service the user points at, instead of
    # re-running a generic search. PlannerDeps exposes
    # .supabase / .user_id / .wi_alias_map (satisfies HasWorkspaceContext).
    register_unfold_workspace_item(agent)

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

    @agent.output_validator
    def _resolve_referenced_wi(
        ctx: RunContext[PlannerDeps], value: PlannerResponse,
    ) -> PlannerResponse:
        """Resolve ``referenced_wi`` (alias) → ``referenced_item_id`` (UUID).

        Migration 052 / agent communication protocol: the LLM emits the
        ``WI-{n}`` alias in ``referenced_wi``; this validator looks it up in
        ``deps.wi_alias_map`` and fills ``referenced_item_id`` for downstream
        orchestrator consumers. An unknown alias raises ``ModelRetry`` with
        an Arabic error so the responder can self-correct.

        Defence-in-depth: if the LLM mistakenly fills ``referenced_item_id``
        directly (legacy schema bleed-through), the alias-derived value
        overwrites it so the orchestrator always sees the canonical UUID.
        """
        if value.referenced_wi:
            resolved = _resolve_wi_alias(value.referenced_wi, ctx.deps.wi_alias_map or {})
            if resolved is None:
                raise ModelRetry(
                    f"Item {value.referenced_wi} does not exist in this conversation. "
                    f"Use an alias from <prior_searches> (WI-1, WI-2, ...)."
                )
            value.referenced_item_id = resolved
        else:
            # No alias emitted -- ensure the UUID field is also cleared so
            # downstream code never sees a stale value.
            value.referenced_item_id = None
        return value

    return agent


__all__ = [
    "PLANNER_DECIDER_LIMITS",
    "PLANNER_RESPONDER_LIMITS",
    "create_planner_decider",
    "create_planner_responder",
]
