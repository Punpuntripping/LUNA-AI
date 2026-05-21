"""Pydantic schemas for the deep-search v4 Planner agent (planner-driven loop).

The planner is a **two-phase** agent:

- **Phase 1 — decide.** ``planner_decider`` emits a :class:`PlannerDecision`:
  one of four retrieval *modes*, an optional ``support`` flag (modes 1–3),
  optional sector pre-filter, a logged rationale, AND the new comprehension
  outputs (§3.1 of the redesign): ``planner_brief`` — novel factual context the
  planner discovered that isn't already carried by the query or other context
  blocks; and ``context_labels`` — which context blocks flow to the
  expanders + aggregator. The decider may instead pause via the ``ask_user``
  deferred tool when the query is too vague to plan.
- **Phase 3 — respond.** ``planner_responder`` emits a :class:`PlannerResponse`:
  the user-facing Arabic chat summary, a plain-text next-step suggestion, AND
  the Phase E publish gate fields (``build_artifact`` + ``referenced_item_id``)
  the orchestrator branches on to decide whether to publish a new
  ``workspace_item`` or point at a prior one.

Phase 2 (retrieval) runs as plain Python between the two — no schema here.

The per-mode numeric profile (expander caps, result budgets, aggregator prompt
key) is derived from :class:`PlannerDecision` in :mod:`.apply` —
``build_retrieval_config`` — so the LLM never picks numbers.

:class:`PriorSearchSummary` is the comprehension surface the orchestrator
hydrates from prior ``kind='agent_search'`` workspace items: ``title``,
``describe_query`` (the original task description), ``summary`` (Window D's
``artifact_summarizer`` output — may be empty when the async trigger has not
yet completed), and ``confidence``. The planner reads this list to reason about
"is this question already covered by a prior task in this conversation?".

This module is **pure** — it imports only ``pydantic``, never ``pydantic_ai``
or any executor package, so the model tier of the test suite runs without the
agent runtime installed.
"""
from __future__ import annotations

import json as _json
from typing import Literal

from pydantic import BaseModel, Field, field_validator


# The four retrieval modes. ``reg_led`` is the default for ordinary questions.
Mode = Literal["case_led", "reg_led", "compliance_led", "full"]

# Forward-compat seam: what the planner suggests the user do next. Each literal
# (except "none"/"writer") maps to a future planner tool — see
# PLANNER_TOOL_MIGRATION_PLAN.md.
#
# Phase E note (§3.4a / §9 O4): the ``suggested_action`` FIELD has been dropped
# from :class:`PlannerResponse` — the new ``build_artifact`` + ``referenced_item_id``
# fields cover the machine-readable routing the enum used to encode, and the
# responder's chat_summary conveys the next-step intent in prose. The Literal
# alias itself stays defined here per the redesign spec — it remains the source
# of truth for any downstream consumer cleanup that still references the type
# (logger payload migration, dashboard schemas), and is scheduled for deletion
# in a follow-up wave once that cleanup is verified.
SuggestedAction = Literal["internet", "cross_executor", "writer", "none"]


