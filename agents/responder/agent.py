"""Agent factory for the unified responder (plan unified_responder.md §4.1).

One Agent per mode, each with its OWN ``output_type`` (``DeepSearchResponse`` /
``SimpleSearchResponse`` / ``WritingResponse``) so a mode agent only ever sees
its own schema — a smaller tool schema and a stable cached prefix.

Layer 2 (user-facing voice) · model slot ``responder`` = ``_FLASH`` (tier_2).

* Static instructions: ``SYSTEM_PROMPTS[mode]`` — byte-identical across turns.
* Dynamic instruction: the router-context block pre-rendered by the runner into
  ``ResponderDeps.context_block`` (``context.py``).
* User message: the turn package (``prompts.build_turn_message``).

Structured output opts into the shared salvager
(``agents/utils/structured_output.py``) with a retry message naming THIS mode's
fields — flash models sometimes finalise as ``<thinking>…</thinking>{json}``
text instead of calling the output tool.

Built fresh per call (like ``create_planner_responder`` /
``simple_search.responder.create_responder_agent``): the test harness patches
``get_agent_model`` in this module, and a cached instance would bind a live
model at first use. Prompt caching depends on the prompt BYTES, which are
module-level constants — not on the Agent object.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import Agent, ModelRetry, RunContext, TextOutput
from pydantic_ai.usage import UsageLimits

from agents.models import (
    MAX_ANSWER_STEPS,
    MAX_NEXT_STEPS,
    NEXT_STEP_LABEL_MAX,
    NEXT_STEP_PROMPT_MAX,
    next_step_errors,
    salvage_next_steps,
)
from agents.utils.agent_models import ModelPolicy, get_agent_model
from agents.utils.structured_output import make_json_salvager
from agents.utils.usage_limits import OUTPUT_TOKENS_CAP

from .models import (
    FOLLOW_UP_QUESTION_MAX,
    OUTPUT_TYPES,
    RESPONDER_MODES,
    DeepSearchResponse,
    ResponderBase,
    ResponderMode,
    SimpleSearchResponse,
)
from .prompts import SYSTEM_PROMPTS

logger = logging.getLogger(__name__)

#: ``agents/utils/agent_models.py`` slot — also the ``llm_calls`` agent label.
RESPONDER_SLOT = "responder"

RESPONDER_LIMITS = UsageLimits(
    output_tokens_limit=OUTPUT_TOKENS_CAP,  # shared runaway net — see agents/utils/usage_limits.py
    # No tools — 1 initial request + the 3 `output_retries` below + headroom.
    # A request_limit that bites before output_retries are spent turns a
    # self-correctable label/alias error into a run failure.
    request_limit=5,
)

# ── WI alias resolver (deep_search referenced_wi) ────────────────────────────
_WI_ALIAS_RE = re.compile(r"^WI-(\d+)$", re.IGNORECASE)
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _resolve_wi_alias(alias: str, alias_map: dict[int, str]) -> str | None:
    """``"WI-{seq}"`` → item_id (raw UUID accepted verbatim); ``None`` if unknown."""
    if not alias:
        return None
    s = alias.strip()
    m = _WI_ALIAS_RE.match(s)
    if m:
        return alias_map.get(int(m.group(1)))
    if _UUID_RE.match(s):
        return s
    return None


@dataclass
class ResponderDeps:
    """Per-run deps. No tools, no DB — everything the model sees is rendered.

    ``conversation_id`` / ``case_id`` stamp the tracking span
    (``tracking._identity_from_deps``). ``tracking_input`` keeps the context
    text itself (user content) out of span attributes.
    """

    mode: ResponderMode
    conversation_id: str = ""
    case_id: str | None = None
    context_block: str = ""
    wi_alias_map: dict[int, str] = field(default_factory=dict)
    #: simple_search: the D1..Dn labels rendered into <documents> — the
    #: allow-list ``cards`` is validated against.
    doc_labels: tuple[str, ...] = ()
    #: simple_search pause leg — chips and the question are forced empty.
    suppress_next_steps: bool = False

    def tracking_input(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "context_chars": len(self.context_block or ""),
            "doc_labels": list(self.doc_labels),
            "suppress_next_steps": self.suppress_next_steps,
        }


_FIELDS: dict[ResponderMode, str] = {
    "deep_search": (
        "chat_summary_md, next_steps, follow_up_question, question_basis, "
        "build_artifact, referenced_wi"
    ),
    "simple_search": (
        "chat_summary_md, next_steps, follow_up_question, question_basis, cards"
    ),
    "writing": "chat_summary_md, next_steps, follow_up_question, question_basis",
}


def _retry_msg(mode: ResponderMode) -> str:
    """Salvager retry message naming THIS mode's schema fields only."""
    extra = ""
    if mode == "simple_search":
        extra = (
            ' Each entry of `cards` is {"doc": "D1", "card": true, "title": "..."}, '
            "where `doc` is one of the labels shown in <documents>."
        )
    kinds = (
        "open|narrow_search|apply|draft|answer"
        if mode == "simple_search"
        else "narrow_search|apply|draft|answer"
    )
    return (
        "Return the output as a single valid JSON object conforming to the schema "
        f"({_FIELDS[mode]}) only — with no text and no <thinking> tag outside the "
        "JSON. Each entry of `next_steps` is "
        f'{{"kind": "{kinds}", "label": "...", "prompt": "..."}}.'
        f"{extra} Every user-facing string must be in Arabic."
    )


