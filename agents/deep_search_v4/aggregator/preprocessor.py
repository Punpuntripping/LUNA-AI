"""Preprocessor — deterministic reference assembly for the aggregator.

This module is PURE CODE (no LLM). Under URA 2.0, the URA merger has already
deduplicated results by ``ref_id`` and tier-split them into ``high_results`` /
``medium_results``. The preprocessor here simply:

1. Walks ``ura.high_results`` then ``ura.medium_results`` (so high-relevance
   refs get lower citation numbers).
2. Dispatches on the typed discriminated union (``RegURAResult`` /
   ``ComplianceURAResult`` / ``CaseURAResult``) to build a ``Reference`` per
   URA result — no dict lookups, no cross-domain dedup.
3. Assigns 1-based citation numbers in iteration order.
4. Returns a parallel mapping ``ref.n -> appears_in_sub_queries`` (sorted)
   so the post-validator can do coverage checks.

When ``agg_input.ura is None`` (legacy callers that build ``sub_queries``
manually), the old pre-URA path still runs: iterate ``sub_queries.results``,
dedup by identity tuple, merge relevance / reasoning.

Assigning citation numbers in code (not in the LLM) is the central
anti-hallucination mechanism for the aggregator.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Iterable

from agents.deep_search_v4.reg_search.models import RerankedResult
from agents.deep_search_v4.source_viewer import build_source_view
from agents.deep_search_v4.ura.schema import (
    CaseURAResult,
    ComplianceURAResult,
    RegURAResult,
    URAResultBase,
)

from .models import AggregatorInput, Reference

logger = logging.getLogger(__name__)

__all__ = [
    "preprocess_references",
    "attach_source_views",
    "collect_ordered_ura_results",
    "build_snippet",
    "_identity_key",
    "_merge_duplicates",
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
    """Return the stable identity tuple for a legacy reranker result.

    Only used on the backward-compat path when ``agg_input.ura is None``.
    """
    reg = _norm(result.regulation_title)
    if result.source_type == "article":
        return ("article", reg, _norm(result.article_num))
    # section (only other option per the Literal)
    return ("section", reg, _norm(result.section_title))


def _is_citable(result: RerankedResult) -> bool:
    """Drop malformed legacy reranker results that cannot produce a citation."""
    reg = _norm(result.regulation_title)
    if not reg:
        title = _norm(result.title)
        has_regulation_in_title = any(
            marker in title
            for marker in ("نظام ", "لائحة ", "الأدلة ", "اللوائح ")
        )
        if not has_regulation_in_title:
            return False
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
    """Collapse a list of duplicate reranker results into one (legacy path)."""
    if not results:
        raise ValueError("_merge_duplicates requires at least one result")
    base = results[0]
    if len(results) == 1:
        return base

    best_relevance = base.relevance
    for r in results[1:]:
        best_relevance = _relevance_max(best_relevance, r.relevance)

    merged_reasoning = _merge_reasoning(r.reasoning for r in results)

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


def _build_snippet_text(text: str, max_chars: int = 500) -> str:
    """Snippet helper for arbitrary text."""
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


def build_snippet(result, max_chars: int = 500) -> str:
    """Extract a short excerpt suitable for hover tooltips + validator grounding.

    Accepts either a legacy ``RerankedResult`` or a URA result (any of the
    discriminated union members). Prefers ``content`` -> ``section_summary`` ->
    ``title``.
    """
    if isinstance(result, RegURAResult):
        source = (
            result.content
            or result.section_summary
            or result.title
            or ""
        )
    elif isinstance(result, (ComplianceURAResult, CaseURAResult)):
        source = result.content or result.title or ""
    else:
        # Legacy RerankedResult
        source = (
            getattr(result, "content", "")
            or getattr(result, "section_summary", "")
            or getattr(result, "title", "")
            or ""
        )
    return _build_snippet_text(source, max_chars=max_chars)


# ---------------------------------------------------------------------------
# Legacy path -- RerankedResult -> Reference
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


# ---------------------------------------------------------------------------
# URA 2.0 path -- typed URA result -> Reference
# ---------------------------------------------------------------------------


def _reference_from_ura(n: int, r: URAResultBase) -> Reference:
    """Dispatch on the URA discriminated union to build a Reference.

    - RegURAResult:        source_type passes through ("article" | "section")
    - ComplianceURAResult: source_type = "gov_service"; label = provider/platform name
    - CaseURAResult:       source_type = "case"; label = court name
    """
    snippet = build_snippet(r)

    if isinstance(r, RegURAResult):
        st = r.source_type if r.source_type in ("article", "section") else "article"
        return Reference(
            n=n,
            source_type=st,  # type: ignore[arg-type]
            regulation_title=r.regulation_title or "",
            article_num=r.article_num if st == "article" else None,
            section_title=r.section_title if st == "section" else None,
            title=r.title,
            snippet=snippet,
            relevance=r.relevance,
            ref_id=r.ref_id,
            domain="regulations",
        )

    if isinstance(r, ComplianceURAResult):
        label = r.provider_name or r.platform_name or ""
        return Reference(
            n=n,
            source_type="gov_service",
            regulation_title=label,
            article_num=None,
            section_title=None,
            title=r.title,
            snippet=snippet,
            relevance=r.relevance,
            ref_id=r.ref_id,
            domain="compliance",
        )

    if isinstance(r, CaseURAResult):
        return Reference(
            n=n,
            source_type="case",
            regulation_title=r.court or "",
            article_num=None,
            section_title=None,
            title=r.title,
            snippet=snippet,
            relevance=r.relevance,
            ref_id=r.ref_id,
            domain="cases",
        )

    # Defensive: unknown URA subclass. Build a minimal Reference so callers
    # keep working; upstream type checkers will catch new domains.
    return Reference(
        n=n,
        source_type="regulation",
        regulation_title=getattr(r, "title", "") or "",
        article_num=None,
        section_title=None,
        title=getattr(r, "title", "") or "",
        snippet=snippet,
        relevance=getattr(r, "relevance", "medium"),
        ref_id=getattr(r, "ref_id", "") or "",
        domain="regulations",
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def preprocess_references(
    agg_input: AggregatorInput,
) -> tuple[list[Reference], dict[int, list[int]]]:
    """Assign 1-based citation numbers to URA results (or legacy reranker output).

    Preferred path (URA 2.0): when ``agg_input.ura`` is set, iterate
    ``ura.high_results`` then ``ura.medium_results``. URA merger already
    deduplicates by ``ref_id`` and bins by relevance tier, so this function
    does NOT re-dedupe. Each URA result yields exactly one ``Reference``.

    Fallback (legacy): when ``ura is None`` — used by older CLIs / replay
    tests — walks ``agg_input.sub_queries`` and merges duplicates by
    identity tuple.

    Returns:
        references: ordered list, each with a unique .n starting at 1.
        ref_to_sub_queries: mapping reference.n -> sorted list of 0-based
            sub_query indices that produced it.
    """
    ura = agg_input.ura
    if ura is not None:
        return _preprocess_from_ura(ura)
    return _preprocess_from_legacy(agg_input)


def _preprocess_from_ura(
    ura,
) -> tuple[list[Reference], dict[int, list[int]]]:
    """URA 2.0 path: high tier first, then medium tier, no dedup."""
    references: list[Reference] = []
    ref_to_sub_queries: dict[int, list[int]] = {}

    # High tier first so high-relevance refs get lower citation numbers.
    ordered_results: list[URAResultBase] = []
    ordered_results.extend(ura.high_results or [])
    ordered_results.extend(ura.medium_results or [])

    for n, r in enumerate(ordered_results, start=1):
        ref = _reference_from_ura(n, r)
        references.append(ref)
        ref_to_sub_queries[n] = sorted(list(r.appears_in_sub_queries or []))

    return references, ref_to_sub_queries


async def attach_source_views(
    supabase: Any,
    references: list[Reference],
    ura_results: list[URAResultBase],
) -> None:
    """Resolve `SourceView` for each reference in parallel and attach in place.

    `references[i]` must correspond 1:1 with `ura_results[i]` (same iteration
    order used by ``_preprocess_from_ura``). Each source-view lookup runs as
    its own ``asyncio.gather`` task so total wall-time is bounded by the
    slowest single Supabase round-trip rather than N sequential round-trips.

    Failure handling: if ``build_source_view`` raises (DB unavailable, missing
    row, malformed ref_id) for any reference, the warning is logged and that
    reference's ``source_view`` stays ``None``. The aggregator NEVER crashes
    because of source resolution -- this is purely additive UX metadata.
    """
    if supabase is None:
        return
    if not references or not ura_results:
        return
    if len(references) != len(ura_results):
        logger.warning(
            "attach_source_views: reference/ura length mismatch (%d vs %d) -- skipping",
            len(references),
            len(ura_results),
        )
        return

    async def _resolve_one(ura: URAResultBase) -> Any:
        try:
            return await build_source_view(supabase, ura)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "attach_source_views: build_source_view(%s) failed: %s",
                getattr(ura, "ref_id", "<no ref_id>"),
                exc,
            )
            return None

    views = await asyncio.gather(
        *(_resolve_one(ura) for ura in ura_results),
        return_exceptions=False,
    )

    for ref, view in zip(references, views):
        if view is not None:
            ref.source_view = view


def collect_ordered_ura_results(ura) -> list[URAResultBase]:
    """Return URA results in the same order ``_preprocess_from_ura`` consumes them.

    High-tier first, then medium-tier. Exposed so callers (the runner) can
    pair references with their originating URA result for ``attach_source_views``
    without duplicating tier-ordering logic.
    """
    ordered: list[URAResultBase] = []
    ordered.extend(ura.high_results or [])
    ordered.extend(ura.medium_results or [])
    return ordered


def _preprocess_from_legacy(
    agg_input: AggregatorInput,
) -> tuple[list[Reference], dict[int, list[int]]]:
    """Legacy path: reranker-driven dedup + merge (pre-URA)."""
    grouped: dict[tuple[str, str, str], list[RerankedResult]] = {}
    first_seen_order: dict[tuple[str, str, str], int] = {}
    sub_query_hits: dict[tuple[str, str, str], list[int]] = {}

    appearance = 0
    for sq_idx, sub_query in enumerate(agg_input.sub_queries or []):
        for result in getattr(sub_query, "results", []) or []:
            # Only legacy RerankedResult instances are dedup-able by identity
            # tuple. URA results (if any slipped in) are skipped here -- this
            # path exists solely for pre-URA callers.
            if not isinstance(result, RerankedResult):
                continue
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

    if not grouped:
        return references, ref_to_sub_queries

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

    return references, ref_to_sub_queries
