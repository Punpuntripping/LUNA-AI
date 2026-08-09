"""The court slug map — ONE place where a URL segment meets a raw ``cases.court`` string.

`public.cases.court` is free text straight from the source feeds: **30 distinct
values on 30,531 rows**, where the same judicial body appears under many spellings
that differ only by CITY («… في مدينة جدة» / «… في مدينة الرياض» / «… في مدينة
الدمام»). This module is the digest: it collapses those 30 raw strings into **12
browsable buckets** and is the ONLY normalizer for court names in the codebase.

Two rules the user set (2026-08-08):
  * **City never distinguishes a bucket.** «لجان الفصل في الرياض» and «لجان الفصل
    في جدة» are one court.
  * **Tax type DOES distinguish** — ZATCA committees split into ضريبة القيمة
    المضافة vs ضريبة الدخل والزكاة — and so do ديوان المظالم's دوائر
    (تجارية / إدارية / جزائية).

⚠ **THE TAX SPLIT ONLY REACHES 54% OF THE TAX CORPUS.** 2,281 of the 4,966 ZATCA
rows carry court strings that name no tax type at all («اللجنة الابتدائية الأولى»,
«اللجنة الاستئنافية», «لجنة الفصل الضريبي/الزكوي»). There is nothing in the field
to split them on, so they land in ``اللجان-الضريبية-عام``. That residual is the
LARGEST tax bucket and holds the best content in the feed (1,687 rows with
citation mesh — more than VAT and income combined). Splitting it further means
classifying from the judgment body, which is an LLM pass, not a string rule.

ORDER IS MEANINGFUL. ``COURTS`` is ordered by corpus volume and that insertion
order IS the browse order — alphabetical would bury المحكمة التجارية (20,335 rows)
under المحكمة العامة (69).

SLUGS ARE ARABIC here, unlike :mod:`shared.library.sectors`. `library_sectors.md`
D4 says structural path segments are Latin, and that rule's justification was SEO
neutrality — but the whole `/judgments` wing is ``noindex`` behind the PDPL gate,
so there is no SEO to be neutral about. The user asked for Arabic explicitly
(«judgment/ديوان المظالم»). Reversing the decision means editing only the slug
keys below; nothing else in the stack reads the slug's language.

DRIFT BEHAVIOUR — deliberate, and the same contract as ``sectors.py``: a raw court
value that no bucket claims is LOGGED and simply has no section page. It never
raises at import time (a pipeline-side re-ingest must not be able to crash the
backend's boot) and the judgment stays reachable via the unfiltered `/judgments`
hub and via search. ``backend/tests/test_library_courts.py`` asserts the live
distinct set against this map, so drift is caught in CI.

Row counts in the comments were measured 2026-08-08 and are documentation only —
nothing reads them.
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# ─── Source feeds ─────────────────────────────────────────────────────────────
# The coarse axis. `legal_domains` and `date_gregorian` are populated PER FEED,
# not per row: every وزارة العدل row has both, every ديوان المظالم / ZATCA /
# insurance row has NEITHER. Anything that reasons about sector coverage or date
# ordering must branch on the feed, not on the individual court.
FEED_MOJ = "moj"                # وزارة العدل — 20,671 rows, sectors + dates
FEED_BOG = "board_of_grievances"  # ديوان المظالم — 4,669 rows, no sectors, NO citation mesh
FEED_ZATCA = "zatca"            # هيئة الزكاة والضريبة — 4,966 rows, no sectors, partial mesh
FEED_INSURANCE = "insurance"    # لجان التأمين — 225 rows, no sectors, NO citation mesh

# ─── The map ──────────────────────────────────────────────────────────────────
# slug → (Arabic label, raw `cases.court` variants, source feed)
# ORDERED BY CORPUS VOLUME. This order is the browse order.
_COURTS: dict[str, tuple[str, tuple[str, ...], str]] = {
    # 20,335
    "المحكمة-التجارية": (
        "المحكمة التجارية",
        ("التجارية", "التجارية الثالثة"),
        FEED_MOJ,
    ),
    # 2,625
    "ديوان-المظالم-تجارية": (
        "ديوان المظالم — الدائرة التجارية",
        ("ديوان المظالم — الدائرة التجارية",),
        FEED_BOG,
    ),
    # 2,281 — the untyped ZATCA residual. See the module docstring.
    "اللجان-الضريبية-عام": (
        "اللجان الضريبية والزكوية — عام",
        (
            "هيئة الزكاة والضريبة — اللجنة الاستئنافية الضريبية/الزكوية",
            "هيئة الزكاة والضريبة — اللجنة الابتدائية الأولى",
            "هيئة الزكاة والضريبة — اللجنة الاستئنافية",
            "هيئة الزكاة والضريبة — اللجنة الابتدائية الثانية",
            "هيئة الزكاة والضريبة — لجنة الفصل الضريبي/الزكوي",
            "هيئة الزكاة والضريبة — اللجنة الابتدائية الثالثة",
        ),
        FEED_ZATCA,
    ),
    # 1,879
    "ديوان-المظالم-إدارية": (
        "ديوان المظالم — الدائرة الإدارية",
        ("ديوان المظالم — الدائرة الإدارية",),
        FEED_BOG,
    ),
    # 1,622 — six variants differing only by city (جدة / الرياض / الدمام) + دائرة number.
    "لجان-ضريبة-القيمة-المضافة": (
        "لجان ضريبة القيمة المضافة",
        (
            "هيئة الزكاة والضريبة — الدائرة الأولى للفصل في مخالفات ومنازعات ضريبة القيمة المضافة في مدينة جدة",
            "هيئة الزكاة والضريبة — الدائرة الأولى للفصل في مخالفات ومنازعات ضريبة القيمة المضافة في مدينة الرياض",
            "هيئة الزكاة والضريبة — الدائرة الأولى للفصل في مخالفات ومنازعات ضريبة القيمة المضافة في مدينة الدمام",
            "هيئة الزكاة والضريبة — الدائرة الاستئنافية الأولى لمخالفات ومنازعات ضريبة القيمة المضافة والسلع الانتقائية في مدينة الرياض",
            "هيئة الزكاة والضريبة — الدائرة الثانية للفصل في مخالفات ومنازعات ضريبة القيمة المضافة في مدينة الرياض",
            "هيئة الزكاة والضريبة — الدائرة الثالثة للفصل في مخالفات ومنازعات ضريبة القيمة المضافة في مدينة الرياض",
        ),
        FEED_ZATCA,
    ),
    # 1,063 — income tax + the zakat/income appeal circuit (same subject matter).
    "لجان-ضريبة-الدخل-والزكاة": (
        "لجان ضريبة الدخل والزكاة",
        (
            "هيئة الزكاة والضريبة — الدائرة الأولى للفصل في مخالفات ومنازعات ضريبة الدخل في مدينة الرياض",
            "هيئة الزكاة والضريبة — الدائرة الأولى للفصل في مخالفات ومنازعات ضريبة الدخل في مدينة الدمام",
            "هيئة الزكاة والضريبة — الدائرة الثانية للفصل في مخالفات ومنازعات ضريبة الدخل في مدينة الرياض",
            "هيئة الزكاة والضريبة — الدائرة الأولى للفصل في مخالفات ومنازعات ضريبة الدخل في مدينة جدة",
            "هيئة الزكاة والضريبة — الدائرة الاستئنافية الأولى لمخالفات ومنازعات الزكاة وضريبة الدخل في مدينة الرياض",
            "هيئة الزكاة والضريبة — الدائرة الثالثة للفصل في مخالفات ومنازعات ضريبة الدخل في مدينة الرياض",
        ),
        FEED_ZATCA,
    ),
    # 225 — the whole insurance corpus. Separate from the tax لجان (user decision).
    "لجان-التأمين": (
        "لجان الفصل في المنازعات التأمينية",
        ("لجان الفصل في المنازعات والمخالفات التأمينية",),
        FEED_INSURANCE,
    ),
    # 165
    "ديوان-المظالم-جزائية": (
        "ديوان المظالم — الدائرة الجزائية",
        ("ديوان المظالم — الدائرة الجزائية",),
        FEED_BOG,
    ),
    # 125 — NOTE the second variant has TWO spaces after the dash. Copied verbatim.
    "المحكمة-العليا": (
        "المحكمة العليا",
        ("العليا", "العليا -  الهيئة الدائمة"),
        FEED_MOJ,
    ),
    # 106
    "محكمة-الاستئناف": ("محكمة الاستئناف", ("الاستئناف",), FEED_MOJ),
    # 69
    "المحكمة-العامة": ("المحكمة العامة", ("العامة",), FEED_MOJ),
    # 35 — the entire labour corpus. Shipped thin on purpose (user decision
    # 2026-08-08): the route is honest and stands as a visible marker that labour
    # judgments need SOURCING, which is a scraping job and not part of this work.
    "المحكمة-العمالية": ("المحكمة العمالية", ("العمالية",), FEED_MOJ),
}

# ─── Derived lookups ──────────────────────────────────────────────────────────
COURT_ORDER: list[str] = list(_COURTS)
"""The 12 court slugs in browse (volume) order."""

COURT_LABELS: dict[str, str] = {slug: label for slug, (label, _v, _f) in _COURTS.items()}
"""slug → Arabic display label. This is the H1 and the switcher label."""

COURT_VARIANTS: dict[str, tuple[str, ...]] = {
    slug: variants for slug, (_l, variants, _f) in _COURTS.items()
}
"""slug → the raw ``cases.court`` strings it claims. Feeds the ``in.()`` predicate."""

COURT_FEED: dict[str, str] = {slug: feed for slug, (_l, _v, feed) in _COURTS.items()}
"""slug → source feed. Branch on this for sector/date availability, never on the slug."""

COURT_SLUG_VOCAB: frozenset[str] = frozenset(_COURTS)
"""Closed slug vocabulary — validate against this BEFORE any DB work."""

# raw court string → slug. Built from the map so the two can never disagree.
_SLUG_BY_RAW: dict[str, str] = {
    raw: slug for slug, variants in COURT_VARIANTS.items() for raw in variants
}

# ─── Reserved segments ────────────────────────────────────────────────────────
# `/judgments/courts/page/{n}` must never resolve as a court, in either
# direction. Next resolves static segments first, but the BACKEND must refuse
# them too so the two namespaces can never collide. Same contract as
# ``sectors.RESERVED_SECTOR_SLUGS``.
RESERVED_COURT_SLUGS: frozenset[str] = frozenset({"page", "mine"})

# ─── Integrity check (log-and-continue, never raise) ──────────────────────────
_dupes = [
    raw
    for slug, variants in COURT_VARIANTS.items()
    for raw in variants
    if _SLUG_BY_RAW.get(raw) != slug
]
if _dupes:
    logger.error(
        "shared.library.courts: %d raw court value(s) are claimed by more than one "
        "bucket and will resolve to whichever was declared LAST: %s",
        len(_dupes),
        ", ".join(sorted(set(_dupes))),
    )
_reserved_collision = COURT_SLUG_VOCAB & RESERVED_COURT_SLUGS
if _reserved_collision:
    logger.error(
        "shared.library.courts: slug(s) collide with a reserved segment and will "
        "be unreachable: %s",
        ", ".join(sorted(_reserved_collision)),
    )


def court_for_slug(slug: Optional[str]) -> Optional[str]:
    """Arabic label for a court slug, or ``None`` if it is not one of the 12.

    Returns ``None`` for reserved segments even if a future edit added them to the
    map. Callers treat ``None`` as a 404 and MUST NOT issue a DB round-trip for it.
    """
    if not slug:
        return None
    slug = slug.strip()
    if slug in RESERVED_COURT_SLUGS:
        return None
    return COURT_LABELS.get(slug)


def variants_for_slug(slug: Optional[str]) -> Optional[tuple[str, ...]]:
    """Raw ``cases.court`` values a slug claims, or ``None`` if it is not a court.

    This tuple is what goes into the PostgREST ``in.()`` predicate. It is never a
    LIKE/regex: the vocabulary is closed and exact matching is what keeps the
    section's counts stable enough to stay OUT of the enumeration-oracle clamp.
    """
    if not slug:
        return None
    slug = slug.strip()
    if slug in RESERVED_COURT_SLUGS:
        return None
    return COURT_VARIANTS.get(slug)


def slug_for_court(raw_court: Optional[str]) -> Optional[str]:
    """Bucket slug for a raw ``cases.court`` string, or ``None`` if unclaimed.

    THE normalizer. Used to turn the court pill on a judgment card into a link and
    to bucket rows in the publish selector. An unclaimed value (today: the single
    empty-string row) renders as plain text rather than a broken link.
    """
    if not raw_court:
        return None
    return _SLUG_BY_RAW.get(raw_court.strip())


def feed_for_court(raw_court: Optional[str]) -> Optional[str]:
    """Source feed for a raw ``cases.court`` string, or ``None`` if unclaimed."""
    slug = slug_for_court(raw_court)
    return COURT_FEED.get(slug) if slug else None


def slugs_for_feed(feed: str) -> list[str]:
    """Court slugs belonging to one source feed, in browse order."""
    return [slug for slug in COURT_ORDER if COURT_FEED[slug] == feed]


def unclaimed_courts(raw_courts: Iterable[str]) -> list[str]:
    """Raw court values from the live corpus that no bucket claims.

    The CI drift check: pass ``select distinct court from cases`` and assert the
    result is the known-empty set. A new source feed shows up here first.
    """
    return sorted({c.strip() for c in raw_courts if c and c.strip() not in _SLUG_BY_RAW})


__all__ = [
    "FEED_MOJ",
    "FEED_BOG",
    "FEED_ZATCA",
    "FEED_INSURANCE",
    "COURT_ORDER",
    "COURT_LABELS",
    "COURT_VARIANTS",
    "COURT_FEED",
    "COURT_SLUG_VOCAB",
    "RESERVED_COURT_SLUGS",
    "court_for_slug",
    "variants_for_slug",
    "slug_for_court",
    "feed_for_court",
    "slugs_for_feed",
    "unclaimed_courts",
]
