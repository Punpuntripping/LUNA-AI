"""The entity slug map — ONE place where a URL segment meets a ``provider_name``.

`public.services.provider_name` is the issuing government body of a خدمة, and it
is the browse axis of `/compliance`: **28 distinct values over the 337 published
service guides**, verified live 2026-08-22. This module pairs each one with a
Latin URL segment and is the ONLY place that pairing exists on the Python side.

WHY THIS IS A THINNER MODULE THAN ``courts.py``, AND MUST STAY THINNER
----------------------------------------------------------------------
``cases.court`` is free text — 30 raw strings collapsing to 12 buckets, where the
same judicial body appears under several spellings that differ only by CITY — so
``courts.py`` had to BE the normalizer and its query predicate is ``in.(variants)``.

``services.provider_name`` is **already canonical**: 28 strings, one per body, no
city suffixes, no numbered دوائر, no residual bucket, zero NULLs, zero
near-duplicate spellings (all verified live). So this is a slug ⇄ SINGLE-NAME map
and the predicate is ``eq``, not ``in``. There is deliberately **no variant list
and no ``variants_for_slug``** — adding one later would mean the corpus had
started spelling one body two ways, which is a pipeline problem to fix upstream,
not a bucket to grow here.

That exactness is load-bearing beyond tidiness: a SECTION is exact by
construction or its counts stop being fixed, and fixed counts are the whole
reason the entity axis is allowed to report real ``total_pages`` to an anonymous
caller instead of the flat enumeration ceiling
(``.claude/plans/compliance_entity_sections.md`` §2/D1).

SLUGS ARE LATIN kebab-case, unlike ``courts.py``
------------------------------------------------
``library_sectors.md`` D4 says structural path segments are Latin. The
justification that let ``courts.py`` go Arabic — «the whole /judgments wing is
``noindex`` behind the PDPL gate, so there is no SEO to be neutral about» — is
exactly INVERTED here: /compliance is the indexed wing, 100% published and
ungated, with all 337 guide URLs already in the sitemap. Latin also removes the
percent-encoding trap that runs through every entry point of the courts axis and
through ISR revalidation (memory `isr-revalidate-encoding`).

⚠ THESE SLUGS ARE PERMANENT. They become indexable URLs the moment the sitemap
ships them, so the same "never rewrite an existing slug" rule that governs
``build_seo_slugs.py`` governs this table. A rename is a 301, not an edit.

ORDER IS MEANINGFUL. ``_ENTITIES`` is ordered by corpus volume and that insertion
order IS the browse order (``ENTITY_ORDER``) — alphabetical would bury وزارة
العدل (115 guides) somewhere in the middle of nine one-guide authorities.
**Never re-sort it**, here or on the client.

DRIFT BEHAVIOUR — deliberate, and the same contract as ``courts.py`` /
``sectors.py``: a ``provider_name`` that no slug claims is LOGGED and simply has
no section page. It never raises at import time — a pipeline-side re-ingest of
``services`` must not be able to crash the backend's boot — and its guides stay
reachable through the unfiltered `/compliance` hub, the sitemap and search.
``backend/tests/test_library_entities.py`` asserts the live distinct set against
this map, so drift is caught before it reaches prod.

Guide counts in the comments were measured 2026-08-22 and are documentation only
— nothing reads them. The counts the browse grid prints come from the corpus.
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# ─── The map ──────────────────────────────────────────────────────────────────
# Latin slug → the exact ``services.provider_name`` string it claims.
# ORDERED BY CORPUS VOLUME. This order is the browse order.
_ENTITIES: dict[str, str] = {
    "ministry-of-justice": "وزارة العدل",                                        # 115
    "ministry-of-human-resources": "وزارة الموارد البشرية والتنمية الاجتماعية",   # 53
    "ministry-of-commerce": "وزارة التجارة",                                     # 43
    "gosi": "المؤسسة العامة للتأمينات الاجتماعية",                                # 19
    "ministry-of-health": "وزارة الصحة",                                         # 17
    "zatca": "هيئة الزكاة والضريبة والجمارك",                                     # 14
    "ministry-of-education": "وزارة التعليم",                                    # 10
    "hrdf": "صندوق تنمية الموارد البشرية",                                        # 8
    "ministry-of-environment": "وزارة البيئة والمياه والزراعة",                    # 8
    "media-regulation-authority": "الهيئة العامة لتنظيم الإعلام",                  # 7
    "ministry-of-foreign-affairs": "وزارة الخارجية",                              # 7
    "etimad": "المركز الوطني لنظم الموارد الحكومية",                               # 6
    "social-development-bank": "بنك التنمية الاجتماعية",                          # 5
    "human-rights-commission": "هيئة حقوق الإنسان",                               # 4
    "ministry-of-municipalities-housing": "وزارة البلديات والإسكان",               # 4
    "ministry-of-industry": "وزارة الصناعة والثروة المعدنية",                      # 2
    "ministry-of-tourism": "وزارة السياحة",                                       # 2
    "environmental-compliance-center": "المركز الوطني للرقابة على الإلتزام البيئي",  # 2
    "transport-general-authority": "الهيئة العامة للنقل",                          # 2
    # ── The nine one-guide authorities. ALL 28 SHIP (plan D3). ──────────────
    # The المحكمة العمالية precedent from the judgments wing applies: the route
    # is honest, costs nothing, and a browse grid that silently omits a body
    # whose name is printed on the cards is worse than a thin page.
    "real-estate-general-authority": "الهيئة العامة للعقار",                       # 1
    "awqaf-general-authority": "الهيئة العامة للأوقاف",                            # 1
    # ⚠ THE FATHA ON ق IS REAL AND LOAD-BEARING: the live value is
    # «للمقَيّمين» (U+064E after ق, U+0651 after ي), not «للمقيّمين». The predicate is an
    # exact ``eq``, so dropping the harakah matches ZERO rows and the section
    # silently renders empty. Verified live 2026-08-23 — the plan's §3 table had
    # it retyped without the fatha, which is exactly the class of bug
    # ``courts.py`` warns about (its row 9 differs from its neighbour by an
    # invisible double space). NEVER retype an entity name; copy it from the
    # corpus.
    "taqeem": "الهيئة السعودية للمقَيّمين المعتمدين (تقييم)",  # 1
    "saudi-business-center": "المركز السعودي للأعمال الاقتصادية",                   # 1
    "ministry-of-finance": "وزارة المالية",                                        # 1
    "royal-commission-alula": "الهيئة الملكية لمحافظة العلا",                       # 1
    "monshaat": "الهيئة العامة للمنشآت الصغيرة والمتوسطة",                          # 1
    "tvtc": "المؤسسة العامة للتدريب التقني والمهني",                                # 1
    "sfda": "الهيئة العامة للغذاء والدواء",                                         # 1
}

# ─── Derived lookups ──────────────────────────────────────────────────────────
ENTITY_ORDER: list[str] = list(_ENTITIES)
"""The 28 entity slugs in browse (corpus-volume) order. THE SERVER OWNS THIS
ORDER — the browse grid renders it as given and must not re-sort."""

ENTITY_LABELS: dict[str, str] = dict(_ENTITIES)
"""slug → the exact ``provider_name``. This doubles as the display label: the
Arabic name is both what the corpus stores and what the H1 prints, so there is no
second display string to drift (the ``courts.py`` split between a bucket label
and its raw variants has no counterpart here — see the module docstring)."""

ENTITY_SLUG_VOCAB: frozenset[str] = frozenset(_ENTITIES)
"""Closed slug vocabulary — validate against this BEFORE any DB work."""

# provider_name → slug. Built FROM the map so the two can never disagree.
_SLUG_BY_NAME: dict[str, str] = {name: slug for slug, name in _ENTITIES.items()}

# ─── Reserved segments ────────────────────────────────────────────────────────
# `/compliance/{slug}` is a SHARED NAMESPACE (plan D2): one dynamic segment
# serving both entity sections and guide pages. So `/compliance/page/{n}` (the
# unfiltered deep paginator), `/compliance/entities` and `/compliance/mine` must
# never resolve as an entity, in either direction. Next resolves static segments
# first, but the BACKEND must refuse them too so the two namespaces cannot
# collide — the ``courts.RESERVED_COURT_SLUGS`` contract, plus ``entities``
# because this wing exposes its browse list at that literal path.
RESERVED_SLUGS: frozenset[str] = frozenset({"page", "entities", "mine"})

# ─── Integrity checks (log-and-continue, NEVER raise) ─────────────────────────
# An import-time raise here would let a pipeline re-ingest of ``services`` crash
# the backend's boot. Every failure below costs at most one section page.
_dupe_names = [
    name
    for slug, name in _ENTITIES.items()
    if _SLUG_BY_NAME.get(name) != slug
]
if _dupe_names:
    logger.error(
        "shared.library.entities: %d provider_name(s) are claimed by more than one "
        "slug and will resolve to whichever was declared LAST: %s",
        len(_dupe_names),
        ", ".join(sorted(set(_dupe_names))),
    )
_reserved_collision = ENTITY_SLUG_VOCAB & RESERVED_SLUGS
if _reserved_collision:
    logger.error(
        "shared.library.entities: slug(s) collide with a reserved segment and will "
        "be unreachable: %s",
        ", ".join(sorted(_reserved_collision)),
    )


def name_for_slug(slug: Optional[str]) -> Optional[str]:
    """The exact ``provider_name`` an entity slug claims, or ``None``.

    THE PREDICATE VALUE, not a display-only label: what comes back goes straight
    into an ``eq`` comparison against ``provider_name``. It is never a LIKE, a
    substring or a pattern — the vocabulary is closed and exact matching is what
    keeps a section's counts fixed enough to stay OUT of the enumeration-oracle
    clamp (plan §2/D1). The free-text ``provider`` facet on the same wing IS a
    substring; the two are different axes and must not be merged.

    Returns ``None`` for a RESERVED segment (``page`` / ``entities`` / ``mine``)
    even if a future edit added one to the map, so `/compliance/page/2` can never
    resolve as an entity in either namespace (the ``courts.court_for_slug`` rule,
    copied). Callers treat ``None`` as a refusal and MUST NOT issue a DB
    round-trip for it.
    """
    if not slug:
        return None
    slug = slug.strip().lower()
    if slug in RESERVED_SLUGS:
        return None
    return ENTITY_LABELS.get(slug)


def slug_for_name(provider_name: Optional[str]) -> Optional[str]:
    """Entity slug for a raw ``provider_name``, or ``None`` if unclaimed.

    Used to turn the provider line printed on a /compliance card into a link —
    the card is where a reader learns the entity's name, so it is where the axis
    should be discoverable. An unclaimed value (none live today) renders as plain
    text rather than a broken link.
    """
    if not provider_name:
        return None
    return _SLUG_BY_NAME.get(provider_name.strip())


def unclaimed_entities(provider_names: Iterable[str]) -> list[str]:
    """Live ``provider_name`` values that no slug claims.

    The CI drift check: pass ``select distinct provider_name from services`` (or
    from the published guide view) and assert the result is empty. A new issuing
    body added by an ingest shows up here first, and its guides keep working —
    they just have no section page until someone adds a slug.
    """
    return sorted(
        {n.strip() for n in provider_names if n and n.strip() not in _SLUG_BY_NAME}
    )


__all__ = [
    "ENTITY_ORDER",
    "ENTITY_LABELS",
    "ENTITY_SLUG_VOCAB",
    "RESERVED_SLUGS",
    "name_for_slug",
    "slug_for_name",
    "unclaimed_entities",
]
