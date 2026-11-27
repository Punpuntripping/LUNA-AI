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

import json
import logging
import re
from typing import Any

from pydantic import ValidationError
from pydantic_ai import Agent, CallDeferred, DeferredToolRequests, ModelRetry, RunContext, TextOutput
from pydantic_ai.usage import UsageLimits

from agents.models import next_step_errors, salvage_next_steps
from agents.tool_repository.completion_search_tool import register_completion_search
from agents.tool_repository.fetch_article import register_fetch_article
from agents.tool_repository.unfold_workspace_item import register_unfold_workspace_item
from agents.utils.agent_models import ModelPolicy, get_agent_model
from agents.utils.usage_limits import OUTPUT_TOKENS_CAP

from .deps import PlannerDeps
from .models import PlannerDecision, PlannerResponse
from .prompts import (
    LONG_MESSAGE_REVIEW_RULE,
    MAX_REQUEST_QUOTE_CHARS,
    MAX_REQUEST_QUOTES,
    PLANNER_DECIDER_SYSTEM_PROMPT,
    PLANNER_RESPONDER_SYSTEM_PROMPT,
    build_decider_instructions,
    decider_system_prompt,
    build_responder_instructions,
    locate_request_quotes,
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
    output_tokens_limit=OUTPUT_TOKENS_CAP,  # shared runaway net — see agents/utils/usage_limits.py
    # request_limit counts CUMULATIVE requests across pause/resume — the
    # rehydrated message_history carries the prior request count forward. The
    # budget covers: 1 initial + 1 `ask_user` → pause → 1 resume + a few
    # output_retries + headroom. 16 leaves comfortable margin (request_limit
    # counts CUMULATIVE requests across pause/resume — the rehydrated
    # message_history carries the prior count forward). Do NOT lower it.
    request_limit=16,
)

PLANNER_RESPONDER_LIMITS = UsageLimits(
    output_tokens_limit=OUTPUT_TOKENS_CAP,  # shared runaway net — see agents/utils/usage_limits.py
    request_limit=4,
)

# Times a long message's decision is sent back before the turn goes ahead
# without the review. Below the decider's output_retries, which would fail the run.
_REVIEW_RETRIES = 2
# Times a decision with an unmatched ``request_quotes`` passage is sent back
# before the passage is dropped. Same reasoning as above.
_QUOTE_RETRIES = 2
# Times a «بحث شامل» plan that breaks its offer is sent back (one planner only).
_PLAN_RETRIES = 3
# The one planner's per-response output limit: Wafer's maximum, as the extended
# planner ran (a plan with its cheaper option is long). The run still stays under
# the shared OUTPUT_TOKENS_CAP — never a per-agent cap.
ONE_PLANNER_MAX_TOKENS = 65_536

_ONE_PLANNER_SALVAGE_RETRY = (
    "Answer with a tool call only: `ask_user` to ask the user, or the final output as a "
    "single valid JSON object conforming to its schema — no text outside it. All prose "
    "values in Arabic."
)


def _is_extended_plan(value: Any) -> bool:
    """The one planner's «بحث شامل» output (lazy import: today's decider never loads it)."""
    try:
        from agents.deep_search_v4.extended.models import ExtendedPlan
    except Exception:  # noqa: BLE001
        return False
    return isinstance(value, ExtendedPlan)


def _decision_of_single_plan(plan: Any) -> PlannerDecision:
    """A one-search «بحث شامل» plan, run as the one search it is (the decision's mode;
    the request's focus goes nowhere — the search reads the file itself)."""
    req = plan.requests[0]
    return PlannerDecision(
        mode=req.mode,
        support=False,
        rationale=(plan.rationale or req.why or "one_planner: one-search plan")[:600],
        request_quotes=list(getattr(plan, "request_quotes", []) or []),
    )


