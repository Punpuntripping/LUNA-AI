"""Canonical legal-domain vocabulary for case_search.

Single source of truth for VALID_SECTORS — the Arabic tags stored in the
DB's `cases.legal_domains text[]` column. Used for:

1. Prompting the expander LLM (so it generates names that match the DB).
2. Canonicalizing LLM output before passing as a DB filter.

The core failure mode this prevents:
    LLM emits   "عقود المقاولات"        (truncated / common colloquial form)
    DB stores   "عقود المقاولات والإنشاءات"  (full official tag)
    PostgreSQL `&&` operator is exact-match → filter silently returns 0 rows.

canonicalize_sectors() uses substring matching: if the raw name is a substring
of any canonical entry (or vice versa), map to the shortest match. Same
rationale as reg_search/sector_vocab.py — difflib character similarity is
unsafe for Arabic because of shared prefixes (ال) and letter patterns.

List source: live audit of `cases.legal_domains` distribution on the
production Supabase instance (2026-04-21). Entries below the threshold
(~50 cases) are omitted to keep the prompt tight; the LLM is instructed to
emit `null` when the issue doesn't fit any listed sector, so truly niche
domains fall through cleanly rather than being misclassified.

Known data issues (not vocabulary issues):
    - 20 cases have empty legal_domains (will not surface under any sector filter).
    - ~250 cases carry OCR/classification typos like "التعويضات والأضمار"
      (194), "التعويضات والأضهار" (61) instead of "التعويضات والأضرار".
      These rows are invisible to sector filtering until the ingestion side
      re-tags them; the Python `canonicalize_sectors` can't fix DB typos
      (it maps LLM → canonical, not DB → canonical).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ─── Canonical legal-domain list ──────────────────────────────────────────────
# Ordered by frequency in the live DB (descending). Every entry has ≥50 cases
# — together they cover >99% of all tagged cases in entity 17642.

VALID_SECTORS: list[str] = [
    "المسائل الإجرائية والاختصاص",              # 7,482 cases
    "عقود تجارية",                                # 6,909
    "تحصيل الديون والمطالبات المالية",           # 5,855
    "منازعات البيع والشراء",                     # 3,657
    "الإخلال العقدي",                             # 2,862
    "التعويضات والأضرار",                        # 2,840
    "عقود التوريد والتوزيع",                     # 1,659
    "عقود المقاولات والإنشاءات",                 # 1,419
    "التحكيم والوسائل البديلة لحل المنازعات",    # 1,267
    "الشراكة والاستثمار",                         # 1,039
    "التنفيذ وإجراءات التنفيذ",                  # 1,010
    "قانون الشركات",                              #   938
    "عقود الخدمات",                               #   936
    "عقود الإيجار",                               #   713
    "الأوراق التجارية",                           #   275
    "النقل واللوجستيات",                          #   227
    "الإفلاس والإعسار",                           #   134
    "الملكية الفكرية",                            #   132
    "الوكالة والتمثيل التجاري",                   #   124
    "الاحتيال والجرائم التجارية",                 #   114
    "منازعات العمل والعمال",                      #   109
    "الخدمات المصرفية والتمويل",                  #    88
    "التجارة الإلكترونية",                        #    85
    "العقارات والممتلكات",                        #    82
    "الضمان والكفالة",                            #    73
    "حماية المستهلك",                             #    52
]

# Pre-formatted for embedding directly into LLM prompts (pipe-separated).
SECTORS_PROMPT_LIST: str = " | ".join(VALID_SECTORS)


# ─── Canonicalization ─────────────────────────────────────────────────────────


def canonicalize_sectors(sectors: list[str]) -> list[str]:
    """Map LLM-output sector names to exact canonical VALID_SECTORS entries.

    Algorithm:
    1. Exact match → keep (fast path, O(1) set lookup).
    2. Substring match (bidirectional):
       a. Raw name appears inside a canonical entry — e.g. "عقود المقاولات"
          inside "عقود المقاولات والإنشاءات".
       b. Canonical entry appears inside raw name — e.g. LLM emits
          "قضايا الإفلاس والإعسار التجارية" → map to "الإفلاس والإعسار".
       Shortest match wins (most specific).
    3. No match → warn and drop (safer than passing an invalid filter).
    4. Deduplicate so repeated mappings collapse.

    Args:
        sectors: Raw sector names emitted by the expander LLM.

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

        if raw in valid_set:
            if raw not in result:
                result.append(raw)
            continue

        candidates = [s for s in VALID_SECTORS if raw in s or s in raw]
        if candidates:
            best = min(candidates, key=len)
            if best not in result:
                result.append(best)
            if best != raw:
                logger.info("legal_domain canonicalize: %r -> %r", raw, best)
            continue

        logger.warning(
            "legal_domain canonicalize: no match for %r -- dropped from filter",
            raw,
        )

    return result
