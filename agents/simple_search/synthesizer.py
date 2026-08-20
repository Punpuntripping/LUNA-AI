"""The ``simple_search`` synthesizer — Layer 3, one agent per object (§2.2).

An agent **factory keyed by entry level**. Every invocation receives the §4
unfold of ONE document under the §5 budget (run by the deterministic
:mod:`agents.simple_search.unfold` layer before the model is built), plus the
raw user message and the pre-numbered references. Its three responsibilities,
per plan §2.2:

1. **Validate** — is this actually the object the user meant? On failure it
   *rejects*, which is why :attr:`SynthesizerOutput.rejected` exists: the
   runner reads it, loops back to the searcher, and spawns a **fresh**
   synthesizer that starts over (D3).
2. **Answer** in Arabic.
3. **Cite** the pre-numbered references (§6.4).

There used to be a fourth — deciding whether the turn leaves a durable workspace
card — and ``.claude/plans/simple_search_responder.md`` §5 removed it. Whether a
turn is worth a card is a *turn*-level judgement, and this is the wrong vantage
point to make it from: up to three synthesizers run concurrently (``_run_round``
in ``agents/simple_search/runner.py``), each holding exactly one document, blind
to its siblings and to what the turn as a whole produced. That decision now
belongs to the ``simple_search`` responder, which runs once per turn after every
synthesizer has settled and sees every body at once — the same division by which
``planner_responder`` owns ``build_artifact`` in ``deep_search``
(``agents/deep_search_v4/planner/models.py:246-278``, gating the publish at
``agents/orchestrator.py:3063``). Nothing about the *behaviour* here changed:
this agent still always writes the full document body and always cites ``[n]``,
precisely because it no longer knows whether a card will exist (§5).

Model: the ``simple_search_synthesizer`` slot — ``tier_2`` / ``_FLASH_MEDIUM``
(§3). The deep_search aggregator runs ``_FLASH_MAX``; this family deliberately
does not, because the whole point is that it costs less. One object in hand
bounds the reasoning.

Structured output opts into the shared salvager
(``agents/utils/structured_output.py:123-159``): flash models sometimes finalise
as ``<thinking>…</thinking>{json}`` text instead of calling the output tool, and
salvaging that text is strictly cheaper than a retry that re-sends the whole
unfolded document. The retry message names **our** fields — copying the
aggregator's ``(synthesis_md, used_refs, gaps, confidence)`` would tell the
model to emit two fields this schema does not have and omit two it does. The
trap is symmetric, and §11.1 records the other direction: *dropping* a field
while leaving ``_SYNTH_RETRY_MSG`` alone teaches the model to keep emitting a
field the schema no longer accepts, so every future salvage carries dead keys
into a validation retry. The message moves in the same edit as the schema.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from pydantic import BaseModel, Field
from pydantic_ai import Agent, TextOutput
from pydantic_ai.usage import UsageLimits

from agents.simple_search.models import SimpleSearchLevel
from agents.simple_search.prompts import get_synthesizer_prompt
from agents.utils.agent_models import ModelPolicy, get_agent_model
from agents.utils.structured_output import make_json_salvager

logger = logging.getLogger(__name__)

#: ``agents/utils/agent_models.py`` slot for this agent.
SYNTHESIZER_SLOT = "simple_search_synthesizer"


@dataclass
class SynthesizerDeps:
    """Scope identifiers only — the synthesizer has no tools and reads no DB.

    It exists so the tracking span carries ``conversation_id`` / ``case_id``
    (``tracking._identity_from_deps`` reads exactly these two off the deps
    object). Everything the model sees arrives in the user message: the object
    is already unfolded and the references are already numbered.
    """

    conversation_id: str = ""
    case_id: str | None = None


class SynthesizerOutput(BaseModel):
    """What one synthesizer returns for one document.

    Kept deliberately close to the aggregator's shape where the shape is load
    bearing — ``synthesis_md`` + ``used_refs`` — and deliberately different
    where the aggregator's fields carry sub-query semantics a lookup has no
    concept of: ``gaps`` (which enumerate sub-queries that came back
    insufficient) and ``confidence`` (which scores corpus coverage of a
    multi-axis question) are **dropped**. A lookup either holds the right object
    or it does not, and that binary is :attr:`rejected`.

    Two fields this schema once carried are gone for a different reason — not
    "a lookup has no such concept" but "one document is the wrong vantage
    point". A card-warranted flag and a card title asked a single synthesizer,
    holding one document and blind to its concurrent siblings, whether the
    *turn* deserves a durable workspace card. Plan §5 / D2 moved both to the
    ``simple_search`` responder, which runs once after every synthesizer has
    settled and rules with every body in hand — the shape ``build_artifact``
    already has on deep_search's ``PlannerResponse``
    (``agents/deep_search_v4/planner/models.py:246-278``). What is left here is
    strictly per-document: the body, the citations it used, and whether the
    document was the right one at all.

    The consequence is deliberate and named in §5: this output always cites
    ``[n]``, so a body the responder declines to card arrives with markers
    pointing at a panel that was never published. ``_strip_citation_markers``
    (``agents/simple_search/runner.py``) removes them on the way into the
    bubble. That is no longer a patch over a self-contradicting agent — it is
    the hand-off step between two agents that each did their own job correctly.
    """

    synthesis_md: str = Field(
        default="",
        description=(
            "The Arabic answer the user reads. Empty ONLY when rejected=true."
        ),
    )
    used_refs: list[int] = Field(
        default_factory=list,
        description=(
            "The reference numbers actually cited as [n] in synthesis_md. "
            "Numbers are assigned in code before the run — never invent one."
        ),
    )
    rejected: bool = Field(
        default=False,
        description=(
            "True when the object in hand is NOT the one the user meant (wrong "
            "نظام, wrong مادة number, the executive regulation rather than the "
            "statute). Triggers a fresh retrieval round + a fresh synthesizer. "
            "Identity mismatches only — never thin or truncated content."
        ),
    )
    rejection_reason: str = Field(
        default="",
        description=(
            "Arabic, specific, naming what was received vs what was expected. "
            "The next retrieval round runs on this text, so a vague reason "
            "wastes the round. Empty unless rejected."
        ),
    )

    def is_answer(self) -> bool:
        """True when this output carries a usable answer (not a rejection)."""
        return not self.rejected and bool(self.synthesis_md.strip())


# The retry message enumerates THIS schema's fields. The aggregator's message
# names ``gaps``/``confidence`` — sending that here would misdirect the retry
# toward a schema that does not exist (§7.2). It also listed the two card
# fields until they moved to the responder (responder plan §5, trap §11.1); a
# retry message outliving its fields is the same bug pointed the other way.
# Keep this list identical to ``SynthesizerOutput.model_fields``.
_SYNTH_RETRY_MSG = (
    "Return the output as a single valid JSON object conforming to the schema "
    "(synthesis_md, used_refs, rejected, rejection_reason) only — with no text "
    "and no <thinking> tag outside the JSON. "
    "The `synthesis_md` value must be in Arabic."
)


def _synthesizer_text_output() -> TextOutput:
    """``TextOutput`` salvage member for the synthesizer's ``output_type`` union."""
    return TextOutput(make_json_salvager(SynthesizerOutput, retry_msg=_SYNTH_RETRY_MSG))


