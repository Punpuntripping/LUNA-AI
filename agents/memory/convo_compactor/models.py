"""Input / output contracts for the convo_compactor agent.

The agent's audience is **other agents** (the router on the next turn, and the
planners it dispatches to), not the end user. Output is a single Arabic
markdown blob that REPLACES a span of conversation messages which is being
removed from the context window — so every downstream context surface
(``agents/router/context.py``, ``backend/app/services/workspace_context.py``,
``agents/memory/summarize.py``) reads this instead of the dropped turns.

Two contract details are deliberate and load-bearing:

1. **Single semantic field** on the LLM output (``summary_md``). The three
   prescribed sections live in the prompt's shape, not in three Pydantic
   fields — which makes the ``TextOutput`` salvager loss-free: a flash model
   that finalises as plain text maps 1:1 onto ``summary_md`` with no
   ``ModelRetry`` round. A three-field output would lose two sections on a
   text emission.
2. **``failed`` defaults to ``True``** on :class:`CompactionOutput`. This
   agent is fail-CLOSED, unlike its ``artifact_summarizer`` sibling: a caller
   that forgets to check, or a construction path that forgets to set the flag,
   must degrade into "do not compact" rather than "advance the cutoff and
   substitute junk". See ``runner.py`` for the full rationale.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field


@dataclass
class CompactionInput:
    """Everything the compactor needs to write one carry-forward summary.

    - ``messages`` — the batch being compacted, **oldest-first**, as
      ``[{"role": str, "content": str}]``. These are the messages that leave
      the context window once the cutoff advances.
    - ``workspace_items`` — ``[{"wi_seq": int, "kind": str, "title": str,
      "summary": str | None}]`` for the items created in or before the span.
      Passed to GROUND the ``WI-{n}`` references: the prompt forbids citing a
      seq that does not appear here. Note the item rows themselves are NOT
      dropped by compaction, so this is not an inventory to reproduce — it is
      the list the summary is allowed to link against.
    - ``prior_summary_md`` — the previous ``convo_context.content_md``; ``""``
      on the first compaction. When present the new summary SUPERSEDES it
      (context.py keeps only the most recent ``convo_context``, so anything
      not folded in is lost — this is plan §1.3).
    - ``user_call_name`` — optional; the name the user is addressed by. Keeps
      the summary readable in the third person.
    - ``conversation_id`` — telemetry only; never rendered into the prompt.
      Stamped on the ``convo_compactor.run`` span so a compaction trace can be
      joined back to its conversation (``/convo-monitor`` joins on exactly
      this). Optional so a caller that genuinely has no conversation can omit
      it, but every live caller should pass it.
    """

    messages: list[dict]
    workspace_items: list[dict]
    prior_summary_md: str = ""
    user_call_name: str | None = None
    conversation_id: str | None = None


class CompactionLLMOutput(BaseModel):
    """The structured output the LLM is asked to produce.

    Single semantic field: ``summary_md``. Wrapping it in a Pydantic model
    (rather than ``output_type=str``) gives pydantic_ai a clean schema to
    enforce and avoids accidental whitespace/quote-wrapping, while keeping the
    ``TextOutput`` salvage path loss-free.
    """

    summary_md: str = Field(
        description=(
            "Arabic markdown summary written for downstream AGENTS — carries "
            "the user's intent across the compacted span, which workspace "
            "items it produced (as WI-{n}), and which threads are still open."
        ),
    )


class CompactionOutput(BaseModel):
    """Final output returned by the runner to callers (``compact_conversation``).

    ``failed`` is the contract that matters. It defaults to ``True`` and is
    flipped to ``False`` on exactly one path: a real, non-empty summary came
    back from the model. The caller MUST refuse to insert a ``convo_context``
    item or advance ``conversations.compacted_through_message_id`` while
    ``failed`` is ``True`` — a fallback string standing in for deleted
    messages is the exact bug this agent exists to remove.
    """

    summary_md: str
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_reasoning: int = 0
    tokens_cached: int = 0
    model_used: str = ""
    failed: bool = True