def _strip_question_from_summary(value: ResponderBase) -> None:
    """D6: the question never lives inside ``chat_summary_md`` — remove a copy."""
    q = (value.follow_up_question or "").strip()
    if not q or q not in (value.chat_summary_md or ""):
        return
    value.chat_summary_md = "\n".join(
        line for line in value.chat_summary_md.replace(q, "").splitlines()
    ).strip()


def _drop_question(value: ResponderBase, reason: str) -> None:
    """Clear the question, its basis and every answer chip (they belong to it)."""
    if value.follow_up_question or any(s.kind == "answer" for s in value.next_steps):
        logger.info("responder: dropping follow-up question (%s)", reason)
    value.follow_up_question = None
    value.question_basis = None
    value.next_steps = [s for s in value.next_steps if s.kind != "answer"]


def create_responder(
    mode: ResponderMode,
    model_override: ModelPolicy | str | None = None,
) -> Agent[ResponderDeps, Any]:
    """Build the responder agent for ``mode``.

    Args:
        mode: ``"deep_search"`` / ``"simple_search"`` / ``"writing"``.
        model_override: optional tier override token / :class:`ModelPolicy`
            for the ``responder`` slot (the tier stays fixed).
    """
    if mode not in RESPONDER_MODES:
        raise ValueError(f"unknown responder mode {mode!r}")
    out_cls = OUTPUT_TYPES[mode]
    allow_open = mode == "simple_search"

    agent: Agent[ResponderDeps, Any] = Agent(
        get_agent_model(RESPONDER_SLOT, model_override),
        name=RESPONDER_SLOT,
        deps_type=ResponderDeps,
        output_type=[
            out_cls,
            TextOutput(make_json_salvager(out_cls, retry_msg=_retry_msg(mode))),
        ],
        instructions=SYSTEM_PROMPTS[mode],
        retries=2,
        output_retries=3,
    )

    @agent.instructions
    def _context(ctx: RunContext[ResponderDeps]) -> str:
        """The router-context block (or the headless fallback) for this turn."""
        return ctx.deps.context_block or ""

    @agent.output_validator
    def _validate_question(ctx: RunContext[ResponderDeps], value: Any) -> Any:
        """Police ``follow_up_question`` (§6.1 / D6).

        * Over-long → one ``ModelRetry``, then the question is dropped (a
          truncated question is worse than none).
        * A copy inside ``chat_summary_md`` is removed in place (no retry).
        * Pause leg (``suppress_next_steps``) → question forced empty.
        * deep_search with ``build_artifact=false`` → no question (§6.1.7).
        """
        if not isinstance(value, ResponderBase):
            return value
        if ctx.deps.suppress_next_steps:
            _drop_question(value, "pause leg")
            value.next_steps = []
            return value
        if isinstance(value, DeepSearchResponse) and not value.build_artifact:
            _drop_question(value, "build_artifact=false")
        q = (value.follow_up_question or "").strip()
        if not q:
            value.follow_up_question = None
            value.question_basis = None
            return value
        value.follow_up_question = q
        if len(q) > FOLLOW_UP_QUESTION_MAX:
            if ctx.retry < 1:
                raise ModelRetry(
                    f"`follow_up_question` is {len(q)} characters; the maximum is "
                    f"{FOLLOW_UP_QUESTION_MAX}. Ask ONE short question with its "
                    "reason in the same sentence, or set it to null."
                )
            _drop_question(value, "over-long after retry")
            return value
        _strip_question_from_summary(value)
        return value

    @agent.output_validator
    def _validate_next_steps(ctx: RunContext[ResponderDeps], value: Any) -> Any:
        """Police ``next_steps`` (§6.3): ≤3 total; with a question ≤2 ``answer``
        + ≤1 action; without one no ``answer`` and one chip per kind; ``open``
        only in simple_search. One ``ModelRetry`` on the first bad output, then
        salvage — chips must never cost the turn its answer.
        """
        if not isinstance(value, ResponderBase):
            return value
        has_question = bool(value.follow_up_question)
        errors = next_step_errors(
            value.next_steps, allow_open=allow_open, allow_answer=has_question,
        )
        if not has_question and any(s.kind == "answer" for s in value.next_steps):
            errors.append("'answer' chips need a follow_up_question")
        if not errors:
            return value
        if ctx.retry < 1:
            raise ModelRetry(
                "Fix `next_steps`: " + "; ".join(dict.fromkeys(errors))
                + f". At most {MAX_NEXT_STEPS} chips. With a follow_up_question: up "
                f"to {MAX_ANSWER_STEPS} `answer` chips + at most 1 action. Without "
                "one: no `answer`, each chip a different kind. Labels ≤ "
                f"{NEXT_STEP_LABEL_MAX} chars, prompts ≤ {NEXT_STEP_PROMPT_MAX} chars."
            )
        logger.warning(
            "responder(%s): salvaging next_steps after retry (%s)",
            mode, "; ".join(errors),
        )
        value.next_steps = salvage_next_steps(
            value.next_steps, allow_open=allow_open, allow_answer=has_question,
        )
        value._next_steps_salvaged = True
        return value

    if mode == "deep_search":

        @agent.output_validator
        def _resolve_referenced_wi(ctx: RunContext[ResponderDeps], value: Any) -> Any:
            """``referenced_wi`` (alias) → ``referenced_item_id`` (UUID).

            Ported from ``planner/agent.py`` ``_resolve_referenced_wi``: an
            unknown alias is a ``ModelRetry`` (the model can fix it in one
            retry; code can only paper over it).
            """
            if not isinstance(value, DeepSearchResponse):
                return value
            if value.referenced_wi:
                resolved = _resolve_wi_alias(value.referenced_wi, ctx.deps.wi_alias_map or {})
                if resolved is None:
                    raise ModelRetry(
                        f"Item {value.referenced_wi} does not exist in this conversation. "
                        "Use an alias from <prior_searches> (WI-1, WI-2, ...)."
                    )
                value.referenced_item_id = resolved
            else:
                value.referenced_item_id = None
            return value

    if mode == "simple_search":

        @agent.output_validator
        def _validate_cards(ctx: RunContext[ResponderDeps], value: Any) -> Any:
            """Police ``cards`` against the dispatched labels (ported from
            ``simple_search/responder.py`` ``_validate_cards``): unknown label →
            Arabic ``ModelRetry``; missing verdict → fine (no card); duplicates
            → first wins; ``card=False`` forces ``title=""``.
            """
            if not isinstance(value, SimpleSearchResponse):
                return value
            allowed = tuple(
                str(lbl).strip() for lbl in (ctx.deps.doc_labels or ()) if str(lbl).strip()
            )
            if not allowed:
                if value.cards:
                    logger.warning(
                        "responder(simple_search): %d card verdict(s) with an empty "
                        "doc_labels allow-list — dropping them", len(value.cards),
                    )
                value.cards = []
                return value
            canonical = {lbl.upper(): lbl for lbl in allowed}
            seen: set[str] = set()
            kept = []
            for verdict in value.cards:
                label = canonical.get(str(verdict.doc or "").strip().upper())
                if label is None:
                    raise ModelRetry(
                        f"لا يوجد مستند بالرمز «{verdict.doc}» في هذه الجولة. "
                        f"استخدم أحد هذه الرموز فقط: {'، '.join(allowed)}."
                    )
                if label in seen:
                    continue
                seen.add(label)
                verdict.doc = label
                verdict.title = verdict.title.strip() if verdict.card else ""
                kept.append(verdict)
            value.cards = kept
            return value

    return agent


__all__ = [
    "RESPONDER_SLOT",
    "RESPONDER_LIMITS",
    "ResponderDeps",
    "create_responder",
]
