"""The sector slug map — ONE place where a Latin URL segment meets an Arabic sector.

`library_sectors.md` D4/D5: structural path segments are Latin, document
identifiers stay Arabic, and the Latin form is an ENGLISH TRANSLATION, never a
transliteration (`commercial-transactions`, never `almuamalat-altijariya`).
The display name is always the Arabic one (D6) — that is where the SEO weight
lives; the slug only has to be stable and readable.

The Arabic names are IMPORTED from the vocabulary that owns them
(``agents.deep_search_v4.shared.sector_vocab.unified.VALID_SECTORS``) and never
retyped — the same rule ``public_library.py`` sets for every other closed
vocabulary (``_DOC_TYPE_VOCAB``, ``_COURT_LEVEL_VOCAB``). This module pairs that
list with Latin slugs and nothing else.

ORDER IS MEANINGFUL. ``SECTOR_SLUGS`` is ordered by corpus volume, not
alphabetically, and that insertion order IS the browse-grid order (plan §3/§7.2):
alphabetical would bury المعاملات التجارية (20k items) under الأمن الغذائي (753).

DRIFT BEHAVIOUR — deliberate: if ``VALID_SECTORS`` gains an entry that has no
slug here, this module logs an error and simply omits it. It does NOT raise at
import time. A sector added on the agents side (a pipeline concern) must not be
able to crash the backend's boot; the un-slugged sector just has no public page
until someone adds one. ``backend/tests/test_library_sectors.py`` asserts exact
equality, so the drift is caught in CI long before it reaches prod.
"""
from __future__ import annotations

import logging

from agents.deep_search_v4.shared.sector_vocab.unified import VALID_SECTORS

logger = logging.getLogger(__name__)

# ─── The map ──────────────────────────────────────────────────────────────────
# name_ar → Latin slug, ORDERED BY TOTAL CORPUS VOLUME (plan §3 table).
# Five slugs marked ⚠ in the plan are awaiting sign-off on the English wording;
# a change is a one-row `topics` update plus a 301 while the corpus is young.
_SLUG_BY_NAME: dict[str, str] = {
    "المعاملات التجارية": "commercial-transactions",
    "حوكمة الشركات والاستثمار": "corporate-governance-investment",
    "القضاء والمحاكم": "judiciary-courts",
    "المالية والضرائب": "finance-tax",
    "العقار": "real-estate",
    "الإسكان": "housing",
    "النقل": "transport",
    "المهن المرخصة": "licensed-professions",
    "العمل والتوظيف": "labor-employment",
    "الصحة": "health",
    "تقنية المعلومات والأمن السيبراني": "it-cybersecurity",
    "البلديات والتخطيط العمراني": "municipalities-urban-planning",
    "المواصفات والمقاييس": "standards-metrology",          # ⚠ alt: standards-measurement
    "الأمن الغذائي": "food-security",
    "التعليم": "education",
    "الزراعة": "agriculture",
    "المياه والبيئة": "water-environment",
    "التأمين": "insurance",
    "الصناعة والتعدين": "industry-mining",
    "الحوكمة": "governance",                                # ⚠ confusable with #2
    "الجمارك والتجارة الدولية": "customs-international-trade",
    "الجنايات والجرائم": "criminal-offenses",               # ⚠ alt: criminal-law
    "الملكية الفكرية": "intellectual-property",
    "السياحة والترفيه": "tourism-entertainment",
    "الثقافة والإعلام": "culture-media",
    "التنمية الاجتماعية": "social-development",
    "التعاملات والأحوال المدنية": "civil-transactions-status",  # ⚠ alt: civil-affairs
    "الطاقة": "energy",
    "الأمن والدفاع": "security-defense",
    "الاتصالات والفضاء": "telecom-space",
    "الرقابة": "oversight",                                 # ⚠ alts: regulatory-oversight, audit
    "الحج والعمرة": "hajj-umrah",
    "البحث والابتكار": "research-innovation",
    "المنظمات غير الربحية": "nonprofits",
    "الشؤون الإسلامية والأوقاف": "islamic-affairs-endowments",
    "الشؤون الخارجية": "foreign-affairs",
    "الرياضة": "sports",
    "حقوق الإنسان": "human-rights",
}

# ─── Reconciliation against the owning vocabulary ─────────────────────────────
_VOCAB = frozenset(VALID_SECTORS)
_unslugged = [name for name in VALID_SECTORS if name not in _SLUG_BY_NAME]
_unknown = [name for name in _SLUG_BY_NAME if name not in _VOCAB]

if _unslugged:
    logger.error(
        "shared.library.sectors: %d sector(s) in VALID_SECTORS have no slug and "
        "will have NO public page: %s — add them to _SLUG_BY_NAME.",
        len(_unslugged),
        ", ".join(_unslugged),
    )
if _unknown:
    logger.error(
        "shared.library.sectors: %d slugged name(s) are NOT in VALID_SECTORS and "
        "will match zero rows: %s — the vocabulary is the source of truth.",
        len(_unknown),
        ", ".join(_unknown),
    )

# Only names that exist in BOTH are exposed. Volume order is preserved.
SECTOR_SLUGS: dict[str, str] = {
    name: slug for name, slug in _SLUG_BY_NAME.items() if name in _VOCAB
}
"""name_ar → slug, ordered by corpus volume. This order is the browse order."""

SLUG_TO_SECTOR: dict[str, str] = {slug: name for name, slug in SECTOR_SLUGS.items()}
"""slug → name_ar. Reverse of :data:`SECTOR_SLUGS`, same order."""

SECTOR_ORDER: list[str] = list(SECTOR_SLUGS)
"""The 38 Arabic sector names in browse (volume) order."""

SECTOR_SLUG_VOCAB: frozenset[str] = frozenset(SLUG_TO_SECTOR)
"""Closed slug vocabulary — validate against this BEFORE any DB work (§5)."""

# ─── Reserved segments ────────────────────────────────────────────────────────
# T2: `/library/mine` is the authed shelf and `/library/page/{n}` is the deep
# paginator for the unfiltered hub. Neither may ever be resolvable as a sector,
# in either direction — Next resolves static segments first, but the BACKEND
# must refuse them too so the two namespaces can never collide.
RESERVED_SECTOR_SLUGS: frozenset[str] = frozenset({"mine", "page"})


def sector_for_slug(slug: str | None) -> str | None:
    """Arabic sector name for a Latin slug, or ``None`` if it is not one of the 38.

    Returns ``None`` for reserved segments (``mine`` / ``page``) even if a future
    edit were to add them to the map. Callers treat ``None`` as a 404 and MUST
    NOT issue a DB round-trip for it (§12.7).
    """
    if not slug:
        return None
    slug = slug.strip().lower()
    if slug in RESERVED_SECTOR_SLUGS:
        return None
    return SLUG_TO_SECTOR.get(slug)


def slug_for_sector(name_ar: str | None) -> str | None:
    """Latin slug for an Arabic sector name, or ``None`` if it is not one of the 38.

    Used to turn the sector pills on the corpus cards into links (D11) — an
    unrecognised value renders as plain text rather than a broken link.
    """
    if not name_ar:
        return None
    return SECTOR_SLUGS.get(name_ar.strip())


__all__ = [
    "SECTOR_SLUGS",
    "SLUG_TO_SECTOR",
    "SECTOR_ORDER",
    "SECTOR_SLUG_VOCAB",
    "RESERVED_SECTOR_SLUGS",
    "sector_for_slug",
    "slug_for_sector",
]
