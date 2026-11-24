"""Confidence — decided by code from checkable facts (plan §4.3). Pure.

The model never states how sure it is. Every level below follows from facts a
lookup returned: was the title an exact match, how much of the asked name does the
title cover, how many articles hold the phrase, did the model reword the request.

The gate is always title COVERAGE, never the BM25 score (a wrong answer can score
higher than a right one — see ``fetch_article.py``'s calibration table).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

#: A title must cover this share of the asked name to be a best guess.
MIN_COVERAGE = 0.60
#: The best guess must lead the next candidate by more than this.
CLEAR_MARGIN = 0.10
#: A phrase held by more articles than this is too broad to return.
MAX_PHRASE_ARTICLES = 5

HIGH, MEDIUM, LOW = "high", "medium", "low"

#: «نظام المرافعات» for «نظام المرافعات الشرعية» — the caller dropped the last words.
PREFIX_SHORT = "asked_opens_title"
#: «نظام العمل السعودي» for «نظام العمل» — the caller added words.
PREFIX_LONG = "title_opens_asked"

#: Law statuses whose text is the law in force.
IN_FORCE = frozenset({"in_force", "in_force_amended"})


@dataclass(frozen=True)
class TitleCandidate:
    """One title a lookup found, with the two facts the gate reads."""

    id: str
    title: str
    exact: bool = False          # an exact normalized-title match
    coverage: float = 0.0        # share of the asked name's words present in the title
    recall_only: bool = False    # found by a rung that only shares one word — never stands alone
    label: str = ""              # a type word shown beside the title («لائحة تنفيذية»)
    extra: dict = field(default_factory=dict)
    relevance: float | None = None   # coverage balanced against how much of the TITLE the name explains
    prefix: str = ""             # PREFIX_SHORT: the asked name opens the title · PREFIX_LONG: the title opens the asked name


@dataclass(frozen=True)
class GateResult:
    """``state``: exact · best · several · none."""

    state: str
    winner: TitleCandidate | None = None
    shown: tuple[TitleCandidate, ...] = ()


def title_gate(candidates: Sequence[TitleCandidate], *, limit: int = 3) -> GateResult:
    """Pick from RANKED candidates.

    - one exact title → ``exact``;
    - several exact titles → ``several`` (identical titles; the caller may still
      break the tie on a hard fact such as "only one is in force");
    - no exact title, but the asked name is the OPENING of exactly one title
      («نظام التأمينات» → «نظام التأمينات الاجتماعية»), or a title is the opening of
      the asked name («نظام العمل السعودي» → «نظام العمل») → ``best``; the opening of
      several titles («نظام المرافعات») → ``several``;
    - otherwise one candidate clearly ahead → ``best``. "Ahead" is measured on
      relevance when the lookup gives it — a long title that merely contains the
      asked words («نظام المرافعات أمام ديوان المظالم» for «نظام ديوان المظالم») covers
      them all and is still not that law;
    - otherwise ``several`` when anything clears the floor, else ``none`` (the
      nearest titles are still returned as hints for a reworded attempt).
    """
    pool = list(candidates)
    if not pool:
        return GateResult("none")
    exact = [c for c in pool if c.exact]
    if len(exact) == 1:
        return GateResult("exact", exact[0], (exact[0],))
    if len(exact) > 1:
        return GateResult("several", None, tuple(exact[:limit]))
    longer = [c for c in pool if c.prefix == PREFIX_LONG and not c.recall_only]
    if longer:
        best = max(longer, key=lambda c: len(c.title))
        return GateResult("best", best, (best,))
    shorter = [c for c in pool if c.prefix == PREFIX_SHORT and not c.recall_only]
    if len(shorter) == 1:
        return GateResult("best", shorter[0], (shorter[0],))
    if len(shorter) > 1:
        return GateResult("several", None, tuple(shorter[:limit]))

    def strength(c: TitleCandidate) -> float:
        return c.coverage if c.relevance is None else c.relevance

    above = sorted(
        (c for c in pool if c.coverage >= MIN_COVERAGE and strength(c) >= MIN_COVERAGE and not c.recall_only),
        key=strength,
        reverse=True,
    )
    if len(above) == 1 or (len(above) > 1 and strength(above[0]) - strength(above[1]) > CLEAR_MARGIN):
        return GateResult("best", above[0], tuple(above[:limit]))
    if above:
        return GateResult("several", None, tuple(above[:limit]))
    return GateResult("none", None, tuple(pool[:limit]))


def article_confidence(*, title_state: str, as_asked: bool, in_force: bool = True) -> str | None:
    """An article found by number inside a law.

    high — the law's title matched exactly, the name is the one the request wrote,
    and the law is in force. medium — a best-guess title, a name the model
    reworded or picked from a list, or a text that is a draft / cancelled.
    ``None`` — the law itself is not settled, so there is no text to return.
    """
    if title_state == "exact":
        return HIGH if as_asked and in_force else MEDIUM
    if title_state == "best":
        return MEDIUM
    return None


def phrase_confidence(
    *,
    title_state: str,
    as_asked: bool,
    whole_hits: int,
    definition_hits: int = 0,
    is_definition: bool = False,
    phrase_as_asked: bool = True,
    in_force: bool = True,
) -> str | None:
    """A word or phrase found inside a law (Mode B).

    high — exact title, and the phrase sits in exactly one article (or, for a
    definition request, exactly one article defines it), with nothing reworded.
    medium — 2 to 5 articles hold it, or it was found only after rewording, or the
    title is a best guess. low — more than 5 articles, or only as part of a longer word.
    """
    if title_state not in ("exact", "best"):
        return None
    if whole_hits == 0:
        return LOW
    sure_title = title_state == "exact" and as_asked and in_force
    single = whole_hits == 1 or (is_definition and definition_hits == 1)
    if single:
        return HIGH if sure_title and phrase_as_asked else MEDIUM
    if whole_hits <= MAX_PHRASE_ARTICLES:
        return MEDIUM
    return LOW


def document_confidence(*, title_state: str, as_asked: bool) -> str | None:
    """A circular or a service found by its title / name.

    high — the exact title as the request wrote it. medium — one clear best by
    coverage, or an exact title the model arrived at by rewording.
    """
    if title_state == "exact":
        return HIGH if as_asked else MEDIUM
    if title_state == "best":
        return MEDIUM
    return None


__all__ = [
    "CLEAR_MARGIN",
    "GateResult",
    "HIGH",
    "IN_FORCE",
    "LOW",
    "MAX_PHRASE_ARTICLES",
    "MEDIUM",
    "MIN_COVERAGE",
    "PREFIX_LONG",
    "PREFIX_SHORT",
    "TitleCandidate",
    "article_confidence",
    "document_confidence",
    "phrase_confidence",
    "title_gate",
]
