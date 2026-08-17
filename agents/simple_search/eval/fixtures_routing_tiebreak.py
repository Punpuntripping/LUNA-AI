"""Axis 2 — the TIE-BREAK OVER-FIRE watch set (added 2026-08-16, re-run lane).

WHY THIS SET EXISTS
-------------------
The router prompt gained a new line on 2026-08-16 (plan §1.1, "The tie-break —
when in doubt, deep_search"). It is the newest and riskiest of the three prompt
edits, because **its failure mode is invisible to an accuracy number**: if the
tie-break over-fires, everything routes ``deep_search``, every question still
gets answered, and the deep_search side of the eval still "passes" — while the
family this plan exists to build has quietly stopped being reachable.

So this set does NOT measure accuracy. It measures **`simple_search` share on
the cases where the two tests leave NOTHING in doubt.** Each anchor below is a
row §1.1 marks `simple_search` without qualification:

* two ``type 2`` anchors — a whole named نظام, wanted whole;
* two ``type 4`` anchors — one مادة by number, its TEXT wanted.

Every anchor is carried VERBATIM as paraphrase #1 so the measured shape is the
plan's own string, then ≥5 paraphrases hold the shape and vary the surface.

THE BARE-ARTICLE ANCHOR IS SCORED SEPARATELY
--------------------------------------------
«اعطيني نص المادة الخامسة» names no نظام. A ``ChatResponse`` asking *which* law
is a defensible answer to it and is NOT a tie-break over-fire — it is check 3 of
the router's own four checks. ``deep_search`` on it IS an over-fire. The two
outcomes therefore must not be summed, so the bare forms live in their own class
(``tb4_article_bare``) and the report prints ``chat`` and ``deep_search``
separately.
"""
from __future__ import annotations

from agents.simple_search.eval.fixtures_routing import RouteFixture

TIEBREAK_FIXTURES: list[RouteFixture] = [
    # ── ANCHOR 1 — §1.1 matrix row 1: «اش يقول نظام العمل» ──────────────────
    RouteFixture(
        "tb2-labor", "tb2_whole_named_law", "simple_search",
        (
            "اش يقول نظام العمل",                    # the plan's string, verbatim
            "وش يقول نظام العمل",
            "اش يقول نظام المرور",
            "وش يقول نظام الشركات",
            "اش يقول نظام التنفيذ",
            "اش يقول نظام المعاملات المدنية",
        ),
        why="The single least ambiguous shape in the family: a whole نظام, named, "
            "wanted whole, no qualifier of any kind. If the tie-break fires HERE "
            "it fires everywhere and the family is unreachable.",
    ),

    # ── ANCHOR 2 — §1.1 matrix row 2: the blessed overview ──────────────────
    RouteFixture(
        "tb2-overview", "tb2_whole_with_overview", "simple_search",
        (
            "اش يقول نظام المعاملات المدنية، اهم احكامه",   # the plan's string, verbatim
            "اش يقول نظام العمل، اهم احكامه",
            "اش يقول نظام الشركات، اهم احكامه",
            "اش يقول نظام المرور، اهم احكامه",
            "اش يقول نظام التنفيذ، اهم احكامه",
            "اش يقول نظام المعاملات المدنية واهم احكامه",
        ),
        why="§1.1 blesses «اهم احكامه» as an OVERVIEW of the whole document, not a "
            "narrowing. It is the row most likely to read as 'in doubt' to a model "
            "that has just been told to fall back to deep_search — the trailing "
            "clause LOOKS like a qualifier and is not one.",
    ),

    # ── ANCHOR 3 — §1.1 Test 2, identity side, law NAMED ───────────────────
    RouteFixture(
        "tb4-named", "tb4_article_named_law", "simple_search",
        (
            "اش هي المادة 67 من نظام التنفيذ",         # the plan's string, verbatim
            "اش هي المادة 81 من نظام العمل",
            "اش هي المادة 1 من نظام المعاملات المدنية",
            "اش هي المادة 74 من نظام العمل",
            "اش هي المادة 5 من نظام الشركات",
            "اش هي المادة 20 من نظام المرور",
        ),
        why="A مادة is one of the six addressable levels and the user wants its "
            "TEXT. Both tests pass cleanly, so nothing here is 'in doubt'. §1.1 "
            "also warns that a cited article number pulls toward deep_search — "
            "this anchor measures whether the tie-break amplified that pull past "
            "the identity side.",
    ),

    # ── ANCHOR 4 — the same identity request with NO law named ─────────────
    RouteFixture(
        "tb4-bare", "tb4_article_bare", "simple_search",
        (
            "اعطيني نص المادة الخامسة",                # the plan's string, verbatim
            "اعطيني نص المادة الخامسة من نظام العمل",
            "اعطيني نص المادة العاشرة",
            "ابغى نص المادة الخامسة",
            "نص المادة الخامسة من نظام الشركات",
            "اعطيني نص المادة الثلاثين من نظام المرور",
        ),
        why="SCORED SEPARATELY. Three of the six name no نظام, where a ChatResponse "
            "asking which law is defensible and is NOT an over-fire; deep_search IS. "
            "The three that name one are ordinary type-4 controls inside the same "
            "surface form, so the two can be compared inside one class.",
    ),
]


def total_calls() -> int:
    return sum(len(f.paraphrases) for f in TIEBREAK_FIXTURES)