def _one_planner_salvager(offer_on: bool):
    """``TextOutput`` coercer for a plain-text JSON answer: a «بحث شامل» plan when the
    object has ``requests`` (and the offer is on), else a :class:`PlannerDecision`."""
    from agents.utils.structured_output import _extract_json_object

    def _coerce(text: str) -> Any:
        blob = _extract_json_object(text)
        if blob is None:
            raise ModelRetry(_ONE_PLANNER_SALVAGE_RETRY)
        try:
            data = json.loads(blob)
        except json.JSONDecodeError as exc:
            raise ModelRetry(f"{_ONE_PLANNER_SALVAGE_RETRY} (JSON parse error: {exc})") from exc
        if not isinstance(data, dict):
            raise ModelRetry(_ONE_PLANNER_SALVAGE_RETRY)
        if offer_on and "requests" in data:
            from agents.deep_search_v4.extended.models import ExtendedSearchPlan as cls
        else:
            cls = PlannerDecision
        try:
            out = cls.model_validate(data)
        except ValidationError as exc:
            raise ModelRetry(f"{_ONE_PLANNER_SALVAGE_RETRY} ({exc.error_count()} schema error(s))") from exc
        logger.info("one_planner: salvaged %s from plain-text emission", cls.__name__)
        return out

    return _coerce


