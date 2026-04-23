"""Preprocessor — deterministic reference assembly for the aggregator.

This module is PURE CODE (no LLM). It receives reranker output and:

1. Flattens `RerankedResult` items across all sub-queries.
2. Deduplicates by stable identity:
   - articles: (regulation_title, article_num)
   - sections: (regulation_title, section_title)
3. Merges duplicates:
   - relevance: "high" wins over "medium"
   - reasoning: semicolon-joined, deduped
4. Assigns 1-based citation numbers (stable across a single run) ordered by:
   - relevance DESC (high before medium)
   - then first-appearance order across sub-queries
5. Returns a parallel mapping ref.n -> list of 0-based sub_query indices,
   used downstream by the validator for coverage checks.

When `agg_input.compliance_results` is set, compliance references are appended
AFTER reg references (regulations take ordering priority). Compliance refs
are keyed by their URA ref_id (already unique) -- no cross-domain dedup.

Assigning citation numbers in code (not in the LLM) is the central
anti-hallucination mechanism for the aggregator.
"""
from __future__ import annotations

from typing import Iterable

from agents.deep_search_v3.reg_search.models import RerankedResult

from .models import AggregatorInput, Reference

__all__ = [
    "preprocess_references",
    "build_snippet",
    "_identity_key",
    "_merge_duplicates",
    "_compliance_identity_key",
]


# ---------------------------------------------------------------------------
# Identity + merging
# ---------------------------------------------------------------------------


def _norm(s: str | None) -> str:
    """Normalize for identity comparisons (trim + collapse whitespace)."""
    if not s:
        return ""
    return " ".join(s.split()).strip()


def _identity_key(result: RerankedResult) -> tuple[str, str, str]:
    """Return the stable identity tuple for a reranker result.

    Shape: (source_type, regulation_title_norm, sub_identifier_norm)
    - for articles, sub_identifier = article_num
    - for sections, sub_identifier = section_title
    """
    reg = _norm(result.regulation_title)
    if result.source_type == "article":
        return ("article", reg, _norm(result.article_num))
    # section (only other option per the Literal)
    return ("section", reg, _norm(result.section_title))


def _compliance_identity_key(ref: str) -> str:
    """Identity for compliance URA results -- the URA ref_id is already unique."""
    return _norm(ref)


def _is_citable(result: RerankedResult) -> bool:
    """Drop malformed reranker results that cannot produce a meaningful citation.

    A result is citable iff it has at least one of:
    - regulation_title + article_num (article)
    - regulation_title + section_title (section)
    - regulation_title alone (whole-regulation reference)

    Observed malformed case: reranker tagged a result as [مادة] but emitted
    only a title+reasoning with no regulation name or article number. Such
    entries produce empty reference labels like "(ref #83) — " in the final
    doc and should be filtered out upstream.
    """
    reg = _norm(result.regulation_title)
    if not reg:
        # Allow through only if the title itself looks like a regulation name.
        title = _norm(result.title)
        has_regulation_in_title = any(
            marker in title
            for marker in ("نظام ", "لائحة ", "الأدلة ", "اللوائح ")
        )
        if not has_regulation_in_title:
            return False
        # Title-only: treat as whole-regulation reference, still citable.
        return True
    if result.source_type == "article":
        return bool(_norm(result.article_num))
    if result.source_type == "section":
        return bool(_norm(result.section_title))
    return True


_RELEVANCE_RANK = {"high": 2, "medium": 1}


def _relevance_max(a: str, b: str) -> str:
    """Return the stronger of two relevance tags ("high" > "medium")."""
    return a if _RELEVANCE_RANK.get(a, 0) >= _RELEVANCE_RANK.get(b, 0) else b


def _merge_reasoning(parts: Iterable[str]) -> str:
    """Semicolon-join non-empty reasoning strings, preserving first-seen order,
    deduped case-sensitively after whitespace normalization."""
    seen: set[str] = set()
    ordered: list[str] = []
    for p in parts:
        norm = _norm(p)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        ordered.append(norm)
    return "؛ ".join(ordered)


