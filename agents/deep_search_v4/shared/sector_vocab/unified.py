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

# O(1) membership set — built once.
_VALID_SET: frozenset[str] = frozenset(VALID_SECTORS)


# ─── Aliases ──────────────────────────────────────────────────────────────────
# Curated map of common LLM near-misses that neither exact nor substring
# matching catches, pointing each to its canonical sector.
#
# The driving case (diagnosed in conv ``accbc49c``): the sector is labelled
# ``التعاملات والأحوال المدنية`` (تـ-prefix), but the well-known law filed under
# it is ``نظام المعاملات المدنية`` (مـ-prefix). A model reasoning about that law
# naturally emits ``المعاملات المدنية`` — a genuinely different word from the
# sector label (الت‍عاملات ≠ الم‍عاملات), so it is neither a substring of the
# canonical entry nor vice-versa, and it would otherwise be silently dropped.
# The civil-status laws (``نظام الأحوال الشخصية``) live under the same sector and
# trigger the same near-miss, so they get aliases too.
SECTOR_ALIASES: dict[str, str] = {
    "المعاملات المدنية": "التعاملات والأحوال المدنية",
    "نظام المعاملات المدنية": "التعاملات والأحوال المدنية",
    "الأحوال الشخصية": "التعاملات والأحوال المدنية",
    "المعاملات الشخصية": "التعاملات والأحوال المدنية",
}


# ─── Canonicalization ─────────────────────────────────────────────────────────


def resolve_sector(raw: str) -> str | None:
    """Resolve one raw sector name to its canonical VALID_SECTORS entry.

    Resolution order:
    1. Exact match (fast path, O(1) set lookup).
    2. Alias map — curated near-misses (see :data:`SECTOR_ALIASES`).
    3. Substring match, bidirectional, shortest (most specific) entry wins:
       - raw inside a canonical entry — e.g. ``السياحة`` → ``السياحة والترفيه``;
       - a canonical entry inside raw — e.g. ``قضايا القضاء والمحاكم`` →
         ``القضاء والمحاكم``.

    Returns the canonical name, or ``None`` when nothing matches. Callers
    decide what ``None`` means: :func:`canonicalize_sectors` drops it; the
    sector_picker schema treats it as an invalid output (rejected wholesale).
    """
    raw = (raw or "").strip()
    if not raw:
        return None

    # 1. Exact match — fast path
    if raw in _VALID_SET:
        return raw

    # 2. Alias map
    aliased = SECTOR_ALIASES.get(raw)
    if aliased is not None:
        return aliased

    # 3. Substring match — bidirectional, shortest (most specific) wins
    candidates = [s for s in VALID_SECTORS if raw in s or s in raw]
    if candidates:
        return min(candidates, key=len)

    return None


def canonicalize_sectors(sectors: list[str]) -> list[str]:
    """Map LLM-output sector names to exact canonical VALID_SECTORS entries.

    Each raw name is resolved via :func:`resolve_sector` (exact → alias →
    bidirectional substring). Names that resolve are kept (deduplicated); names
    that do not resolve are **dropped with a warning** — safer than passing an
    invalid filter. This drop-and-keep contract is relied on by reg_search,
    case_search and the v4 planner; the sector_picker uses a stricter all-or-
    nothing path in its output schema instead.

    Args:
        sectors: Raw sector names emitted by an LLM (planner or expander).

    Returns:
        Deduplicated canonical names. Empty list means no valid sectors
        were found — callers should treat this as filter_sectors=None.
    """
    result: list[str] = []

    for raw in sectors:
        resolved = resolve_sector(raw)
        if resolved is None:
            logger.warning(
                "sector canonicalize: no match for %r -- dropped from filter "
                "(not in VALID_SECTORS, no alias, not a substring of any "
                "canonical entry)",
                raw,
            )
            continue

        if resolved not in result:
            result.append(resolved)
        if resolved != (raw or "").strip():
            logger.info("sector canonicalize: %r -> %r", raw, resolved)

    return result


__all__ = [
    "VALID_SECTORS",
    "SECTORS_PROMPT_LIST",
    "SECTOR_ALIASES",
    "resolve_sector",
    "canonicalize_sectors",
]