def create_planner_decider(
    model_override: ModelPolicy | str | None = None,
    *,
    completion_search: bool = False,
    one_planner: bool = False,
    extended: Any = None,
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

    ``one_planner`` (ONE_PLANNER_ENABLED, .claude/plans/one_planner.md): the same
    agent on V4.1 (slot ``deep_planner``), the ONLY deep-search planner. With
    ``extended`` (an ``extended.offer.ExtendedOffer``) it is also told about «بحث
    شامل», and when the offer is available it may end the run in an
    :class:`~agents.deep_search_v4.extended.models.ExtendedSearchPlan` — two or more
    searches, shown to the user as a plan card. Both off → today's decider, exactly.
    """
    model = get_agent_model("deep_planner" if one_planner else "planner_decider", model_override)

    # Completion Search (completion_search.md §4.4): the prompt and the tool change
    # TOGETHER. If the prompt cannot be built, this decider keeps `fetch_article`
    # with the prompt that describes it — never one without the other.
    system_prompt = PLANNER_DECIDER_SYSTEM_PROMPT
    if completion_search:
        try:
            system_prompt = decider_system_prompt(True)
        except Exception:  # noqa: BLE001
            logger.error("planner: complete_search prompt could not be built — keeping fetch_article", exc_info=True)
            completion_search = False

    # The one planner (ONE_PLANNER_ENABLED): «بحث شامل» is an output only when the
    # turn's offer is available; V4.1 sometimes finalises in plain text, so it gets
    # the JSON salvager every V4.1 structured agent carries.
    offer_on = bool(one_planner and extended is not None and getattr(extended, "available", False))
    one_planner_kw: dict[str, Any] = {}
    if one_planner:
        from agents.deep_search_v4.extended.models import ExtendedSearchPlan

        finals: list[Any] = [PlannerDecision, ExtendedSearchPlan] if offer_on else [PlannerDecision]
        output_type: list[Any] = [
            *finals, DeferredToolRequests, TextOutput(_one_planner_salvager(offer_on)),
        ]
        one_planner_kw["model_settings"] = {"max_tokens": ONE_PLANNER_MAX_TOKENS}
    else:
        output_type = [PlannerDecision, DeferredToolRequests]

    agent: Agent[PlannerDeps, PlannerDecision | DeferredToolRequests] = Agent(
        model,
        name="planner_decider",
        deps_type=PlannerDeps,
        output_type=output_type,
        instructions=system_prompt,
        retries=2,
        output_retries=4,
        **one_planner_kw,
    )

    @agent.instructions
    def _comprehension(ctx: RunContext[PlannerDeps]) -> str:
        """Inject the per-turn comprehension blocks (case_brief / messages / …)."""
        return build_decider_instructions(ctx.deps)

    @agent.instructions
    def _long_message_review(ctx: RunContext[PlannerDeps]) -> str:
        """Require the understanding review on a long message."""
        return LONG_MESSAGE_REVIEW_RULE if ctx.deps.review_required else ""

    if one_planner and extended is not None:
        @agent.instructions
        def _extended_offer(ctx: RunContext[PlannerDeps]) -> str:
            """«بحث شامل» this turn — available (rules + encoded data) or not (the reason
            and "say so, offer «بحث عميق»"). Rendered once, when the offer was prepared."""
            return str(getattr(extended, "instructions", "") or "")

    @agent.output_validator
    def _hold_decision_for_review(
        ctx: RunContext[PlannerDeps],
        value: PlannerDecision | DeferredToolRequests,
    ) -> PlannerDecision | DeferredToolRequests:
        """Send a long message's decision back until the model asks the user.

        Only a decision reaches this validator: pydantic-ai ends a deferred
        ``ask_user`` call without running output validators. The review is worth
        a retry, never the turn, so after ``_REVIEW_RETRIES`` the decision stands.
        """
        if not ctx.deps.review_required or _is_extended_plan(value):
            # The one planner's «بحث شامل» plan is shown as a card the user approves —
            # the card is the review (.claude/plans/one_planner.md C1).
            return value
        if ctx.retry < _REVIEW_RETRIES:
            raise ModelRetry(
                "This message is long, so a decision is not accepted yet. Call "
                "`ask_user` with a review of your understanding, as the section "
                "«Required this turn» says."
            )
        logger.warning(
            "planner_decider: decided without the long-message review after "
            "%d retries", _REVIEW_RETRIES,
        )
        return value

    @agent.output_validator
    def _check_request_quotes(
        ctx: RunContext[PlannerDeps],
        value: PlannerDecision | DeferredToolRequests,
    ) -> PlannerDecision | DeferredToolRequests:
        """Every ``request_quotes`` passage must exist, word for word, earlier on.

        The search runs on these passages, so a passage the model wrote itself
        would be exactly the rewording the owner forbade (2026-11-05). Sent back
        while retries last; after that the unmatched passages are dropped and the
        decision stands — a quote is never worth the turn.
        """
        if not (isinstance(value, PlannerDecision) or _is_extended_plan(value)) or not value.request_quotes:
            return value
        quotes = [q for q in value.request_quotes if str(q or "").strip()]
        _found, missing = locate_request_quotes(quotes, ctx.deps.recent_messages)
        total = sum(len(str(q)) for q in quotes)
        too_many = len(quotes) > MAX_REQUEST_QUOTES or total > MAX_REQUEST_QUOTE_CHARS
        if (missing or too_many) and ctx.retry < _QUOTE_RETRIES:
            problems: list[str] = []
            if missing:
                problems.append(
                    "These `request_quotes` passages are not in any message before "
                    "the latest one, character for character: "
                    + " | ".join(f"«{str(q)[:120]}»" for q in missing)
                    + ". Copy each passage exactly as it appears in <recent_messages>, "
                    "or drop it."
                )
            if too_many:
                problems.append(
                    f"Keep `request_quotes` to at most {MAX_REQUEST_QUOTES} passages "
                    f"and {MAX_REQUEST_QUOTE_CHARS} characters: only the request and "
                    "the question or proposal the latest message answers."
                )
            raise ModelRetry(" ".join(problems))
        kept = [q for q in quotes if q not in missing][:MAX_REQUEST_QUOTES]
        if missing or kept != quotes:
            logger.warning(
                "planner_decider: dropped %d unmatched / surplus request_quotes",
                len(quotes) - len(kept),
            )
        value.request_quotes = kept
        return value

    if offer_on:
        @agent.output_validator
        def _check_extended_plan(
            ctx: RunContext[PlannerDeps],
            value: PlannerDecision | DeferredToolRequests,
        ) -> PlannerDecision | DeferredToolRequests:
            """A «بحث شامل» plan must fit the offer: two or more searches, the budget
            mode's limits, existing angle ids / section refs, the workspace's room.
            Sent back while retries last. A one-search plan that survives them runs as
            the one search it is; any other broken plan fails the run (the runner
            falls back to its default decision — an ordinary search)."""
            if not _is_extended_plan(value):
                return value
            from agents.deep_search_v4.extended.offer import offer_plan_problems
            from agents.deep_search_v4.extended.prompts import RETRY_PREFIX

            bad = offer_plan_problems(value, extended)
            if not bad:
                return value
            if ctx.retry < _PLAN_RETRIES:
                raise ModelRetry(RETRY_PREFIX + "; ".join(bad))
            if len(value.requests) == 1:
                logger.warning("one_planner: a one-search plan after %d retries — run as one search", _PLAN_RETRIES)
                return _decision_of_single_plan(value)
            raise ModelRetry(RETRY_PREFIX + "; ".join(bad))

    @agent.tool_plain
    async def ask_user(question: str) -> str:  # noqa: RUF029
        """Ask one clarifying question — or reflect your understanding back for
        confirmation — pausing the run until the user replies.

        Use it WHENEVER planning genuinely needs the user's input — whenever a
        sharper search or a truer reading of their situation depends on it.
        Don't hesitate when it serves the user. Common situations (a whole
        field or theme with no question in it — «ابحث في القضايا البنكية», an
        article "about" a subject — is NOT one: that is a hand-back, ``aborted``):
          1. (moved to the hand-back.)
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
          3. The user narrates a situation at length rather than asking one
             clear question, so you are the one piecing together what happened
             and what they want — reflect a brief restatement of your
             understanding and what you will cover, and ask the user to confirm
             or correct before launching a full search. Clear parties do not
             settle this, and neither does the aspect count that picks the mode.
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
    # cites. Lets the decider anchor planner_brief on a
    # specific named regulation/ruling/service the user points at, instead of
    # re-running a generic search. PlannerDeps exposes
    # .supabase / .user_id / .wi_alias_map (satisfies HasWorkspaceContext).
    register_unfold_workspace_item(agent)

    # Deterministic read tool: fetch the verbatim text of a specific article
    # (by article number) from a specific named regulation, straight from
    # articles_v2.content. Lets the decider fold the exact article the user
    # cites into planner_brief so it flows to the executors and aggregator as
    # TEXT (never a citation), while the normal search still supplies the
    # answer's corpus sources. Ambiguous regulation titles come back as an
    # "AMBIGUOUS:" string → resolve via ask_user.
    # With Completion Search on, the helper replaces it: plain-words requests, five
    # kinds of text, a confidence level on each result (`complete_search` /
    # `keep_results`, agents/tool_repository/completion_search_tool.py).
    if completion_search:
        register_completion_search(agent, "deep_planner")
    else:
        register_fetch_article(agent)

    return agent


