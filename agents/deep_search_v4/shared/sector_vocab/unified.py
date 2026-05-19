"""Unified sector vocabulary for the deep-search v4 executors.

Single source of truth for ``VALID_SECTORS`` — the Saudi ministry-sector
taxonomy shared by all three executor corpora and the v4 planner:

- ``regulations_v2.sectors[]``  (reg_search)   — 38 sectors
- ``services.sectors[]``        (compliance)   — 38 sectors
- ``cases.legal_domains[]``     (case_search)  — 36 of the 38 (two sectors
  carry zero cases, so they never appear in the cases corpus)

Verified against the live Supabase instance (2026-05-17): ``regulations_v2``
and ``services`` carry the identical 38-entry set; ``cases`` is a 36-entry
subset. The 38-entry list below is the canonical vocabulary — every executor
and the planner draw their sector list from here.

The submodules :mod:`.regulations` and :mod:`.cases`, plus the legacy paths
``reg_search.sector_vocab`` / ``case_search.sector_vocab``, are now thin
re-exports of this module — kept only for import-path backward compatibility.

The core failure mode this prevents:
    LLM emits   "السياحة" / "الترفيه"     (split, colloquial)
    DB stores   "السياحة والترفيه"        (one combined entry)
    PostgreSQL `&&` operator is exact-match → both silently dropped → the
    sector filter excludes ALL rows → 0 results with no error.

canonicalize_sectors() uses bidirectional substring matching: if the raw name
is a substring of a canonical entry (or vice versa), map to the shortest
match. difflib character similarity is unsafe for Arabic — shared prefixes
(ال) and letter patterns give false positives.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ─── Canonical sector list ────────────────────────────────────────────────────
# The unified ministry-sector vocabulary. Must match exactly the values stored
# in regulations_v2.sectors[], services.sectors[] and cases.legal_domains[].
# Sorted alphabetically for readability. 38 entries.

VALID_SECTORS: list[str] = [
    "الأمن الغذائي",
    "الأمن والدفاع",
    "الإسكان",
    "الاتصالات والفضاء",
    "البحث والابتكار",
    "البلديات والتخطيط العمراني",
    "التأمين",
    "التعاملات والأحوال المدنية",
    "التعليم",
    "التنمية الاجتماعية",
    "الثقافة والإعلام",
    "الجمارك والتجارة الدولية",
    "الجنايات والجرائم",
    "الحج والعمرة",
    "الحوكمة",
    "الرقابة",
    "الرياضة",
    "الزراعة",
    "السياحة والترفيه",            # NOTE: combined — NOT "السياحة" + "الترفيه"
    "الشؤون الإسلامية والأوقاف",
    "الشؤون الخارجية",
    "الصحة",
    "الصناعة والتعدين",
    "الطاقة",
    "العقار",
    "العمل والتوظيف",
    "القضاء والمحاكم",
    "المالية والضرائب",
    "المعاملات التجارية",
    "الملكية الفكرية",
    "المنظمات غير الربحية",
    "المهن المرخصة",
    "المواصفات والمقاييس",
    "المياه والبيئة",
    "النقل",
    "تقنية المعلومات والأمن السيبراني",
    "حقوق الإنسان",
    "حوكمة الشركات والاستثمار",
]

# Pre-formatted for embedding directly into LLM prompts (pipe-separated).
SECTORS_PROMPT_LIST: str = " | ".join(VALID_SECTORS)


# ─── Canonicalization ─────────────────────────────────────────────────────────


def canonicalize_sectors(sectors: list[str]) -> list[str]:
    """Map LLM-output sector names to exact canonical VALID_SECTORS entries.

    Algorithm:
    1. Exact match → keep (fast path, O(1) set lookup).
    2. Substring match (bidirectional):
       a. Raw name appears inside a canonical entry — e.g. "السياحة"
          inside "السياحة والترفيه".
       b. Canonical entry appears inside raw name — e.g. LLM emits
          "قضايا القضاء والمحاكم" → map to "القضاء والمحاكم".
       Shortest match wins (most specific).
    3. No match → warn and drop (safer than passing an invalid filter).
    4. Deduplicate so repeated mappings collapse.

    Args:
        sectors: Raw sector names emitted by an LLM (planner or expander).

    Returns:
        Deduplicated canonical names. Empty list means no valid sectors
        were found — callers should treat this as filter_sectors=None.
    """
    valid_set = set(VALID_SECTORS)
    result: list[str] = []

    for raw in sectors:
        raw = (raw or "").strip()
        if not raw:
            continue

        # 1. Exact match — fast path
        if raw in valid_set:
            if raw not in result:
                result.append(raw)
            continue

        # 2. Substring match — bidirectional, shortest (most specific) wins
        candidates = [s for s in VALID_SECTORS if raw in s or s in raw]
        if candidates:
            best = min(candidates, key=len)
            if best not in result:
                result.append(best)
            if best != raw:
                logger.info("sector canonicalize: %r -> %r", raw, best)
            continue

        # 3. No match — drop with a warning
        logger.warning(
            "sector canonicalize: no match for %r -- dropped from filter "
            "(not in VALID_SECTORS and not a substring of any canonical entry)",
            raw,
        )

    return result


__all__ = ["VALID_SECTORS", "SECTORS_PROMPT_LIST", "canonicalize_sectors"]
