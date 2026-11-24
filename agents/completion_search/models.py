"""Types of the Completion Search helper (``.claude/plans/completion_search.md`` §4.2).

Two families, kept apart on purpose:

- what the MODEL may write (``HelperDecision``) — search labels, handles, a short note.
  It has no field that can carry legal text: the model only *selects* handles;
- what CODE produces (``Pin``, ``SearchOutcome``, ``CompletionResult``) — the opened
  official text, its confidence and its identity.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Confidence = Literal["high", "medium", "low"]
SearchStatus = Literal["found", "candidates", "not_in_library", "not_found"]
SourceType = Literal["article", "circular", "service", "case", "principle"]
Caller = Literal["deep_planner", "writer_planner", "attachment"]

#: A request holds at most this many searches (plan §8 Q2).
MAX_SEARCHES = 10
#: At most this many attempts per search (owner decision C5).
MAX_ROUNDS = 3
#: A low-confidence answer lists at most this many titles, with no text.
MAX_CANDIDATE_TITLES = 3

_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}


def lowest(levels: list[str]) -> str | None:
    """The weakest of several confidence levels; ``None`` for an empty list."""
    return min(levels, key=lambda c: _CONFIDENCE_RANK.get(c, 0)) if levels else None


# --------------------------------------------------------------------------- #
# What the model writes
# --------------------------------------------------------------------------- #


class SearchDecision(BaseModel):
    """The model's verdict on ONE search. Handles only — never the text itself."""

    search_id: str = Field(max_length=8, description="The id you used in the tool calls, e.g. S1.")
    asked: str = Field(
        max_length=200,
        description="What this search was looking for, in the caller's own words.",
    )
    selected: list[str] = Field(
        default_factory=list,
        max_length=12,
        description="Handles the tools returned (T1, T2 … for texts; L1 … for a law). Never anything else.",
    )
    status: SearchStatus = Field(
        description=(
            "found = you selected text handles · candidates = several possible, you selected law "
            "handles or nothing · not_in_library = the law is not in the library · not_found = nothing matched."
        )
    )
    note: str = Field("", max_length=200, description="One short line. Never quote or paraphrase legal text.")


class HelperDecision(BaseModel):
    """The model's whole answer: one entry per search."""

    searches: list[SearchDecision] = Field(max_length=MAX_SEARCHES)


# --------------------------------------------------------------------------- #
# What code produces
# --------------------------------------------------------------------------- #


class Pin(BaseModel):
    """One official text, opened by code."""

    key: str                          # identity — "article:<id>", "circular:<id>" …
    source_type: SourceType
    title: str                        # «المادة 56 من نظام المعاملات المدنية»
    source_title: str = ""            # the law, the issuing body, the court
    text: str                         # the official wording, in full
    confidence: Confidence
    note: str = ""                    # e.g. "a second version of this law exists"
    regulation_id: str = ""
    article_id: str = ""
    article_number: str = ""
    circular_id: str = ""
    service_id: str = ""
    case_id: str = ""
    principle_no: str = ""


class SearchOutcome(BaseModel):
    """One search, finished."""

    search_id: str
    asked: str = ""
    status: SearchStatus
    confidence: Confidence | None = None
    pins: list[Pin] = Field(default_factory=list)
    candidates: list[str] = Field(default_factory=list)   # titles only
    note: str = ""
    rounds: int = 0


class CompletionResult(BaseModel):
    """What the runner hands back to the caller's tool."""

    request: str
    searches: list[SearchOutcome] = Field(default_factory=list)
    model_used: bool = False          # False = answered by code alone
    error: str = ""

    def pins(self, *levels: str) -> list[Pin]:
        """Every pin, or only those at the given confidence levels."""
        wanted = set(levels)
        return [p for s in self.searches for p in s.pins if not wanted or p.confidence in wanted]


__all__ = [
    "Caller",
    "CompletionResult",
    "Confidence",
    "HelperDecision",
    "MAX_CANDIDATE_TITLES",
    "MAX_ROUNDS",
    "MAX_SEARCHES",
    "Pin",
    "SearchDecision",
    "SearchOutcome",
    "SearchStatus",
    "SourceType",
    "lowest",
]
