"""The ``simple_search`` synthesizer — Layer 3, one agent per object (§2.2).

An agent **factory keyed by entry level**. Every invocation receives the §4
unfold of ONE document under the §5 budget (run by the deterministic
:mod:`agents.simple_search.unfold` layer before the model is built), plus the
raw user message and the pre-numbered references. Its four responsibilities,
per plan §2.2:

1. **Validate** — is this actually the object the user meant? On failure it
   *rejects*, which is why :attr:`SynthesizerOutput.rejected` exists: the
   runner reads it, loops back to the searcher, and spawns a **fresh**
   synthesizer that starts over (D3).
2. **Answer** in Arabic.
3. **Decide whether a workspace card is warranted** — not every lookup deserves
   one (D4's "minimal summaries").
4. **Cite** the pre-numbered references (§6.4).

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
model to emit two fields this schema does not have and omit three it does.
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

#: Workspace-item titles are capped at 80 chars upstream (``workspace_items.title``
#: is sized for the router's ``task_label``). Enforced on the field so an
#: over-long title is a validation retry rather than a truncated card.
WI_TITLE_MAX = 80


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
    wi_warranted: bool = Field(
        default=False,
        description=(
            "Whether this answer deserves a durable workspace card. False for "
            "one-line answers, pointers, not-founds and rejections."
        ),
    )
    wi_title: str = Field(
        default="",
        max_length=WI_TITLE_MAX,
        description=(
            "Short Arabic content-derived card title naming the object "
            f"(≤{WI_TITLE_MAX} chars, no verbs). Empty when wi_warranted is false."
        ),
    )

    def is_answer(self) -> bool:
        """True when this output carries a usable answer (not a rejection)."""
        return not self.rejected and bool(self.synthesis_md.strip())


# The retry message enumerates THIS schema's fields. The aggregator's message
# names ``gaps``/``confidence`` — sending that here would misdirect the retry
# toward a schema that does not exist (§7.2).
_SYNTH_RETRY_MSG = (
    "Return the output as a single valid JSON object conforming to the schema "
    "(synthesis_md, used_refs, rejected, rejection_reason, wi_warranted, "
    "wi_title) only — with no text and no <thinking> tag outside the JSON. "
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
