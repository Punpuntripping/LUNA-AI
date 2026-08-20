"""The ``simple_search`` responder — the turn's voice, and the publish gate.

Plan ``.claude/plans/simple_search_responder.md``. One agent, one call per
lookup turn, run from ``_finalise`` (``runner.py:1273``) after every synthesizer
has settled. It exists because the family had **nobody who owned the turn**: up
to three Layer-3 synthesizers ran concurrently and blind to each other, each
writing a document body that was then used as the chat bubble *and* as the
card's ``content_md`` — so a carded lookup showed the user the same document
twice (§1.1) — and each deciding, alone, whether its own answer deserved a card
it could not see the other two competing for (§1.4).

This agent takes both of those jobs, and only those:

1. **`chat_summary_md`** — the Arabic message at the top of the bubble. It is a
   lead-in, not an answer: carded bodies live on their cards, and uncarded
   bodies are pasted verbatim below it **by code** (§9). D4 is the reason the
   split is drawn there — a flash model paraphrasing a provision is precisely
   what ``_SHARED_ROLE`` forbids the synthesizers to do, so the responder never
   retypes legal text either.
2. **`cards`** — one verdict per dispatched document, and the publish is
   **gated** on the return, exactly as ``should_publish`` gates
   ``publish_search_result`` at ``orchestrator.py:3063``. Nothing is written to
   ``workspace_items`` before this agent answers (trap §11.5).

Model: the ``simple_search_responder`` slot — ``tier_2`` / ``_FLASH``, the same
policy as its deep_search counterpart ``planner_responder``. Not
``_FLASH_MEDIUM`` like the synthesizers: those reason about whether a retrieved
object is the one the user meant, over a full document; this one holds bounded
digests and writes two short Arabic strings. Cost of the whole change is +1
flash call on a turn that already runs a searcher plus up to three synthesizers
(§10).

**D7 — failure publishes NOTHING.** deep_search's ``_response_from_artifact``
(``planner/runner.py:140``) falls back to publishing on responder failure,
because there the artifact IS the product. Here the bubble carries the text, so
the safe direction inverts: if this agent raises, the runner ships every body in
full and the turn simply leaves no card. A missing card is recoverable by asking
again; a wrongly-published one is permanent clutter in a 15-item-capped
workspace.

Structured output opts into the shared salvager
(``agents/utils/structured_output.py:123-159``) for the same measured reason as
the synthesizer: flash models sometimes finalise as ``<thinking>…</thinking>{json}``
text instead of calling the output tool. The retry message names **this**
schema's fields — copying the synthesizer's ``(synthesis_md, used_refs,
rejected, rejection_reason)`` would tell the model to emit four fields this
schema does not have and omit all three it does.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext, TextOutput
from pydantic_ai.usage import UsageLimits

from agents.simple_search.prompts import (
    RESPONDER_EXCERPT_HARD_CAP,
    SIMPLE_SEARCH_RESPONDER_PROMPT,
)
from agents.utils.agent_models import ModelPolicy, get_agent_model
from agents.utils.structured_output import make_json_salvager

logger = logging.getLogger(__name__)

#: ``agents/utils/agent_models.py`` slot for this agent.
RESPONDER_SLOT = "simple_search_responder"

#: Per-document excerpt budget for the digest the runner builds (§6).
#:
#: Mirrors the deep_search responder's ``_SYNTHESIS_DIGEST_CHARS``
#: (``deep_search_v4/planner/prompts.py:277``) — the same agent shape reaching
#: the same conclusion about how much body a responder needs, which is: enough
#: to frame an answer and judge whether it deserves a card, never enough to
#: retype it. The runner clips ``ResponderDocDigest.excerpt`` to this;
#: ``build_responder_user_message`` clips again at render as the last line of
#: defence (trap §11.8).
RESPONDER_EXCERPT_CHARS = 1600

# Two literals, one value, checked at import — the cheap-invariant pattern
# already used for ``SYNTHESIZER_PROMPTS`` vs ``SIMPLE_SEARCH_LEVELS``
# (``prompts.py``). The public constant lives here because the budget is the
# responder's business; the clip lives in ``prompts.py`` because that is where
# the tokens are actually spent, and ``prompts.py`` cannot import this module
# (this module imports it). Drift between them would silently double the
# family's per-turn prompt cost, which no test would notice.
assert RESPONDER_EXCERPT_CHARS == RESPONDER_EXCERPT_HARD_CAP, (
    "RESPONDER_EXCERPT_CHARS and prompts.RESPONDER_EXCERPT_HARD_CAP must agree; "
    f"got {RESPONDER_EXCERPT_CHARS} vs {RESPONDER_EXCERPT_HARD_CAP}"
)

#: Workspace-item titles are capped at 80 chars upstream (``workspace_items.title``
#: is sized for the router's ``task_label``). Enforced on the field, exactly as
#: the synthesizer enforced it before the decision moved here: an over-long
#: title is a validation retry rather than a truncated card.
WI_TITLE_MAX = 80


@dataclass
class ResponderDeps:
    """Scope identifiers, plus the label allow-list the output validator polices.

    ``conversation_id`` / ``case_id`` are what ``tracking._identity_from_deps``
    reads off a deps object to stamp the span — the same two fields
    :class:`~agents.simple_search.synthesizer.SynthesizerDeps` carries, and for
    the same reason. This agent has no tools and reads no DB either; everything
    it sees arrives in the user message.

    ``doc_labels`` is the exception, and it is here rather than in the prompt
    because it is not context — it is a **contract**. It holds the ``D1..Dn``
    labels dispatched this turn, and :func:`create_responder_agent`'s output
    validator retries any ``CardVerdict.doc`` outside it. That mirrors
    ``deps.wi_alias_map`` on the deep_search planner, which
    ``_resolve_referenced_wi`` (``planner/agent.py:232-260``) validates the same
    way and for the same reason: a silent ``None`` was explicitly rejected
    there, because a card verdict naming a document that does not exist is a
    wiring error the model can fix in one retry and code can only paper over.

    Pass the SAME labels rendered into ``<documents>`` by
    :func:`~agents.simple_search.prompts.build_responder_user_message`. A tuple,
    not a list: it is a snapshot of a decision already made, and
    ``tracking._bounded_snapshot`` serialises deps onto the span.
    """

    conversation_id: str = ""
    case_id: str | None = None
    doc_labels: tuple[str, ...] = ()


class CardVerdict(BaseModel):
    """One document's card decision.

    ``doc`` is the runner-assigned label, never an id. The whole point of the
    ``D1..Dn`` indirection — same discipline as the router's ``WI-{n}`` aliases
    — is that a model asked to echo a ``document_key`` or a UUID will eventually
    echo a plausible wrong one, and a wrong id publishes the wrong card.
    """

    doc: str = Field(
        description=(
            "The document's label exactly as it appears in <documents>: D1, D2, "
            "D3. Never a UUID, a title, or any other identifier."
        ),
    )
    card: bool = Field(
        description=(
            "Whether this answer deserves a durable workspace card. Default "
            "true; false for one-line answers, pointers, not-founds, bodies too "
            "truncated to stand as a document, and anything already delivered "
            "earlier in this turn."
        ),
    )
    title: str = Field(
        default="",
        max_length=WI_TITLE_MAX,
        description=(
            "Short Arabic content-derived card title naming the object "
            f"(≤{WI_TITLE_MAX} chars, no verbs). Empty when card is false."
        ),
    )


class ResponderOutput(BaseModel):
    """What the responder returns for the whole turn — one object, not one per doc.

    Deliberately close to ``PlannerResponse`` (``planner/models.py:246-278``)
    where the shape is load bearing — ``chat_summary_md`` + ``suggestion_md`` —
    and deliberately different where deep_search's fields carry single-artifact
    semantics a fan-out has no concept of: ``build_artifact`` is one boolean for
    one aggregated artifact, and ``referenced_wi`` points at one prior card.
    A lookup turn can produce three documents whose card verdicts differ, so the
    gate is a **list** keyed by label.
    """

    chat_summary_md: str = Field(
        description=(
            "The Arabic message the user reads at the top of the reply. A "
            "lead-in that frames what was opened — never a restatement of the "
            "documents themselves, and never any [n] citation marker."
        ),
    )
    suggestion_md: str = Field(
        default="",
        description=(
            "ONE next step in Arabic, offering tone, grounded in the objects "
            "this turn considered and did not open. Empty is valid and frequent."
        ),
    )
    cards: list[CardVerdict] = Field(
        default_factory=list,
        description=(
            "One verdict per document label shown in <documents>. A label with "
            "no verdict defaults to no card."
        ),
    )

    def verdict_for(self, doc: str) -> CardVerdict | None:
        """This document's verdict, or ``None`` when the model did not rule on it.

        ``None`` is a normal, non-exceptional answer and the caller must treat
        it as ``card=False`` (§6): a missing verdict is a model omission, and
        the safe default under D7 is to leave no card rather than to publish an
        untitled one. It is NOT the same as an unknown label — that one never
        reaches here, because the output validator turns it into a retry.

        Matching is case- and whitespace-insensitive as defence in depth; the
        validator has already canonicalised every ``doc`` it kept.
        """
        key = str(doc or "").strip().upper()
        if not key:
            return None
        for verdict in self.cards:
            if str(verdict.doc or "").strip().upper() == key:
                return verdict
        return None


# The retry message enumerates THIS schema's fields. The synthesizer's message
# names ``rejected``/``rejection_reason``/``used_refs`` — sending that here would
# misdirect the retry toward a schema that does not exist (trap §11.1 is the same
# trap in reverse: a stale field list teaches the model to emit fields nobody
# reads and omit the ones that gate the publish).
_RESPONDER_RETRY_MSG = (
    "Return the output as a single valid JSON object conforming to the schema "
    "(chat_summary_md, suggestion_md, cards) only — with no text and no "
    "<thinking> tag outside the JSON. Each entry of `cards` is "
    '{"doc": "D1", "card": true, "title": "..."}, where `doc` is one of the '
    "labels shown in <documents>. `chat_summary_md`, `suggestion_md` and every "
    "`title` must be in Arabic."
)


def _responder_text_output() -> TextOutput:
    """``TextOutput`` salvage member for the responder's ``output_type`` union."""
    return TextOutput(make_json_salvager(ResponderOutput, retry_msg=_RESPONDER_RETRY_MSG))


