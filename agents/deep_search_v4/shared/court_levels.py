"""Canonical `cases.court_level` vocabulary — one home, no per-module copies.

`cases.court_level` has **three** values in prod (2026-07-24):

    first_instance  23,932
    appeal           6,474
    supreme            125   <-- the one every ad-hoc copy of this logic dropped

This module exists because that third value was silently collapsed in FOUR
independent places, each written as a two-branch ternary or a two-alternative
regex:

    case_search/unfold_ura.py   `"appeal" if raw == "appeal" else "first_instance"`
    case_search/reranker.py     `r"\\((ابتدائي|استئناف)\\)"` + two-value ternary
    case_search/search.py       `"استئناف" if lvl == "appeal" else "ابتدائي"`
    aggregator/preprocessor.py  (new Arabic label map)

Every supreme-court ruling was therefore labelled ابتدائي / `first_instance`
downstream. Import from here instead of writing a fourth copy — a two-branch
conditional over a three-value column is the bug, and it reappears every time
someone re-derives it locally.

See `.claude/plans/case_topics_loop.md` §8 (decision D5: court level is
informational only — it is never a retrieval boost or a relevance signal).
"""
from __future__ import annotations

# Canonical enum values, ordered by judicial seniority (ascending).
CASE_COURT_LEVELS: tuple[str, ...] = ("first_instance", "appeal", "supreme")

# Fallback for NULL / empty / unrecognised input. `cases.court_level` is NOT
# NULL in the schema, so this only fires on a hand-built row or a future value.
DEFAULT_COURT_LEVEL = "first_instance"

# enum -> Arabic display label (prompts, reranker markdown, aggregator refs).
COURT_LEVEL_AR: dict[str, str] = {
    "first_instance": "ابتدائي",
    "appeal": "استئناف",
    "supreme": "عليا",
}

# Arabic label -> enum. Used when parsing a rendered header back to the enum
# (the legacy markdown reranker path). Keys are exactly COURT_LEVEL_AR values.
COURT_LEVEL_FROM_AR: dict[str, str] = {ar: en for en, ar in COURT_LEVEL_AR.items()}


def normalize_court_level(raw: str | None) -> str:
    """Coerce a raw `cases.court_level` value to one of :data:`CASE_COURT_LEVELS`.

    Unknown / NULL / empty input returns :data:`DEFAULT_COURT_LEVEL`. Never
    raises — this sits on the retrieval hot path and a surprise value must not
    take down a search.
    """
    value = (raw or "").strip()
    return value if value in CASE_COURT_LEVELS else DEFAULT_COURT_LEVEL


def court_level_ar(raw: str | None, *, strict: bool = False) -> str:
    """Arabic label for a court level.

    Args:
        raw: the enum value (or None).
        strict: when True, an unrecognised value yields ``""`` instead of the
            default label. Use this for **display** paths, where silently
            printing ابتدائي for an unknown level would assert something false;
            leave it False on paths that need a label no matter what.
    """
    value = (raw or "").strip()
    if value in COURT_LEVEL_AR:
        return COURT_LEVEL_AR[value]
    return "" if strict else COURT_LEVEL_AR[DEFAULT_COURT_LEVEL]


__all__ = [
    "CASE_COURT_LEVELS",
    "COURT_LEVEL_AR",
    "COURT_LEVEL_FROM_AR",
    "DEFAULT_COURT_LEVEL",
    "court_level_ar",
    "normalize_court_level",
]
