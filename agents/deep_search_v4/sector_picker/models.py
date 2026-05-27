"""Pydantic schemas for the sector_picker agent.

A small Layer 3 Task agent: it reads the user query + planner_brief +
context_blocks and emits a 2-5 sector AND-filter (or ``None`` if the question
is too broad to filter sensibly). Replaces the planner_decider's old ``sectors``
output — moved out because the decider's flat 38-name list gave the model no
way to distinguish e.g. ``المعاملات التجارية`` from ``حوكمة الشركات والاستثمار``
(diagnosed in conv ``faa3b71e``).

The bounds are load-bearing:

- **< 2 sectors** is not allowed. A single-sector AND-filter is exactly the
  failure mode we are fixing — too narrow → drops the controlling law.
- **> 5 sectors** is treated as "the question is too broad to filter" and the
  output is downgraded to ``None`` (run unfiltered) by the runner.
- ``None`` is the explicit "do not filter" signal.

The output ``sectors`` are canonicalized by the runner against
:data:`agents.deep_search_v4.shared.sector_vocab.regulations.VALID_SECTORS`
before being returned, so the agent layer never has to reason about typos /
LLM invention.
"""
from __future__ import annotations

import json as _json

from pydantic import BaseModel, Field, field_validator


# Lower / upper bounds on the picker's sector list. Re-exported so the runner
# and tests share one source of truth.
MIN_SECTORS = 2
MAX_SECTORS = 5


class SectorPickerOutput(BaseModel):
    """Phase output — the sector AND-filter for one deep_search invocation.

    ``sectors`` is either ``None`` (no filter — run unfiltered) or a list of
    canonical sector names. The model is instructed to bias toward
    **inclusivity**: when the question plausibly touches multiple adjacent
    sectors, include all of them. The downstream filter is a Postgres
    array-overlap (``regulations_v2.sectors[] && {picked}``), so an extra
    adjacent sector only widens the candidate pool — the semantic ranker still
    picks the best matches inside that pool. Missing the right sector is
    fatal; including an extra one is free.
    """

    sectors: list[str] | None = Field(
        default=None,
        description=(
            f"{MIN_SECTORS}-{MAX_SECTORS} canonical sector names, or null if "
            f"the question is too broad to filter sensibly (would need {MAX_SECTORS + 1}+ "
            "sectors). Names must come from the unified VALID_SECTORS list; "
            "the runner canonicalizes and drops anything outside the vocab."
        ),
    )
    rationale: str = Field(
        default="",
        description=(
            "Short Arabic justification for the pick (logged, not user-facing). "
            "Lets us audit why a particular sector set was chosen when a query "
            "regresses."
        ),
    )

    # ------------------------------------------------------------------
    # Validators — defensive parsing of LLM quirks (mirrors PlannerDecision)
    # ------------------------------------------------------------------

    @field_validator("sectors", mode="before")
    @classmethod
    def _coerce_sectors(cls, v):
        """LLM may serialize the list as a JSON string; coerce before list check."""
        if isinstance(v, str):
            stripped = v.strip()
            if stripped == "" or stripped.lower() in ("none", "null"):
                return None
            try:
                parsed = _json.loads(stripped)
                if isinstance(parsed, list):
                    return parsed
            except (_json.JSONDecodeError, TypeError):
                pass
        return v


__all__ = ["MIN_SECTORS", "MAX_SECTORS", "SectorPickerOutput"]