RESPONDER_LIMITS = UsageLimits(
    # Far tighter than the synthesizer's 24k: this agent's entire output is two
    # short Arabic strings plus N three-field verdicts (~200-500 tokens
    # observed-equivalent on the deep_search responder). 16k is ~30x that, sized
    # for the reasoning tokens that count against `output_tokens` on DashScope
    # rather than for the text.
    output_tokens_limit=16_000,
    # No tools — 1 initial request + the 3 `output_retries` below. An unknown
    # `doc` label costs one of those, so the budget must cover them all: a
    # request_limit that bites before output_retries are spent turns a
    # self-correctable label error into a run failure, which under D7 costs the
    # whole turn its cards.
    request_limit=4,
)


def create_responder_agent(
    model_override: ModelPolicy | str | None = None,
) -> Agent[ResponderDeps, ResponderOutput]:
    """Build the responder for one turn.

    Args:
        model_override: optional tier override token / :class:`ModelPolicy` for
            the ``simple_search_responder`` slot (the tier stays fixed).

    Returns:
        A fresh agent. Built per turn like every other agent in this family —
        it is a single stateless call and there is nothing to carry between
        turns.
    """
    agent: Agent[ResponderDeps, ResponderOutput] = Agent(
        get_agent_model(RESPONDER_SLOT, model_override),
        name="simple_search_responder",
        deps_type=ResponderDeps,
        output_type=[ResponderOutput, _responder_text_output()],
        instructions=SIMPLE_SEARCH_RESPONDER_PROMPT,
        retries=2,
        output_retries=3,
    )

    @agent.output_validator
    def _validate_cards(
        ctx: RunContext[ResponderDeps], value: ResponderOutput,
    ) -> ResponderOutput:
        """Police ``cards`` against the labels actually dispatched this turn.

        Copied from ``_resolve_referenced_wi`` (``planner/agent.py:232-260``),
        including the Arabic correction message — a responder self-corrects
        better in the language it is writing, and that call was made once
        already for the deep_search alias map.

        Three behaviours, and the asymmetry between them is the design:

        * **An unknown label is a `ModelRetry`, not a drop.** A verdict naming
          ``D4`` on a two-document turn means the model lost track of which
          answers exist; silently discarding it would publish (or withhold) a
          card on a reading nobody checked.
        * **A dispatched label with NO verdict is not an error.** It defaults to
          ``card=False`` downstream via :meth:`ResponderOutput.verdict_for`
          returning ``None`` (§6). Retrying for completeness would burn a
          request to re-derive the safe default.
        * **`card=False` forces `title=""`.** The two fields can disagree
          coming out of a flash model, and a stray title on a declined card is
          how a "no card" verdict ends up publishing a titled row downstream if
          any caller reads ``title`` as truthiness.

        Duplicates are deduped (first verdict wins) so
        :meth:`ResponderOutput.verdict_for` cannot return one of two
        contradicting answers depending on iteration order.
        """
        allowed = tuple(
            str(label).strip() for label in (ctx.deps.doc_labels or ()) if str(label).strip()
        )
        if not allowed:
            # Defensive: with no allow-list every label is "unknown", so the
            # retry loop could never converge — it would burn every retry and
            # then fail the run, which under D7 costs the turn all its cards.
            # A turn with no dispatched labels has nothing to publish anyway.
            if value.cards:
                logger.warning(
                    "simple_search responder: %d card verdict(s) with an empty "
                    "doc_labels allow-list — dropping them",
                    len(value.cards),
                )
            value.cards = []
            return value

        canonical = {label.upper(): label for label in allowed}
        seen: set[str] = set()
        kept: list[CardVerdict] = []
        for verdict in value.cards:
            label = canonical.get(str(verdict.doc or "").strip().upper())
            if label is None:
                raise ModelRetry(
                    f"لا يوجد مستند بالرمز «{verdict.doc}» في هذه الجولة. "
                    f"استخدم أحد هذه الرموز فقط: {'، '.join(allowed)}."
                )
            if label in seen:
                logger.warning(
                    "simple_search responder: duplicate verdict for %s — keeping "
                    "the first", label,
                )
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
    "RESPONDER_EXCERPT_CHARS",
    "RESPONDER_LIMITS",
    "WI_TITLE_MAX",
    "ResponderDeps",
    "CardVerdict",
    "ResponderOutput",
    "create_responder_agent",
]
