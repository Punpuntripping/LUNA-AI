"""ContextBlock — the structured context-bundle contract (§4.1).

A :class:`ContextBlock` is the unit of context that flows from the planner into
the downstream stages (expanders + aggregator) of the deep_search v4 pipeline.
The planner emits **one** ``context_labels`` list per turn; the runner derives
``ContextBlock`` objects from that list and threads the SAME filtered bundle
through every executor's ``LoopState.context_blocks`` AND
``AggregatorInput.context_blocks``.

Reranker exposure (CHANGED 2026-07-24 — plan §7 / decision D6 of
``.claude/plans/case_topics_loop.md``): rerankers used to receive **zero**
blocks regardless of opt-in. They now receive exactly ONE — the
``planner_brief`` body — rendered as a ``<planner_brief>`` block at the head of
the reranker user message (head placement is load-bearing: all N concurrent
reranker calls in a turn share that prefix, so it stays cacheable). Both
rerankers (``reg_compliance_search`` and ``case_search``) do this. ``case_brief``
and ``prior_search_lessons`` still never reach a reranker. Do not "restore" the
zero-blocks rule — it is superseded, not forgotten.

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

``case_document`` (added 2026-10-25 — ``.claude/plans/case_documents_forwarding.md``):

    | label                  | source                                                                 | persistence    | default opt-in?      | volume                            |
    |------------------------|------------------------------------------------------------------------|----------------|----------------------|-----------------------------------|
    | case_document          | orchestrator: ``shared/case_documents.build_case_document_blocks``     | turn           | n/a — not a planner  | per document: summary always;     |
    |                        | from the turn's ``attached_items`` (uploaded case papers only — see    |                | label; forwarded     | full text ≤ 12,000 chars when due |
    |                        | ``forwardable_documents``). ONE block per document,                    |                | whenever the turn    | (≤ 30,000 per recipient)          |
    |                        | ``source_item_id`` = the document's ``item_id``                        |                | attaches one         |                                   |

``case_document`` is NOT in the planner's ``context_labels`` vocabulary and is
not chosen by it: every forwarded document reaches both expanders (summary,
plus full text for those the planner names in ``case_full_text_wis`` /
``reg_full_text_wis``) and the aggregator (full text), inserted right after
``case_brief``. It never reaches the sector_picker or a reranker. Every other
attached item (a chat, an official record, the user's notes, a reference text,
any non-attachment kind) still reaches the *planner decider only* — when
something in it matters downstream, the planner distills it into
``planner_brief``.

New labels require a doc update (this module's docstring + §4.2 of the redesign
spec). Do NOT add labels ad-hoc — the planner system prompt enumerates the
exact strings above.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ContextBlock:
    """A single structured context block carried by the planner's bundle.

    ``label`` — one of the frozen vocabulary strings: ``"case_brief"``,
    ``"statute_articles"``, ``"planner_brief"``, ``"prior_search_lessons"``,
    ``"case_document"``.
    ``body`` — Arabic prose, rendered verbatim into the ``<context_blocks>`` XML
    block in the expander + aggregator user messages.
    ``persistence`` — lifetime classification for telemetry / future caching:
    ``"case"`` (lives with the case), ``"conversation"`` (lives with the
    conversation), or ``"turn"`` (recomputed each dispatch).
    ``source_item_id`` — optional ``workspace_items.item_id`` reference for
    audit / dedup / UI back-link; set on every ``case_document`` block, ``None``
    for synthesized blocks (``case_brief``, ``planner_brief``,
    ``statute_articles``).

    ``statute_articles`` carries the verbatim text of every article
    ``fetch_article`` resolved this turn, and is the one block whose body is NOT
    prose the planner wrote — it is corpus text, forwarded structurally so that
    capping what the PLANNER reads does not cap what the executors and the
    aggregator read. See ``orchestrator._build_candidate_context_blocks``.
    """

    label: str
    body: str
    persistence: Literal["case", "conversation", "turn"]
    source_item_id: str | None = None


__all__ = ["ContextBlock"]
