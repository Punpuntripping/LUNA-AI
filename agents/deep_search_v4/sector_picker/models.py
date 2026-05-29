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

Sector-name validity is enforced **in the schema**, not the runner: ``sectors``
is typed ``list[Literal[*VALID_SECTORS]]``, so the 38 canonical names are part
of the output tool schema the model sees, and Pydantic rejects any list that
contains a name outside the vocabulary. The reject is **all-or-nothing** — a
single invalid name fails the whole output, which Pydantic AI turns into an
output-retry; if the model still cannot produce a fully-valid list within its
retry budget, the runner's exception handler degrades the call to ``None`` (run
unfiltered — the safe fallback).

To avoid burning retries on trivially-fixable near-misses, the ``sectors``
before-validator first normalizes each raw name via
:func:`agents.deep_search_v4.shared.sector_vocab.regulations.resolve_sector`
(exact → alias → substring). Only names that *still* do not resolve reach the
``Literal`` check and trip the all-or-nothing rejection.
"""
from __future__ import annotations

import json as _json
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from agents.deep_search_v4.shared.sector_vocab.regulations import (
    VALID_SECTORS,
    resolve_sector,
)


# Lower / upper bounds on the picker's sector list. Re-exported so the runner
# and tests share one source of truth.
MIN_SECTORS = 2
MAX_SECTORS = 5

# Dynamic Literal over the canonical vocabulary. Built from VALID_SECTORS so the
# enum can never drift from the single source of truth. ``list[SectorName]``
# makes Pydantic reject the entire list if any element is outside the 38 names.
SectorName = Literal[tuple(VALID_SECTORS)]  # type: ignore[valid-type]


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

    sectors: list[SectorName] | None = Field(
        default=None,
        description=(
            f"{MIN_SECTORS}-{MAX_SECTORS} canonical sector names, or null if "
            f"the question is too broad to filter sensibly (would need {MAX_SECTORS + 1}+ "
            "sectors). Every name MUST be one of the listed vocabulary values "
            "verbatim — a single invalid name rejects the whole output."
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
        """Coerce LLM quirks, then normalize each name before the Literal check.

        Two jobs, in order:

        1. Some models serialize the list as a JSON string (or the literal
           ``"null"``); coerce that to a real list / ``None`` first.
        2. Normalize each name via :func:`resolve_sector` (exact → alias →
           substring) and deduplicate. A name that resolves is rewritten to its
           canonical form so the ``Literal`` accepts it; a name that does *not*
           resolve is left unchanged so the ``Literal`` rejects it — and because
           one bad element fails the whole list, the rejection is all-or-nothing.
        """
        if isinstance(v, str):
            stripped = v.strip()
            if stripped == "" or stripped.lower() in ("none", "null"):
                return None
            try:
                parsed = _json.loads(stripped)
                if isinstance(parsed, list):
                    v = parsed
                else:
                    return v  # not a list — let list validation reject it
            except (_json.JSONDecodeError, TypeError):
                return v

        if v is None or not isinstance(v, list):
            return v

        normalized: list = []
        seen: set = set()
        for raw in v:
            if not isinstance(raw, str):
                normalized.append(raw)  # non-str — let the Literal reject it
                continue
            resolved = resolve_sector(raw)
            name = resolved if resolved is not None else raw.strip()
            if name and name not in seen:
                seen.add(name)
                normalized.append(name)
        return normalized


__all__ = ["MIN_SECTORS", "MAX_SECTORS", "SectorPickerOutput"]
