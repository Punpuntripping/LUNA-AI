"""Pydantic schemas for the deep-search v4 Planner agent (planner-driven loop).

The planner is a **two-phase** agent:

- **Phase 1 — decide.** ``planner_decider`` emits a :class:`PlannerDecision`:
  one of four retrieval *modes*, an optional ``support`` flag (modes 1–3),
  optional sector pre-filter, and a logged rationale. It may instead pause via
  the ``ask_user`` deferred tool when the query is too vague to plan.
- **Phase 3 — respond.** ``planner_responder`` emits a :class:`PlannerResponse`:
  the user-facing Arabic chat summary, a plain-text next-step suggestion, and a
  machine ``suggested_action`` seam for future planner tools.

Phase 2 (retrieval) runs as plain Python between the two — no schema here.

The per-mode numeric profile (expander caps, result budgets, aggregator prompt
key) is derived from :class:`PlannerDecision` in :mod:`.apply` —
``build_retrieval_config`` — so the LLM never picks numbers.

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


class PlannerResponse(BaseModel):
    """Phase-3 output — the user-facing chat message.

    Emitted by ``planner_responder`` after retrieval. The planner — not the
    aggregator — writes the chat summary the user actually reads. The aggregator
    still produces the immutable ``agent_search`` artifact body separately.
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
    suggested_action: SuggestedAction = Field(
        default="none",
        description=(
            "Machine-readable next-step hint. 'internet' — a DB gap warrants a "
            "web search; 'cross_executor' — another corpus is worth searching; "
            "'writer' — hand off to the writer; 'none' — nothing to suggest."
        ),
    )


__all__ = ["Mode", "SuggestedAction", "PlannerDecision", "PlannerResponse"]