def create_planner_responder(
    model_override: ModelPolicy | str | None = None,
) -> Agent[PlannerDeps, PlannerResponse]:
    """Build the phase-3 responder agent.

    Emits a :class:`PlannerResponse` (chat summary + next_steps chips). A dynamic
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

    @agent.output_validator
    def _validate_next_steps(
        ctx: RunContext[PlannerDeps], value: PlannerResponse,
    ) -> PlannerResponse:
        """Police ``next_steps`` (plan: next_step_suggestions.md §3.2).

        ≤ 3 chips, one per kind, no ``open`` (deep_search references are
        already visible in the card), label/prompt within their caps. The FIRST
        attempt gets one ``ModelRetry`` with the concrete errors; any later
        attempt is salvaged in place (:func:`salvage_next_steps`) — chips are
        decoration and must never cost the turn its answer.
        """
        errors = next_step_errors(value.next_steps, allow_open=False)
        if not errors:
            return value
        if ctx.retry == 0:
            raise ModelRetry(
                "Fix `next_steps`: " + "; ".join(errors)
                + ". Emit at most 3 chips, each a different kind "
                "(narrow_search / draft / apply), or an empty list."
            )
        logger.warning(
            "planner_responder: salvaging next_steps after retry (%s)",
            "; ".join(errors),
        )
        value.next_steps = salvage_next_steps(value.next_steps, allow_open=False)
        value._next_steps_salvaged = True
        return value

    return agent


__all__ = [
    "PLANNER_DECIDER_LIMITS",
    "PLANNER_RESPONDER_LIMITS",
    "create_planner_decider",
    "create_planner_responder",
]