class PlannerDecision(BaseModel):
    """Phase-1 output — the retrieval plan.

    Emitted by ``planner_decider``. A single mode choice drives everything:
    executor set, caps, aggregator prompt. The apply step
    (``build_retrieval_config``) expands it into a concrete ``RetrievalConfig``.
    """

    mode: Mode = Field(
        ...,
        description=(
            "Exactly one retrieval mode. 'reg_led' is the default for ordinary "
            "legal questions; 'case_led' when the lawyer asks about a specific "
            "precedent or an open case; 'compliance_led' for procedural / "
            "e-government questions; 'full' when the query genuinely needs the "
            "statute, the procedure, and the case law together."
        ),
    )
    support: bool = Field(
        default=False,
        description=(
            "When True, modes 1–3 also run their support executor (case_led + "
            "reg, reg_led + compliance, compliance_led + reg). Ignored when "
            "mode == 'full' — 'full' has no support role."
        ),
    )
    sectors: list[str] | None = Field(
        default=None,
        description=(
            "1–4 canonical sector names from "
            "``agents.deep_search_v4.shared.sector_vocab.regulations.VALID_SECTORS``, "
            "or ``null`` when the query isn't sector-specific."
        ),
    )
    rationale: str = Field(
        ...,
        description="Short Arabic justification for the mode + support choice (logged).",
    )
    aborted: bool = Field(
        default=False,
        description=(
            "Set True when the user's reply after an ask_user deferral is so "
            "off-script that no plan can be built. The orchestrator re-routes "
            "via the router instead of running phases 2–3."
        ),
    )
    planner_brief: str = Field(
        default="",
        description=(
            "Novel factual context the planner found that is NOT already "
            "carried by the user's query or by any other context block "
            "(case_brief, prior_search_lessons, attached_artifacts). "
            "Empty is the EXPECTED default — non-empty only when the "
            "planner has genuinely new context to inject. "
            "Descriptive, not directive: state facts, do NOT propose angles "
            "or instruct downstream agents what to look for. The expanders "
            "and aggregator are designed to work from the raw query — the "
            "brief supplements, never steers."
        ),
    )
    context_labels: list[str] = Field(
        default_factory=lambda: ["case_brief", "planner_brief"],
        description=(
            "Which context block labels to forward downstream. The "
            "same set flows to all executor expanders AND the "
            "aggregator (the reranker never receives context). "
            "Vocabulary: 'case_brief', 'planner_brief', "
            "'prior_search_lessons', 'attached_artifacts'. "
            "Defaults include case_brief and planner_brief."
        ),
    )

    # ------------------------------------------------------------------
    # Validators — sectors only (mode/support/aborted are constrained by type)
    # ------------------------------------------------------------------

    @field_validator("sectors", mode="before")
    @classmethod
    def _coerce_sectors(cls, v):
        # Coerce LLM quirks before strict list[str] validation:
        # - JSON-stringified arrays: '["x","y"]' -> ["x","y"]
        # - empty string: ''  -> None
        if isinstance(v, str):
            if v.strip() == "":
                return None
            try:
                parsed = _json.loads(v)
                if isinstance(parsed, list):
                    return parsed
            except (_json.JSONDecodeError, TypeError):
                pass
        return v

    @field_validator("sectors")
    @classmethod
    def _validate_sectors_size(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        # Fallback rule: ≥5 entries means the query is too broad — drop the filter.
        if len(value) >= 5:
            return None
        if len(value) < 1:
            raise ValueError(
                f"sectors must contain 1–4 entries or be null; got {len(value)}"
            )
        return value


class PriorSearchSummary(BaseModel):
    """Comprehension surface for a prior ``kind='agent_search'`` workspace item.

    Hydrated by the orchestrator from ``workspace_items`` rows in the current
    conversation (see ``_load_prior_search_summaries`` in
    :mod:`agents.orchestrator`). The planner reads this list in its dynamic
    instructions to reason about prior tasks without opening every artifact.

    ``summary`` comes from Window D's ``artifact_summarizer`` agent
    (``workspace_items.summary`` column, written async by a Postgres trigger →
    ``/internal/summarize-workspace-item`` webhook). It MAY be empty for rows
    where the trigger hasn't run yet (race) or for pre-migration rows; the
    planner system prompt instructs the decider to fall back to just
    ``{title, describe_query, confidence}`` in that case. There is intentionally
    NO ``gaps`` field — gap analysis already lives inside the ``summary`` text
    in its «الخلاصة:» section (avoids duplication / drift).
    """

    item_id: str = Field(description="workspace_items.item_id (UUID).")
    title: str = Field(
        description="workspace_items.title — equals the router's task_label.",
    )
    describe_query: str = Field(
        description=(
            "workspace_items.describe_query — the router's original "
            "description of the question that produced this artifact."
        ),
    )
    summary: str = Field(
        default="",
        description=(
            "workspace_items.summary (artifact_summarizer output). "
            "MAY be empty when the trigger hasn't completed yet."
        ),
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="From workspace_items.metadata.confidence.",
    )
    created_at: str = Field(description="ISO-8601 timestamp.")


class PlannerResponse(BaseModel):
    """Phase-3 output — the user-facing chat message + publish gate.

    Emitted by ``planner_responder`` after retrieval. The planner — not the
    aggregator — writes the chat summary the user actually reads. The aggregator
    still produces the immutable ``agent_search`` artifact body separately.

    Phase E (§3.4a) adds the **publish gate**: ``build_artifact`` tells the
    orchestrator whether to publish a new ``workspace_item`` for this turn, and
    ``referenced_item_id`` (paired with ``build_artifact=False``) lets the
    orchestrator emit a ``referenced_existing_item`` SSE event pointing to a
    prior covering artifact. The legacy ``suggested_action`` enum was removed
    in Phase E (§9 O4 — promoted to decided) since these two fields cover the
    machine-readable routing it used to encode.
    """

    chat_summary_md: str = Field(
        ...,
        description=(
            "User-facing Arabic chat summary of the findings. Prose, not a "
            "report: no '[n]' citation markers, no '##' headings — those belong "
            "to the artifact, not the chat bubble."
        ),
    )
    suggestion_md: str = Field(
        default="",
        description=(
            "A short plain-text next-step suggestion for the user, or empty "
            "string when there is nothing useful to suggest."
        ),
    )
    build_artifact: bool = Field(
        default=True,
        description=(
            "When False, the orchestrator skips publishing a workspace_item "
            "for this turn. Set False when (a) the aggregator returned the "
            "empty-results sentinel, or (b) a prior artifact already in this "
            "conversation covers the question. Otherwise True."
        ),
    )
    referenced_item_id: str | None = Field(
        default=None,
        description=(
            "When build_artifact=False because a prior artifact covers the "
            "question, the item_id of that prior artifact. Used by the "
            "orchestrator to emit a referenced_existing_item SSE event. Null "
            "otherwise. May arrive as literal string 'None'/'null' from LLM "
            "JSON output; the field validator coerces those to actual None."
        ),
    )

    # ------------------------------------------------------------------
    # Validators — referenced_item_id (string-'None'/'null' coercion)
    # ------------------------------------------------------------------

    @field_validator("referenced_item_id", mode="before")
    @classmethod
    def _coerce_none_strings(cls, v):
        """LLMs sometimes serialize None as the literal string 'None' or 'null'.

        Coerce those (and empty/whitespace) to actual None so downstream code
        can rely on truthiness checks (``if response.referenced_item_id:`` etc.)
        without having to second-guess the JSON shape the responder emitted.
        """
        if v is None:
            return None
        if isinstance(v, str):
            stripped = v.strip()
            if stripped == "" or stripped.lower() in ("none", "null"):
                return None
            return stripped
        return v


__all__ = [
    "Mode",
    "SuggestedAction",
    "PlannerDecision",
    "PriorSearchSummary",
    "PlannerResponse",
]
