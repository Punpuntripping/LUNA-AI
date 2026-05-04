"""Canonical sector vocabulary for reg_search.

Single source of truth for VALID_SECTORS — the exact names stored in the
DB's regulations.sectors[] text[] column.

Used for:
1. Prompting the expander LLM (so it generates names that match the DB)
2. Canonicalizing LLM output before passing as a DB filter
3. populate_sectors.py batch classification

The core failure mode this prevents:
    prompt_2 listed  "السياحة | الترفيه"  as two separate sectors
    DB stores them as "السياحة والترفيه"  (one combined entry)
    PostgreSQL && operator is exact-match → both were silently dropped
    → sector filter excluded ALL rows → 0 results with no error

canonicalize_sectors() uses substring matching:
    "السياحة" is a substring of "السياحة والترفيه" → maps correctly
    "الترفيه" is a substring of "السياحة والترفيه" → maps correctly
    "العمل"   is a substring of "العمل والتوظيف"   → maps correctly

NOTE: difflib character-similarity was rejected because Arabic words share
common prefixes (ال) and similar letter patterns, causing false positives:
    "السياحة" → difflib best match = "الرياضة" (ratio 0.714) ← WRONG
    "السياحة" → substring match   = "السياحة والترفيه"       ← CORRECT
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ─── Canonical sector list ────────────────────────────────────────────────────
# These must match exactly what populate_sectors.py writes into
# regulations.sectors[]. Sorted alphabetically for readability.

VALID_SECTORS: list[str] = [
    "الأحوال المدنية والجوازات",
    "الأمن الغذائي",
    "الأمن والدفاع",
    "الإسكان",
    "الاتصالات وتقنية المعلومات",
    "الاستثمار",
    "البحث والابتكار",
    "البلديات والتخطيط العمراني",
    "التأمين",
    "التجارة",
    "التعاملات المدنية",
    "التعدين",
    "التعليم",
    "التنمية الاجتماعية",
    "الثقافة والإعلام",
    "الحج والعمرة",
    "الحوكمة",
    "الرقابة",
    "الرياضة",
    "الزراعة",
    "السياحة والترفيه",            # NOTE: combined — NOT "السياحة" + "الترفيه"
    "الشؤون الإسلامية والأوقاف",
    "الشؤون الخارجية",
    "الشركات",
    "الصحة",
    "الصناعة",
    "الطاقة",
    "العدل والقضاء",
    "العقار",
    "العمل والتوظيف",
    "الخدمات اللوجستية",
    "المالية والضرائب",
    "المساحة والمعلومات الجيومكانية",
    "الملكية الفكرية",
    "المنظمات غير الربحية",
    "المهن المرخصة",
    "المواصفات والمقاييس",
    "المياه والبيئة",
    "النقل",
    "حقوق الإنسان",
]

# Pre-formatted for embedding directly into LLM prompts (pipe-separated)
SECTORS_PROMPT_LIST: str = " | ".join(VALID_SECTORS)


# ─── Canonicalization ─────────────────────────────────────────────────────────


def canonicalize_sectors(sectors: list[str]) -> list[str]:
    """Map LLM-output sector names to exact canonical VALID_SECTORS entries.

    Algorithm:
    1. Exact match → keep as-is (fast path, O(1) set lookup).
    2. Substring match → if the raw name is a complete word found inside a
       canonical entry, use that entry.
       Example: "السياحة" is in "السياحة والترفيه" → maps correctly.
       If multiple canonical entries contain the word, use the shortest
       (most specific) one.
    3. No match → warn and drop (safer than passing an invalid filter).
    4. Deduplicate — ["السياحة", "الترفيه"] both map to "السياحة والترفيه",
       result contains it only once.

    Why not difflib? Arabic words share common prefixes (ال) and letter
    patterns so character similarity gives false positives:
        "السياحة" → difflib best = "الرياضة" (ratio 0.714) ← WRONG
        "السياحة" → substring    = "السياحة والترفيه"      ← CORRECT

    Args:
        sectors: Raw sector names from LLM output.

    Returns:
        Deduplicated list of canonical sector names. Empty list means no valid
        sectors were found — callers should treat this as filter_sectors=None.
    """
    valid_set = set(VALID_SECTORS)
    result: list[str] = []

    for raw in sectors:
        raw = raw.strip()
        if not raw:
            continue

        # 1. Exact match — fast path
        if raw in valid_set:
            if raw not in result:
                result.append(raw)
            continue

        # 2. Substring match — raw must appear as a full substring of the
        #    canonical entry (not just a character overlap).
        #    We treat the raw text as a word/phrase to look for inside the
        #    canonical name, e.g. "السياحة" inside "السياحة والترفيه".
        substring_matches = [s for s in VALID_SECTORS if raw in s]
        if substring_matches:
            # Pick the most specific (shortest) match to avoid over-broad entries
            best = min(substring_matches, key=len)
            if best not in result:
                result.append(best)
            if best != raw:
                logger.info("sector canonicalize (substring): %r -> %r", raw, best)
            continue

        # 3. No match — drop with a warning
        logger.warning(
            "sector canonicalize: no match for %r -- dropped from filter "
            "(not in VALID_SECTORS and not a substring of any canonical entry)",
            raw,
        )

    return result
