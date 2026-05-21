"""ContextBlock — the structured context-bundle contract (§4.1).

A :class:`ContextBlock` is the unit of context that flows from the planner into
the downstream stages (expanders + aggregator) of the deep_search v4 pipeline.
The planner emits **one** ``context_labels`` list per turn; the runner derives
``ContextBlock`` objects from that list and threads the SAME filtered bundle
through every executor's ``LoopState.context_blocks`` AND
``AggregatorInput.context_blocks``. The reranker is hardcoded to receive zero
blocks regardless of opt-in.

Frozen vocabulary (§4.2 — re-quoted verbatim so the contract lives next to the
type):

    | label                  | source                                                                 | persistence    | default opt-in?      | volume                            |
    |------------------------|------------------------------------------------------------------------|----------------|----------------------|-----------------------------------|
    | case_brief             | orchestrator: ``_load_case_block(supabase, case_id, user_id)`` from    | case           | yes (when case-      | small (~500-2000 chars)           |
    |                        | ``lawyer_cases + case_memories``                                       |                | scoped)              |                                   |
    | planner_brief          | planner decider output                                                 | turn           | only when non-empty  | variable (longer when attachments |
    |                        |                                                                        |                | (empty is the        |  are present — see prompts.py)    |
    |                        |                                                                        |                | expected default)    |                                   |
    | prior_search_lessons   | orchestrator: prior ``kind=agent_search`` items rendered as            | conversation   | almost always (cheap | small                             |
    |                        | ``{title, describe_query, confidence, summary}`` (summary =            |                | and small)           |                                   |
    |                        | artifact_summarizer output, already includes gap analysis)             |                |                      |                                   |

Note on attached artifacts: the router-curated ``attached_items`` reach the
*planner decider only* — they are NOT forwarded to expanders / aggregator.
When something in an attachment matters downstream, the planner distills it
into ``planner_brief``. There is no ``attached_artifacts`` label.

New labels require a doc update (this module's docstring + §4.2 of the redesign
spec). Do NOT add labels ad-hoc — the planner system prompt enumerates the
exact three strings above.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ContextBlock:
    """A single structured context block carried by the planner's bundle.

    ``label`` — one of the frozen vocabulary strings: ``"case_brief"``,
    ``"planner_brief"``, ``"prior_search_lessons"``.
    ``body`` — Arabic prose, rendered verbatim into the ``<context_blocks>`` XML
    block in the expander + aggregator user messages.
    ``persistence`` — lifetime classification for telemetry / future caching:
    ``"case"`` (lives with the case), ``"conversation"`` (lives with the
    conversation), or ``"turn"`` (recomputed each dispatch).
    ``source_item_id`` — optional ``workspace_items.item_id`` reference for
    audit / dedup / UI back-link; ``None`` for synthesized blocks
    (``case_brief``, ``planner_brief``).
    """

    label: str
    body: str
    persistence: Literal["case", "conversation", "turn"]
    source_item_id: str | None = None


__all__ = ["ContextBlock"]