def _merge_duplicates(results: list[RerankedResult]) -> RerankedResult:
    """Collapse a list of duplicate reranker results into one.

    Keeps the first occurrence's content/title/metadata as the base, but:
    - lifts relevance to the highest seen
    - concatenates reasoning across all occurrences
    """
    if not results:
        raise ValueError("_merge_duplicates requires at least one result")
    base = results[0]
    if len(results) == 1:
        return base

    best_relevance = base.relevance
    for r in results[1:]:
        best_relevance = _relevance_max(best_relevance, r.relevance)

    merged_reasoning = _merge_reasoning(r.reasoning for r in results)

    # Prefer the first non-empty content / summary when base has blanks.
    content = base.content
    section_summary = base.section_summary
    for r in results[1:]:
        if not content and r.content:
            content = r.content
        if not section_summary and r.section_summary:
            section_summary = r.section_summary

    return base.model_copy(
        update={
            "relevance": best_relevance,
            "reasoning": merged_reasoning,
            "content": content,
            "section_summary": section_summary,
        }
    )


# ---------------------------------------------------------------------------
# Snippet extraction
# ---------------------------------------------------------------------------


_SENTENCE_TERMINATORS = ".!؟?\n"


def build_snippet(result: RerankedResult, max_chars: int = 500) -> str:
    """Extract a short excerpt suitable for hover tooltips + validator grounding.

    Rules:
    - Prefer first sentence or first paragraph of `content`.
    - Fall back to `section_summary`, then `title`.
    - Trim to `max_chars`, never cut mid-word.
    """
    source = result.content or result.section_summary or result.title or ""
    source = source.strip()
    if not source:
        return ""

    # Prefer a natural boundary inside the allowed window.
    window = source[: max_chars + 1]  # +1 so we can see if we overflowed
    best_cut = -1
    for term in _SENTENCE_TERMINATORS:
        idx = window.rfind(term)
        if idx > best_cut:
            best_cut = idx

    if best_cut != -1 and best_cut <= max_chars:
        snippet = source[: best_cut + 1].strip()
        if snippet:
            return snippet

    # No sentence terminator — fall back to word boundary.
    if len(source) <= max_chars:
        return source

    cut = source.rfind(" ", 0, max_chars + 1)
    if cut <= 0:
        # single huge token — hard-cut (should be extremely rare)
        return source[:max_chars].rstrip()
    return source[:cut].rstrip()


def _build_snippet_text(text: str, max_chars: int = 500) -> str:
    """Snippet helper for arbitrary text (used for compliance results)."""
    source = (text or "").strip()
    if not source:
        return ""
    window = source[: max_chars + 1]
    best_cut = -1
    for term in _SENTENCE_TERMINATORS:
        idx = window.rfind(term)
        if idx > best_cut:
            best_cut = idx
    if best_cut != -1 and best_cut <= max_chars:
        snippet = source[: best_cut + 1].strip()
        if snippet:
            return snippet
    if len(source) <= max_chars:
        return source
    cut = source.rfind(" ", 0, max_chars + 1)
    if cut <= 0:
        return source[:max_chars].rstrip()
    return source[:cut].rstrip()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def _reference_from_result(n: int, result: RerankedResult) -> Reference:
    """Build a Reference from a merged RerankedResult, assigning citation number `n`."""
    ref_id = f"reg:{result.db_id}" if getattr(result, "db_id", "") else ""
    return Reference(
        n=n,
        source_type=result.source_type,  # type: ignore[arg-type]
        regulation_title=result.regulation_title,
        article_num=result.article_num if result.source_type == "article" else None,
        section_title=result.section_title if result.source_type == "section" else None,
        title=result.title,
        snippet=build_snippet(result),
        relevance=result.relevance,
        ref_id=ref_id,
        domain="regulations",
    )