SYNTHESIZER_LIMITS = UsageLimits(
    # Bounded, unlike the aggregator's 100k: this agent answers about ONE
    # document and its longest plausible output is a whole-نظام structural walk
    # (~2-3k tokens). 24k is ~8x that, with room for the reasoning tokens that
    # count against `output_tokens` on DashScope.
    output_tokens_limit=24_000,
    # No tools — one call, plus headroom for output validation retries.
    request_limit=4,
)


def create_synthesizer_agent(
    level: SimpleSearchLevel,
    model_override: ModelPolicy | str | None = None,
) -> Agent[SynthesizerDeps, SynthesizerOutput]:
    """Build a synthesizer for one entry level.

    Args:
        level: which of the six §4 levels this object is. Selects the prompt
            variant; an unknown level raises ``KeyError`` from the registry
            rather than silently defaulting.
        model_override: optional tier override token / :class:`ModelPolicy` for
            the ``simple_search_synthesizer`` slot (the tier stays fixed).

    Returns:
        A fresh agent. Callers build a NEW one per invocation — a loop-back
        after a rejection must start over with no memory of the object it
        rejected (D3).
    """
    agent: Agent[SynthesizerDeps, SynthesizerOutput] = Agent(
        get_agent_model(SYNTHESIZER_SLOT, model_override),
        name=f"simple_search_synthesizer_{level}",
        deps_type=SynthesizerDeps,
        output_type=[SynthesizerOutput, _synthesizer_text_output()],
        instructions=get_synthesizer_prompt(level),
        retries=2,
        output_retries=3,
    )
    return agent


__all__ = [
    "SYNTHESIZER_SLOT",
    "SYNTHESIZER_LIMITS",
    "SynthesizerDeps",
    "SynthesizerOutput",
    "create_synthesizer_agent",
]
