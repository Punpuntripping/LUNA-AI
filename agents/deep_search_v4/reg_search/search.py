"""Search pipeline for the reg_search package.

Regulations-only search pipeline using hybrid search (BM25 + semantic via RRF):
- search_regulations_pipeline: embed -> 3 parallel hybrid RPCs -> optional Jina rerank -> unfold -> format

Copied and adapted from agents/deep_search_v3/executors/search_pipeline.py.
Only the regulations pipeline and shared helpers are included here.
This is intentionally a copy (not an import) to avoid circular dependencies
and allow independent evolution.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import RegSearchDeps

logger = logging.getLogger(__name__)

# Jina reranking configuration
JINA_RERANK_URL = "https://api.jina.ai/v1/rerank"
JINA_MODEL = "jina-reranker-v3"

# Default result counts (per-RPC, not split)
MATCH_COUNT = 30
MAX_RESULTS = 30


async def _safe_rpc(
    supabase: Any,
    domain: str,
    query_text: str,
    embedding: list[float],
    match_count: int,
    filter_sectors: list[str] | None,
    errors: list[str],
) -> list[dict]:
    """Call _hybrid_rpc_search, catch errors into `errors` list, return [] on failure."""
    try:
        return await _hybrid_rpc_search(
            supabase, domain, query_text, embedding, match_count,
            filter_sectors=filter_sectors,
        )
    except Exception as e:
        msg = f"hybrid_search_{domain}: {e}"
        logger.error(msg, exc_info=True)
        errors.append(msg)
        return []


# -- Regulations pipeline -----------------------------------------------------


async def search_regulations_pipeline(
    query: str,
    deps: RegSearchDeps,
    filter_sectors: list[str] | None = None,
    unfold_mode: str = "precise",
    precomputed_embedding: list[float] | None = None,
    semaphore: asyncio.Semaphore | None = None,
) -> tuple[str, int]:
    """Search regulations via embed -> 3 parallel RPCs -> Jina rerank -> unfold -> format.

    Args:
        query: Arabic search query.
        deps: RegSearchDeps with supabase, embedding_fn, jina_api_key, http_client.
        filter_sectors: Optional list of 1-4 sector names to narrow search scope.
        unfold_mode: "precise" (compact) or "detailed" (full content).
        precomputed_embedding: Pre-computed embedding vector. Skips embed step if provided.
        semaphore: Optional concurrency limiter for parallel pipeline calls.

    Returns:
        (result_markdown, result_count) tuple.
    """
    if semaphore:
        async with semaphore:
            return await _search_regulations_pipeline_inner(
                query, deps, filter_sectors, unfold_mode, precomputed_embedding,
            )
    return await _search_regulations_pipeline_inner(
        query, deps, filter_sectors, unfold_mode, precomputed_embedding,
    )


async def _search_regulations_pipeline_inner(
    query: str,
    deps: RegSearchDeps,
    filter_sectors: list[str] | None,
    unfold_mode: str,
    precomputed_embedding: list[float] | None,
) -> tuple[str, int]:
    """Inner implementation of search_regulations_pipeline."""
    # Check for mock results
    if deps.mock_results and "regulations" in deps.mock_results:
        mock_md = deps.mock_results["regulations"]
        if isinstance(mock_md, str):
            return mock_md, 2

    from .unfold_reranker import (
        collect_references,
        format_unfolded_result,
        format_unfolded_result_precise,
        unfold_article,
        unfold_article_precise,
        unfold_regulation,
        unfold_regulation_precise,
        unfold_section,
        unfold_section_precise,
    )

    _precise = unfold_mode == "precise"

    events = deps._events

    try:
        events.append({
            "type": "status",
            "text": f"جاري البحث في الأنظمة واللوائح: {query[:80]}...",
        })

        # Step 1: Embed query (skip if pre-computed)
        embedding = precomputed_embedding or await deps.embedding_fn(query)

        # Step 2: Search across 3 regulation RPCs in parallel
        events.append({"type": "status", "text": "جاري البحث في قاعدة بيانات الأنظمة..."})

        errors: list[str] = []
        articles, sections, regulations = await asyncio.gather(
            _safe_rpc(deps.supabase, "articles", query, embedding, MATCH_COUNT, filter_sectors, errors),
            _safe_rpc(deps.supabase, "sections", query, embedding, MATCH_COUNT, filter_sectors, errors),
            _safe_rpc(deps.supabase, "regulations", query, embedding, MATCH_COUNT, filter_sectors, errors),
        )

        if errors:
            for err in errors:
                events.append({"type": "error", "text": err})

        # Fallback: if sector filter returned 0 results, retry without it
        if not articles and not sections and not regulations and filter_sectors:
            logger.warning(
                "Sector filter %s returned 0 candidates — retrying without filter",
                filter_sectors,
            )
            events.append({
                "type": "status",
                "text": "لم تُعطِ تصفية القطاعات نتائج — جاري البحث بدون تصفية...",
            })
            errors.clear()
            articles, sections, regulations = await asyncio.gather(
                _safe_rpc(deps.supabase, "articles", query, embedding, MATCH_COUNT, None, errors),
                _safe_rpc(deps.supabase, "sections", query, embedding, MATCH_COUNT, None, errors),
                _safe_rpc(deps.supabase, "regulations", query, embedding, MATCH_COUNT, None, errors),
            )
            if errors:
                for err in errors:
                    events.append({"type": "error", "text": err})

        # Step 3: Merge and tag with source_type + _text for reranker
        candidates: list[dict[str, Any]] = []
        for row in articles:
            row["source_type"] = "article"
            row["_text"] = row.get("content", "")
            candidates.append(row)
        for row in sections:
            row["source_type"] = "section"
            row["_text"] = row.get("section_summary") or row.get("content", "")
            candidates.append(row)
        for row in regulations:
            row["source_type"] = "regulation"
            row["_text"] = row.get("regulation_summary", "")
            candidates.append(row)

        if not candidates:
            events.append({"type": "status", "text": "لم يتم العثور على أنظمة مطابقة."})
            return "لم يتم العثور على نتائج. لا توجد أنظمة أو مواد مطابقة للاستعلام.", 0

        logger.info(
            "Regulation candidates: %d articles, %d sections, %d regulations",
            len(articles), len(sections), len(regulations),
        )

        # Step 4: Pre-filter by RRF score (positions below threshold are almost always
        # dropped by the LLM reranker — saves unfold + reranker tokens)
        if deps.rrf_min_score > 0:
            before = len(candidates)
            candidates = [c for c in candidates if c.get("score", 0) >= deps.rrf_min_score]
            cut = before - len(candidates)
            if cut:
                logger.info("RRF pre-filter: cut %d/%d candidates below %.4f", cut, before, deps.rrf_min_score)

        # Step 5: Rank by score, filter by threshold, cap at MAX_RESULTS
        threshold = deps.score_threshold
        if deps.use_reranker:
            events.append({"type": "status", "text": f"جاري إعادة ترتيب {len(candidates)} نتيجة عبر Jina..."})
            ranked = await _rerank(query, candidates, deps.http_client, deps.jina_api_key)
            top_candidates = [
                c for c in ranked if c.get("reranker_score", 0) >= threshold
            ][:MAX_RESULTS]
        else:
            ranked = sorted(candidates, key=lambda c: c.get("score", 0.0), reverse=True)
            top_candidates = [
                c for c in ranked if c.get("score", 0) >= threshold
            ][:MAX_RESULTS]
            events.append({"type": "status", "text": f"تم اختيار {len(top_candidates)} نتيجة (عتبة: {threshold})"})

        # Step 5: Unfold top results in parallel (carry scores through)
        events.append({"type": "status", "text": f"جاري استخراج التفاصيل لأفضل {len(top_candidates)} نتيجة..."})

        async def _unfold_one(candidate: dict) -> dict[str, Any] | None:
            st = candidate.get("source_type", "")
            try:
                if _precise:
                    fn = {
                        "article": unfold_article_precise,
                        "section": unfold_section_precise,
                        "regulation": unfold_regulation_precise,
                    }.get(st)
                else:
                    fn = {
                        "article": unfold_article,
                        "section": unfold_section,
                        "regulation": unfold_regulation,
                    }.get(st)
                if fn is None:
                    return None
                u = await asyncio.to_thread(fn, deps.supabase, candidate)
                u["_score"] = candidate.get("score")
                u["_reranker_score"] = candidate.get("reranker_score")
                return u
            except Exception as e:
                logger.warning(
                    "Unfold failed for %s %s: %s",
                    candidate.get("source_type"), candidate.get("id"), e,
                )
                return None

        unfold_results = await asyncio.gather(
            *[_unfold_one(c) for c in top_candidates]
        )
        unfolded: list[dict[str, Any]] = [u for u in unfold_results if u is not None]

        if not unfolded:
            return "لم يتم العثور على نتائج كافية بعد التوسع.", 0

        # Step 6: Format into markdown (single flat ranked list)
        _fmt = format_unfolded_result_precise if _precise else format_unfolded_result
        output_lines: list[str] = [f"## نتائج البحث — {len(unfolded)} نتيجة\n"]
        for i, result in enumerate(unfolded, start=1):
            output_lines.append(_fmt(result, i))

        refs_block = collect_references(unfolded)
        if refs_block:
            output_lines.append("\n---")
            output_lines.append(refs_block)

        result_md = "\n".join(output_lines)
        result_count = len(unfolded)

        events.append({
            "type": "status",
            "text": f"تم استرجاع {result_count} نتيجة من الأنظمة واللوائح.",
        })

        return result_md, result_count

    except Exception as e:
        logger.error("Regulation search failed for '%s': %s", query[:80], e, exc_info=True)
        events.append({"type": "status", "text": "حدث خطأ أثناء البحث في الأنظمة."})
        return (
            f"خطأ أثناء البحث في الأنظمة واللوائح: {e}\n\nلم يتم العثور على نتائج بسبب خطأ تقني.",
            0,
        )


# -- Shared helpers ------------------------------------------------------------


async def _hybrid_rpc_search(
    supabase: Any,
    domain: str,
    query_text: str,
    embedding: list[float],
    match_count: int,
    full_text_weight: float = 0.2,
    semantic_weight: float = 0.8,
    rrf_k: int = 1,
    filter_sectors: list[str] | None = None,
) -> list[dict]:
    """Call a Supabase hybrid search RPC (BM25 + semantic via RRF)."""
    rpc_name = f"hybrid_search_{domain}"

    def _call() -> list[dict]:
        params: dict[str, Any] = {
            "query_text": query_text,
            "query_embedding": embedding,
            "match_count": match_count,
            "full_text_weight": full_text_weight,
            "semantic_weight": semantic_weight,
            "rrf_k": rrf_k,
            # Always include filter_sectors to resolve PostgREST
            # overload ambiguity (PGRST203). NULL = no filtering.
            "filter_sectors": filter_sectors,
        }
        result = supabase.rpc(rpc_name, params).execute()
        return result.data or []

    try:
        return await asyncio.to_thread(_call)
    except Exception as e:
        logger.error("%s RPC failed: %s", rpc_name, e, exc_info=True)
        raise


async def _rerank(
    query: str,
    candidates: list[dict[str, Any]],
    http_client: Any,
    jina_api_key: str,
) -> list[dict[str, Any]]:
    """Rerank candidates using Jina Reranker v3. Falls back to hybrid score sort."""
    if not jina_api_key:
        logger.info("No Jina API key -- falling back to hybrid score sort")
        return sorted(candidates, key=lambda c: c.get("score", 0.0), reverse=True)

    documents: list[str] = []
    for c in candidates:
        text = c.get("_text", "")
        if not text:
            text = c.get("content", "") or c.get("service_markdown", "") or c.get("title", "")
        documents.append(text[:2000] if text else "(empty)")

    try:
        response = await http_client.post(
            JINA_RERANK_URL,
            headers={
                "Authorization": f"Bearer {jina_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": JINA_MODEL,
                "query": query,
                "documents": documents,
                "top_n": len(candidates),
            },
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()

        reranked = data.get("results", [])
        if not reranked:
            logger.warning("Jina returned empty -- falling back to hybrid score")
            return sorted(candidates, key=lambda c: c.get("score", 0.0), reverse=True)

        top: list[dict[str, Any]] = []
        for item in reranked:
            idx = item.get("index", 0)
            if 0 <= idx < len(candidates):
                candidate = candidates[idx]
                candidate["reranker_score"] = item.get("relevance_score", 0.0)
                top.append(candidate)

        logger.info("Jina reranking: %d -> %d results", len(candidates), len(top))
        return top

    except Exception as e:
        logger.warning("Jina reranking failed: %s -- falling back to hybrid score", e)
        return sorted(candidates, key=lambda c: c.get("score", 0.0), reverse=True)


def _truncate(text: str, max_chars: int) -> str:
    """Truncate text, appending '...' if truncated."""
    if not text or len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."