def _reference_from_compliance(n: int, raw: dict) -> Reference:
    """Build a Reference from a compliance URAResult-compatible dict."""
    metadata = raw.get("metadata", {}) or {}
    source_type_raw = (raw.get("source_type") or "gov_service").strip()
    if source_type_raw not in ("gov_service", "form"):
        source_type_raw = "gov_service"

    authority = (
        metadata.get("authority")
        or metadata.get("provider_name")
        or metadata.get("platform_name")
        or ""
    )
    relevance_raw = (raw.get("relevance") or "medium").strip().lower()
    if relevance_raw not in ("high", "medium"):
        relevance_raw = "medium"

    snippet = _build_snippet_text(raw.get("content") or raw.get("title") or "")

    return Reference(
        n=n,
        source_type=source_type_raw,  # type: ignore[arg-type]
        regulation_title=authority,
        article_num=None,
        section_title=None,
        title=raw.get("title", ""),
        snippet=snippet,
        relevance=relevance_raw,  # type: ignore[arg-type]
        ref_id=raw.get("ref_id", ""),
        domain="compliance",
    )


def preprocess_references(
    agg_input: AggregatorInput,
) -> tuple[list[Reference], dict[int, list[int]]]:
    """Assign 1-based citation numbers to deduplicated reranker results.

    Returns:
        references: ordered list, each with a unique .n starting at 1.
            Ordered by relevance DESC, then first-appearance order.
            Compliance references (if any) are appended after reg references.
        ref_to_sub_queries: mapping reference.n -> sorted list of 0-based
            sub_query indices that produced it (used for coverage validation).
            Compliance references have empty lists here (no sub-query index).
    """
    # --- Stage 1: reg references --------------------------------------------
    grouped: dict[tuple[str, str, str], list[RerankedResult]] = {}
    first_seen_order: dict[tuple[str, str, str], int] = {}
    sub_query_hits: dict[tuple[str, str, str], list[int]] = {}

    appearance = 0
    for sq_idx, sub_query in enumerate(agg_input.sub_queries or []):
        for result in getattr(sub_query, "results", []) or []:
            if not _is_citable(result):
                continue
            key = _identity_key(result)
            if key not in grouped:
                grouped[key] = []
                first_seen_order[key] = appearance
                appearance += 1
                sub_query_hits[key] = []
            grouped[key].append(result)
            if sq_idx not in sub_query_hits[key]:
                sub_query_hits[key].append(sq_idx)

    references: list[Reference] = []
    ref_to_sub_queries: dict[int, list[int]] = {}

    if grouped:
        merged: dict[tuple[str, str, str], RerankedResult] = {
            key: _merge_duplicates(items) for key, items in grouped.items()
        }

        sorted_keys = sorted(
            merged.keys(),
            key=lambda k: (
                -_RELEVANCE_RANK.get(merged[k].relevance, 0),
                first_seen_order[k],
            ),
        )

        for new_n, key in enumerate(sorted_keys, start=1):
            ref = _reference_from_result(new_n, merged[key])
            references.append(ref)
            ref_to_sub_queries[new_n] = sorted(sub_query_hits[key])

    # --- Stage 2: compliance references -------------------------------------
    compliance_slice = getattr(agg_input, "compliance_results", None)
    if compliance_slice is not None:
        seen_compliance_ids: set[str] = set()
        next_n = len(references) + 1
        for raw in compliance_slice.results or []:
            if not isinstance(raw, dict):
                continue
            ident = _compliance_identity_key(raw.get("ref_id", ""))
            if not ident or ident in seen_compliance_ids:
                continue
            seen_compliance_ids.add(ident)
            ref = _reference_from_compliance(next_n, raw)
            references.append(ref)
            ref_to_sub_queries[next_n] = []
            next_n += 1

    return references, ref_to_sub_queries
