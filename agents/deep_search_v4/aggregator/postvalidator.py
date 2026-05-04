"""Post-validator for the aggregator agent (pure code, no LLM).

Runs AFTER the LLM produces `AggregatorLLMOutput` and populates a
`ValidationReport`. Catches hallucinated citations, non-Arabic output,
missing structural sections, and dishonest gap reporting before the
synthesis reaches the user.

All functions are side-effect free and deterministic.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable

from agents.deep_search_v4.aggregator.models import (
    AggregatorInput,
    AggregatorLLMOutput,
    Reference,
    ValidationReport,
)
from agents.deep_search_v4.reg_search.models import RerankerQueryResult


# ---------------------------------------------------------------------------
# Arabic normalization helpers
# ---------------------------------------------------------------------------

# Arabic tashkeel (diacritics): U+064B..U+065F + U+0670 (superscript alef) + U+0640 (tatweel)
_TASHKEEL_RE = re.compile(
    r"[\u064B-\u065F\u0670\u0640]"
)

# Minimal Arabic stopword set for query anchoring (kept small on purpose)
_AR_STOPWORDS: frozenset[str] = frozenset(
    {
        "في", "من", "إلى", "الى", "على", "عن", "مع", "أو", "او",
        "و", "أن", "ان", "إن", "هذا", "هذه", "هو", "هي",
        "التي", "الذي", "ال", "ما", "لا", "ثم", "قد", "كل",
    }
)

# Inline citation pattern: digit-only parenthesized groups like (1) or (1,3)
# Match ASCII digits. Allow ASCII comma OR Arabic comma U+060C as separator.
_CITATION_RE = re.compile(r"\((\d+(?:\s*[,\u060C]\s*\d+)*)\)")

# Thinking block: <thinking>...</thinking> (case-insensitive, dotall)
_THINKING_RE = re.compile(r"<thinking\b[^>]*>.*?</thinking\s*>", re.IGNORECASE | re.DOTALL)

# Code fence block: ``` ... ```
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)

# Latin word pattern (>=2 Latin letters in a row)
_LATIN_WORD_RE = re.compile(r"[A-Za-z]{2,}")

# "Latin sentence" heuristic: 3+ Latin words in a row (separated by spaces/punct)
_LATIN_SENTENCE_RE = re.compile(
    r"(?:[A-Za-z]{2,}[\s,.;:!?'\"\-]+){2,}[A-Za-z]{2,}"
)


def _normalize_ar(text: str) -> str:
    """Strip diacritics + tatweel, normalize Alef variants, collapse whitespace, lowercase ASCII."""
    if not text:
        return ""
    # Unicode normalize (NFKC) to fold compatibility forms
    t = unicodedata.normalize("NFKC", text)
    # Remove tashkeel & tatweel
    t = _TASHKEEL_RE.sub("", t)
    # Normalize alef variants (إ أ آ ا → ا) and ya/alef-maksura, and teh marbuta
    t = t.translate(
        str.maketrans(
            {
                "إ": "ا",
                "أ": "ا",
                "آ": "ا",
                "ٱ": "ا",
                "ى": "ي",
                "ة": "ه",
            }
        )
    )
    # Collapse whitespace
    t = re.sub(r"\s+", " ", t)
    return t.strip().lower()


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def strip_thinking_block(synthesis_md: str) -> str:
    """Remove <thinking>...</thinking> block(s) and return the rest."""
    if not synthesis_md:
        return ""
    return _THINKING_RE.sub("", synthesis_md)


def extract_cited_numbers(synthesis_md: str) -> list[int]:
    """Regex-extract all (N) or (N,M,...) inline citations as sorted unique ints.

    Only matches digit-only parenthesized groups. `(مادة 5)` is ignored.
    """
    if not synthesis_md:
        return []
    body = strip_thinking_block(synthesis_md)
    nums: set[int] = set()
    for m in _CITATION_RE.finditer(body):
        group = m.group(1)
        # Split on ASCII comma OR Arabic comma
        parts = re.split(r"[,\u060C]", group)
        for p in parts:
            p = p.strip()
            if p.isdigit():
                nums.add(int(p))
    return sorted(nums)


def check_arabic_only(synthesis_md: str) -> bool:
    """True if the BODY (no thinking block, no code fences) has no Latin sentences.

    Headers/labels with a single Latin word (e.g. "CRAC") are tolerated.
    """
    if not synthesis_md:
        return True
    body = strip_thinking_block(synthesis_md)
    body = _CODE_FENCE_RE.sub(" ", body)
    # Also strip inline code `...`
    body = re.sub(r"`[^`]*`", " ", body)
    return _LATIN_SENTENCE_RE.search(body) is None


def check_structure(synthesis_md: str, prompt_key: str) -> tuple[bool, list[str]]:
    """Verify required Arabic headings are present for this prompt variant.

    Returns (ok, notes). Uses prefix matching on the Arabic heading text
    to tolerate minor variations.
    """
    notes: list[str] = []
    if not synthesis_md:
        return False, ["empty synthesis"]

    body = strip_thinking_block(synthesis_md)

    # Collect markdown headings (## or ###) along with their text
    heading_lines: list[tuple[int, str]] = []  # (level, normalized_text)
    for line in body.splitlines():
        m = re.match(r"^(#{2,6})\s*(.+?)\s*$", line)
        if m:
            level = len(m.group(1))
            text = _normalize_ar(m.group(2))
            heading_lines.append((level, text))

    def has_h2_prefix(prefixes: Iterable[str]) -> bool:
        for level, text in heading_lines:
            if level == 2 and any(text.startswith(_normalize_ar(p)) for p in prefixes):
                return True
        return False

    def has_any_h3() -> bool:
        return any(level == 3 for level, _ in heading_lines)

    key = (prompt_key or "").lower()
    # prompt_3_* (DCR rewrite) uses CRAC structure
    is_crac = key == "prompt_1" or key.startswith("prompt_3")
    is_irac = key == "prompt_2"
    is_thematic = key == "prompt_4"
    # Mode-specialized variants (planner — option B)
    is_reg_only = key == "prompt_reg_only"        # IRAC-shaped, regulatory framing
    is_cases_only = key == "prompt_cases_only"     # CRAC-shaped, judicial principles
    is_comp_only = key == "prompt_comp_only"       # procedural, services + (optional) forms
    is_cases_focus = key == "prompt_cases_focus"   # cases-led + compliance paths

    if is_crac:
        # CRAC: الخلاصة / الأساس النظامي / التطبيق / الخلاصة النهائية
        # "الخلاصة النهائية" starts with "الخلاصة" — use more specific checks first.
        required = {
            "conclusion_initial": ["الخلاصة"],  # initial summary
            "legal_basis": [
                "الأساس النظامي",
                "الاساس النظامي",
                "الأساس القانوني",
                "الأساس المرجعي",
                "الاساس المرجعي",
            ],
            "application": ["التطبيق", "تطبيق"],
            "final": ["الخلاصة النهائية", "الخلاصه النهائيه"],
        }
        # Count H2 headings starting with "الخلاصة"
        khulasa = [
            text for level, text in heading_lines
            if level == 2 and text.startswith(_normalize_ar("الخلاصة"))
        ]
        final_found = any(t.startswith(_normalize_ar("الخلاصة النهائية")) for t in khulasa)
        initial_found = len(khulasa) >= (2 if final_found else 1) or any(
            not t.startswith(_normalize_ar("الخلاصة النهائية")) for t in khulasa
        )
        if not khulasa:
            notes.append("missing '## الخلاصة' heading")
            initial_found = False
        if not final_found:
            notes.append("missing '## الخلاصة النهائية' heading")
        if not has_h2_prefix(required["legal_basis"]):
            notes.append("missing '## الأساس النظامي' heading")
        if not has_h2_prefix(required["application"]):
            notes.append("missing '## التطبيق' heading")
        ok = initial_found and final_found and has_h2_prefix(
            required["legal_basis"]
        ) and has_h2_prefix(required["application"])
        return ok, notes

    if is_irac or is_reg_only:
        # IRAC: المسألة / القاعدة / التطبيق / النتيجة
        # prompt_reg_only uses the same skeleton with a regulatory-flavoured
        # rule heading ("القاعدة النظامية" / "القاعدة المرجعية" also accepted).
        checks = [
            ("المسألة", ["المسألة", "المساله", "المسأله"]),
            (
                "القاعدة",
                [
                    "القاعدة",
                    "القاعده",
                    "القاعدة النظامية",
                    "القاعده النظاميه",
                    "القاعدة المرجعية",
                    "الاساس النظامي",
                    "الأساس النظامي",
                ],
            ),
            ("التطبيق", ["التطبيق", "تطبيق"]),
            ("النتيجة", ["النتيجة", "النتيجه"]),
        ]
        ok = True
        for label, prefixes in checks:
            if not has_h2_prefix(prefixes):
                notes.append(f"missing '## {label}' heading")
                ok = False
        return ok, notes

    if is_cases_only or is_cases_focus:
        # Cases-flavoured CRAC: الخلاصة (initial) / المبادئ القضائية /
        # [for cases_focus: + المسارات العملية] / التطبيق / الخلاصة النهائية
        khulasa = [
            text for level, text in heading_lines
            if level == 2 and text.startswith(_normalize_ar("الخلاصة"))
        ]
        final_found = any(
            t.startswith(_normalize_ar("الخلاصة النهائية")) for t in khulasa
        )
        initial_found = any(
            not t.startswith(_normalize_ar("الخلاصة النهائية")) for t in khulasa
        )
        if not initial_found:
            notes.append("missing '## الخلاصة' heading")
        if not final_found:
            notes.append("missing '## الخلاصة النهائية' heading")
        principles_prefixes = [
            "المبادئ القضائية",
            "المبادي القضائيه",
            "المبادئ القضائيه",
            "المباديء القضائية",
        ]
        if not has_h2_prefix(principles_prefixes):
            notes.append("missing '## المبادئ القضائية' heading")
        application_prefixes = ["التطبيق", "تطبيق", "التطبيق على الحالة"]
        if not has_h2_prefix(application_prefixes):
            notes.append("missing '## التطبيق' heading")
        ok = (
            initial_found
            and final_found
            and has_h2_prefix(principles_prefixes)
            and has_h2_prefix(application_prefixes)
        )
        if is_cases_focus:
            paths_prefixes = [
                "المسارات العملية",
                "المسارات العمليه",
                "المسار العملي",
            ]
            if not has_h2_prefix(paths_prefixes):
                notes.append("missing '## المسارات العملية' heading")
                ok = False
        return ok, notes

    if is_comp_only:
        # Procedural: الخلاصة (initial) / الإجراءات والخدمات /
        # [optional: النماذج والوثائق] / الخلاصة النهائية
        khulasa = [
            text for level, text in heading_lines
            if level == 2 and text.startswith(_normalize_ar("الخلاصة"))
        ]
        final_found = any(
            t.startswith(_normalize_ar("الخلاصة النهائية")) for t in khulasa
        )
        initial_found = any(
            not t.startswith(_normalize_ar("الخلاصة النهائية")) for t in khulasa
        )
        if not initial_found:
            notes.append("missing '## الخلاصة' heading")
        if not final_found:
            notes.append("missing '## الخلاصة النهائية' heading")
        procedures_prefixes = [
            "الإجراءات والخدمات",
            "الاجراءات والخدمات",
            "الإجراءات",
            "الاجراءات",
            "الخدمات والإجراءات",
            "الخدمات",
            "المسارات الإجرائية",
            "المسارات الاجرائيه",
        ]
        if not has_h2_prefix(procedures_prefixes):
            notes.append("missing '## الإجراءات والخدمات' heading")
        ok = (
            initial_found
            and final_found
            and has_h2_prefix(procedures_prefixes)
        )
        # 'النماذج والوثائق' is intentionally optional — prompt instructs the
        # model to skip the section entirely when no form is present.
        return ok, notes

    if is_thematic:
        # Thematic: الخلاصة + >=1 '### ' theme heading + خلاصة عملية
        ok = True
        if not has_h2_prefix(["الخلاصة"]):
            notes.append("missing '## الخلاصة' heading")
            ok = False
        if not has_any_h3():
            notes.append("missing at least one '### ' theme heading")
            ok = False
        if not has_h2_prefix(["خلاصة عملية", "الخلاصة العملية"]):
            notes.append("missing '## خلاصة عملية' heading")
            ok = False
        return ok, notes

    # Unknown prompt_key: be lenient but flag it
    notes.append(f"unknown prompt_key '{prompt_key}' — structure not checked")
    return True, notes


def check_grounding(
    references: list[Reference],
    agg_input: AggregatorInput,
) -> list[int]:
    """Return reference numbers whose snippet is NOT found in any reranker result."""
    if not references:
        return []
    # Build one big normalized haystack from all reranked content + section summaries
    haystack_parts: list[str] = []
    for sq in agg_input.sub_queries or []:
        for r in (sq.results or []):
            content = getattr(r, "content", "") or ""
            section_summary = getattr(r, "section_summary", "") or ""
            title = getattr(r, "title", "") or ""
            haystack_parts.append(content)
            haystack_parts.append(section_summary)
            haystack_parts.append(title)
    haystack_norm = _normalize_ar(" || ".join(haystack_parts))

    ungrounded: list[int] = []
    for ref in references:
        snip = (ref.snippet or "").strip()
        if not snip:
            # No snippet to check — treat as grounded (not our job to flag)
            continue
        needle_raw = snip[:80]
        needle = _normalize_ar(needle_raw)
        if not needle:
            continue
        # Fuzzy-ish: require at least the first 40 normalized chars (or all if shorter) to appear
        min_len = min(40, len(needle))
        if needle[:min_len] in haystack_norm:
            continue
        # Fallback: try a stricter shorter match (first 25 chars)
        shorter = needle[: min(25, len(needle))]
        if shorter and shorter in haystack_norm:
            continue
        ungrounded.append(ref.n)
    return ungrounded


def check_sub_query_coverage(
    used_refs: list[int],
    ref_to_sub_queries: dict[int, list[int]],
    agg_input: AggregatorInput,
) -> float:
    """Fraction of sufficient sub-queries with >=1 cited ref. Denom = sufficient sub-queries."""
    sub_queries = agg_input.sub_queries or []
    if not sub_queries:
        return 0.0
    sufficient_idxs = [i for i, sq in enumerate(sub_queries) if getattr(sq, "sufficient", False)]
    if not sufficient_idxs:
        return 0.0
    used_set = set(used_refs or [])
    # Invert: sub_query_idx -> set of ref_ns mapped to it
    sq_to_refs: dict[int, set[int]] = {}
    for ref_n, sq_list in (ref_to_sub_queries or {}).items():
        for sq_idx in sq_list:
            sq_to_refs.setdefault(sq_idx, set()).add(ref_n)
    covered = 0
    for sq_idx in sufficient_idxs:
        if sq_to_refs.get(sq_idx, set()) & used_set:
            covered += 1
    return covered / len(sufficient_idxs)


def check_query_anchoring(synthesis_md: str, original_query: str) -> bool:
    """True if >=2 meaningful query words (len>=3, not stopwords) appear in first 500 chars."""
    if not synthesis_md or not original_query:
        return False
    body = strip_thinking_block(synthesis_md)
    head_norm = _normalize_ar(body[:500])
    if not head_norm:
        return False
    # Extract meaningful tokens from the original query
    q_norm = _normalize_ar(original_query)
    tokens = re.findall(r"\S+", q_norm)
    meaningful = [
        t for t in tokens
        if len(t) >= 3 and t not in _AR_STOPWORDS and _normalize_ar(t) not in _AR_STOPWORDS
    ]
    if not meaningful:
        # No meaningful tokens -> can't meaningfully assess; be lenient
        return True
    hits = sum(1 for t in meaningful if t in head_norm)
    return hits >= 2


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def validate_llm_output(
    llm_output: AggregatorLLMOutput,
    references: list[Reference],
    agg_input: AggregatorInput,
    ref_to_sub_queries: dict[int, list[int]],
    prompt_key: str,
    final_references: list[Reference] | None = None,
) -> ValidationReport:
    """Run all validators. Returns the populated ValidationReport.

    ``final_references`` (when provided) is the post-citation-filter list
    that will actually ship in the panel. Citation-integrity checks
    (dangling / unused) run against this set so ``unused_references``
    reports refs the user will see but the synthesis didn't cite, not the
    much larger pre-filter set.
    """
    notes: list[str] = []

    synthesis = llm_output.synthesis_md or ""

    # 1) Citation integrity — run against the post-filter set so that
    # ``unused_references`` reports only refs that actually survived to
    # the published panel (H1 follow-up).
    cited = extract_cited_numbers(synthesis)
    citation_refs = final_references if final_references is not None else references
    valid_ref_ns = {ref.n for ref in citation_refs}
    dangling = sorted(n for n in cited if n not in valid_ref_ns)
    unused = sorted(n for n in valid_ref_ns if n not in set(cited))
    citation_ok = len(dangling) == 0
    if dangling:
        notes.append(f"dangling citations: {dangling}")
    if unused:
        notes.append(f"unused references: {unused}")

    # 2) Arabic-only
    arabic_ok = check_arabic_only(synthesis)
    if not arabic_ok:
        notes.append("synthesis contains non-Arabic sentences in body")

    # 3) Structure
    structure_ok, structure_notes = check_structure(synthesis, prompt_key)
    notes.extend(structure_notes)

    # 4) Gap honesty: if any sub_query is insufficient, gaps[] must be non-empty
    sub_queries = agg_input.sub_queries or []
    any_insufficient = any(not getattr(sq, "sufficient", False) for sq in sub_queries)
    gaps = llm_output.gaps or []
    gap_honesty_ok = True
    if any_insufficient and not gaps:
        gap_honesty_ok = False
        notes.append("insufficient sub-queries exist but gaps[] is empty")

    # 5) Grounding (soft)
    ungrounded = check_grounding(references, agg_input)
    if ungrounded:
        notes.append(f"ungrounded snippets (soft): {ungrounded}")

    # 6) Coverage (soft)
    # Prefer LLM-declared used_refs; fall back to cited numbers
    used_refs = llm_output.used_refs or cited
    coverage = check_sub_query_coverage(used_refs, ref_to_sub_queries, agg_input)

    # 7) Query anchoring (soft)
    anchoring_ok = check_query_anchoring(synthesis, agg_input.original_query)
    if not anchoring_ok:
        notes.append("query anchoring weak: original query terms missing from intro")

    # Passed iff all HARD checks pass
    passed = bool(citation_ok and arabic_ok and structure_ok and gap_honesty_ok)

    return ValidationReport(
        passed=passed,
        cited_numbers=cited,
        dangling_citations=dangling,
        unused_references=unused,
        ungrounded_snippets=ungrounded,
        sub_query_coverage=coverage,
        query_anchoring_ok=anchoring_ok,
        arabic_only_ok=arabic_ok,
        structure_ok=structure_ok,
        gap_honesty_ok=gap_honesty_ok,
        notes=notes,
    )
