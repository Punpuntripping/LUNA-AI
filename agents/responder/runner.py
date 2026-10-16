"""``run_responder`` — one call, one bubble (plan unified_responder.md §4.1).

Returns the mode's output, or ``None`` when the call failed. The runner never
applies a failure policy: each family keeps its own (D3 / trap §10) —
deep_search publishes from the artifact (``_response_from_artifact``),
simple_search publishes nothing (D7), writing falls back to a templated line.

Tracking: ``run_tracked`` with stage ``responder`` → one Logfire span and one
``llm_calls`` ledger row labelled ``responder`` (``agent_family`` = the calling
family, ``subtype`` = the mode), priced via the ``responder`` slot. The
per-turn §8 fields (mode, asked_question, question_basis, chips_n) ride the
span through ``ResponderBase.tracking_output``.

وضع السرية: the router context and the artifact package arrive already encoded
with the turn codec; the output is therefore in the encoded domain too and goes
through the caller's existing decode path (the same one ``chat_summary`` takes
today). Nothing here encodes or decodes.
"""
from __future__ import annotations

import logging
from typing import Any

from agents.models import salvage_next_steps
from agents.utils.agent_models import ModelPolicy
from agents.utils.tracking import run_tracked

from .agent import RESPONDER_LIMITS, RESPONDER_SLOT, ResponderDeps, create_responder
from .context import render_context_block
from .models import OUTPUT_TYPES, ResponderBase, ResponderInput, SimpleSearchArtifact
from .prompts import build_turn_message

logger = logging.getLogger(__name__)

#: Calling family per mode — the ``agent_family`` stamped on the span + ledger.
_FAMILY: dict[str, str] = {
    "deep_search": "deep_search",
    "simple_search": "simple_search",
    "writing": "writing",
}


def build_deps(inp: ResponderInput) -> ResponderDeps:
    """The run's deps — the rendered context block + the validators' allow-lists."""
    doc_labels: tuple[str, ...] = ()
    suppress = False
    if isinstance(inp.artifact, SimpleSearchArtifact):
        doc_labels = tuple(
            str(getattr(d, "label", "")).strip()
            for d in inp.artifact.docs
            if str(getattr(d, "label", "")).strip()
        )
        suppress = bool(inp.artifact.suppress_next_steps)
    return ResponderDeps(
        mode=inp.mode,
        conversation_id=inp.conversation_id,
        case_id=inp.case_id,
        context_block=render_context_block(inp),
        wi_alias_map=dict(inp.wi_alias_map or {}),
        doc_labels=doc_labels,
        suppress_next_steps=suppress,
    )


async def run_responder(
    inp: ResponderInput,
    *,
    model_override: ModelPolicy | str | None = None,
) -> ResponderBase | None:
    """Run the unified responder for one turn.

    Returns the mode's output (``DeepSearchResponse`` / ``SimpleSearchResponse``
    / ``WritingResponse``) or ``None`` on any failure (model error, wrong
    output type, empty ``chat_summary_md``). Never raises.
    """
    try:
        agent = create_responder(inp.mode, model_override)
        deps = build_deps(inp)
        history: list[Any] | None = None
        if inp.router_context is not None:
            # D4: the SAME message_history list the router ran on.
            history = list(inp.router_context.message_history or []) or None
        result = await run_tracked(
            agent,
            build_turn_message(inp),
            deps=deps,
            stage=RESPONDER_SLOT,
            slot=RESPONDER_SLOT,
            agent_family=_FAMILY[inp.mode],
            subtype=inp.mode,
            message_history=history,
            usage_limits=RESPONDER_LIMITS,
        )
        output = result.output
        expected = OUTPUT_TYPES[inp.mode]
        if not isinstance(output, expected):
            raise TypeError(
                f"responder({inp.mode}) returned {type(output).__name__}, "
                f"not {expected.__name__}"
            )
        output.chat_summary_md = (output.chat_summary_md or "").strip()
        if not output.chat_summary_md:
            raise ValueError("responder returned an empty chat_summary_md")
        # §6.1 rule 7 code guard: the previous question went unanswered → no
        # second question in a row, whatever the model wrote (the prompt says
        # so too; this makes it a guarantee). Answer chips go with it below.
        if output.follow_up_question and inp.effective_follow_up_status() == "unanswered":
            logger.info(
                "responder(%s): dropped a follow-up question — previous one "
                "unanswered (no-nag) conversation=%s",
                inp.mode, inp.conversation_id,
            )
            output.follow_up_question = None
            output.question_basis = None
        # Never-fail floor: the validator already retried/salvaged; re-salvage
        # is idempotent and guarantees a malformed chip cannot reach the caller.
        salvaged = salvage_next_steps(
            output.next_steps,
            allow_open=inp.mode == "simple_search",
            allow_answer=bool(output.follow_up_question),
        )
        if salvaged != output.next_steps:
            output.next_steps = salvaged
            output._next_steps_salvaged = True
        return output
    except Exception as exc:  # noqa: BLE001 — the caller applies its own policy
        logger.warning(
            "responder(%s): failed for conversation %s — caller falls back: %s",
            inp.mode, inp.conversation_id, exc, exc_info=True,
        )
        return None


__all__ = ["build_deps", "run_responder"]
