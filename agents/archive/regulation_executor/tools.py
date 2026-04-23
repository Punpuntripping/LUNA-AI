"""Tool functions for regulation_executor agent.

Two tools registered on the executor:
1. search_and_retrieve — main 3-stage pipeline (embed, search, rerank, unfold)
2. fetch_parent_section — contextual lookup for an article's parent section
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic_ai import RunContext

from .agent import regulation_executor
from .deps import RegulationSearchDeps
from .regulation_unfold import (
    collect_references,
    format_unfolded_result,
    unfold_article,
    unfold_regulation,
    unfold_section,
)

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────────

JINA_RERANK_URL = "https://api.jina.ai/v1/rerank"
JINA_MODEL = "jina-reranker-v3"

# Batch sizes for the formatted output
BATCH_1_SIZE = 5
BATCH_2_SIZE = 5


# ── Standalone retrieval pipeline ───────────────────────────────────────────


async def run_retrieval_pipeline(
    query: str,
    deps: RegulationSearchDeps,
    match_count: int = 30,
    top_n: int = 10,
) -> str:
    """Execute the 3-stage retrieval pipeline mechanically (no LLM).

    Called by the runner for the initial mechanical retrieval (saves 1 LLM
    call), and by the search_and_retrieve tool when the agent re-searches
    with a reformulated query.

    Args:
        query: Arabic legal query to search for.
        deps: RegulationSearchDeps with supabase, embedding_fn, etc.
        match_count: Total candidates to retrieve across all sources.
        top_n: Number of top results to return after reranking.

    Returns:
        Formatted markdown string with search results.
    """
    import time as _time

    pipeline_start = _time.time()
    logger.info("run_retrieval_pipeline called: %s", query[:120])
    deps._events.append({
        "type": "status",
        "text": f"جاري تضمين الاستعلام وبدء البحث: {query[:80]}...",
    })

    # ── Step 1: Embed the query ──────────────────────────────────────────────
    embedding = await deps.embedding_fn(query)

    # ── Step 2: Parallel semantic search via Supabase RPCs ───────────────────
    deps._events.append({
        "type": "status",
        "text": "جاري البحث في قاعدة بيانات الأنظمة...",
    })

    # Distribute match_count: 50% articles, 33% sections, 17% regulations
    article_count = max(1, match_count // 2)  # 15
    section_count = max(1, match_count // 3)  # 10
    regulation_count = max(1, match_count - article_count - section_count)  # 5

    articles_data, sections_data, regulations_data = await _parallel_search(
        deps.supabase,
        embedding,
        article_count,
        section_count,
        regulation_count,
    )

    # ── Step 3: Merge and tag with source_type ───────────────────────────────
    candidates: list[dict[str, Any]] = []

    for row in articles_data:
        row["source_type"] = "article"
        row["_text"] = row.get("content", "")
        candidates.append(row)

    for row in sections_data:
        row["source_type"] = "section"
        row["_text"] = row.get("section_summary") or row.get("content", "")
        candidates.append(row)

    for row in regulations_data:
        row["source_type"] = "regulation"
        row["_text"] = row.get("regulation_summary", "")
        candidates.append(row)

    if not candidates:
        logger.warning("All RPCs returned empty results for query: %s", query[:80])
        deps._retrieval_logs.append({
            "query": query,
            "match_count": match_count,
            "top_n": top_n,
            "articles_count": 0,
            "sections_count": 0,
            "regulations_count": 0,
            "total_candidates": 0,
            "top_results": [],
            "duration_s": round(_time.time() - pipeline_start, 2),
            "status": "empty",
        })
        return "لم يتم العثور على نتائج. لا توجد أنظمة أو مواد مطابقة للاستعلام."

    logger.info(
        "Candidates: %d articles, %d sections, %d regulations",
        len(articles_data), len(sections_data), len(regulations_data),
    )

    # ── Step 4: Cross-encoder reranking ──────────────────────────────────────
    deps._events.append({
        "type": "status",
        "text": f"جاري إعادة ترتيب {len(candidates)} نتيجة...",
    })

    top_candidates = await _rerank_candidates(
        deps.http_client,
        deps.jina_api_key,
        query,
        candidates,
        top_n,
    )

    # ── Step 5: Unfold all top results ───────────────────────────────────────
    deps._events.append({
        "type": "status",
        "text": f"جاري استخراج التفاصيل لأفضل {len(top_candidates)} نتيجة...",
    })

    unfolded_results: list[dict[str, Any]] = []
    for candidate in top_candidates:
        try:
            source_type = candidate.get("source_type", "")
            if source_type == "article":
                unfolded = unfold_article(deps.supabase, candidate)
            elif source_type == "section":
                unfolded = unfold_section(deps.supabase, candidate)
            elif source_type == "regulation":
                unfolded = unfold_regulation(deps.supabase, candidate)
            else:
                logger.warning("Unknown source_type: %s", source_type)
                continue
            unfolded_results.append(unfolded)
        except Exception as e:
            logger.warning(
                "Failed to unfold %s %s: %s",
                candidate.get("source_type"),
                candidate.get("id"),
                e,
            )
            continue

    # ── Track retrieval for structured logging ───────────────────────────────
    top_results_summary = []
    for c in top_candidates:
        top_results_summary.append({
            "source_type": c.get("source_type", ""),
            "title": c.get("title", c.get("regulation_title", ""))[:120],
            "distance": round(c.get("distance", 0.0), 4),
            "reranker_score": round(c.get("reranker_score", 0.0), 4),
            "id": c.get("id", ""),
        })

    deps._retrieval_logs.append({
        "query": query,
        "match_count": match_count,
        "top_n": top_n,
        "articles_count": len(articles_data),
        "sections_count": len(sections_data),
        "regulations_count": len(regulations_data),
        "total_candidates": len(candidates),
        "top_results": top_results_summary,
        "unfolded_count": len(unfolded_results),
        "duration_s": round(_time.time() - pipeline_start, 2),
        "status": "success",
    })

    if not unfolded_results:
        return "لم يتم العثور على نتائج كافية بعد التوسع. حاول صياغة الاستعلام بشكل مختلف."

    # ── Step 6: Split into two batches ───────────────────────────────────────
    batch_1 = unfolded_results[:BATCH_1_SIZE]
    batch_2 = unfolded_results[BATCH_1_SIZE : BATCH_1_SIZE + BATCH_2_SIZE]

    # ── Step 7: Collect references ───────────────────────────────────────────
    references_block = collect_references(unfolded_results)

    # ── Step 8: Format and return ────────────────────────────────────────────
    output_lines: list[str] = []

    output_lines.append(f"## نتائج البحث — {len(unfolded_results)} نتيجة\n")

    # Batch 1
    output_lines.append("---")
    output_lines.append(
        f"## الدفعة الأولى (الأعلى صلة) — {len(batch_1)} نتائج\n"
    )
    for i, result in enumerate(batch_1, start=1):
        output_lines.append(format_unfolded_result(result, i))

    # Batch 2
    if batch_2:
        output_lines.append("---")
        output_lines.append(
            f"## الدفعة الثانية — {len(batch_2)} نتائج\n"
        )
        for i, result in enumerate(batch_2, start=BATCH_1_SIZE + 1):
            output_lines.append(format_unfolded_result(result, i))

    # References
    if references_block:
        output_lines.append("\n---")
        output_lines.append(references_block)

    deps._events.append({
        "type": "status",
        "text": f"تم استرجاع {len(unfolded_results)} نتيجة من الأنظمة واللوائح.",
    })

    return "\n".join(output_lines)


# ── Tool 1: search_and_retrieve (thin wrapper) ────────────────────────────


@regulation_executor.tool(retries=1)
async def search_and_retrieve(
    ctx: RunContext[RegulationSearchDeps],
    query: str,
    match_count: int = 30,
    top_n: int = 10,
) -> str:
    """Re-search Saudi regulations with a reformulated query.

    Use this tool ONLY when the pre-fetched results provided in the
    message are insufficient or weak. Reformulate the query differently
    before calling this tool.

    Args:
        query: Reformulated Arabic legal query to search for.
        match_count: Total candidates to retrieve across all sources (default 30).
        top_n: Number of top results to return after reranking (default 10).
    """
    return await run_retrieval_pipeline(query, ctx.deps, match_count, top_n)


# ── Tool 2: fetch_parent_section ─────────────────────────────────────────────


@regulation_executor.tool(retries=0)
async def fetch_parent_section(
    ctx: RunContext[RegulationSearchDeps],
    article_id: str,
) -> str:
    """Fetch the parent section of an article for additional context.

    Use this when an article's content alone is insufficient to understand
    the legal provision. Returns the section title, summary, and context.

    Args:
        article_id: UUID of the article to look up the parent section for.
    """
    logger.info("fetch_parent_section called for article_id: %s", article_id)

    try:
        # Look up article's section_id
        article = (
            ctx.deps.supabase.table("articles")
            .select("section_id")
            .eq("id", article_id)
            .maybe_single()
            .execute()
        )

        if not article or not article.data:
            return f"لم يُعثر على المادة: {article_id}"

        section_id = article.data.get("section_id")
        if not section_id:
            return "لا يوجد باب أعلى لهذه المادة"

        # Fetch section details
        section = (
            ctx.deps.supabase.table("sections")
            .select("title, section_summary, section_context")
            .eq("id", section_id)
            .maybe_single()
            .execute()
        )

        if not section or not section.data:
            return f"لم يُعثر على الباب: {section_id}"

        title = section.data.get("title", "بدون عنوان")
        summary = section.data.get("section_summary", "")
        context = section.data.get("section_context", "")

        lines: list[str] = [f"## الباب/الفصل: {title}"]
        if summary:
            lines.append(f"\n**ملخص:** {summary}")
        if context:
            lines.append(f"\n**السياق:** {context}")

        return "\n".join(lines)

    except Exception as e:
        logger.warning("Error in fetch_parent_section: %s", e)
        return f"خطأ أثناء جلب الباب الأعلى: {e}"


# ── Internal helpers ─────────────────────────────────────────────────────────


async def _parallel_search(
    supabase: Any,
    embedding: list[float],
    article_count: int,
    section_count: int,
    regulation_count: int,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Run 3 Supabase RPC searches in parallel using asyncio.to_thread.

    The Supabase Python client is synchronous, so we wrap each call in
    asyncio.to_thread to run them concurrently without blocking.

    Returns:
        Tuple of (articles_data, sections_data, regulations_data).
        Any RPC that fails returns an empty list (other results are preserved).
    """

    def _search_articles() -> list[dict]:
        try:
            result = supabase.rpc(
                "search_articles",
                {"query_embedding": embedding, "match_count": article_count},
            ).execute()
            return result.data or []
        except Exception as e:
            logger.warning("search_articles RPC failed: %s", e)
            return []

    def _search_sections() -> list[dict]:
        try:
            result = supabase.rpc(
                "search_sections",
                {"query_embedding": embedding, "match_count": section_count},
            ).execute()
            return result.data or []
        except Exception as e:
            logger.warning("search_sections RPC failed: %s", e)
            return []

    def _search_regulations() -> list[dict]:
        try:
            result = supabase.rpc(
                "search_regulations",
                {"query_embedding": embedding, "match_count": regulation_count},
            ).execute()
            return result.data or []
        except Exception as e:
            logger.warning("search_regulations RPC failed: %s", e)
            return []

    articles, sections, regulations = await asyncio.gather(
        asyncio.to_thread(_search_articles),
        asyncio.to_thread(_search_sections),
        asyncio.to_thread(_search_regulations),
    )

    return articles, sections, regulations


async def _rerank_candidates(
    http_client: Any,
    jina_api_key: str,
    query: str,
    candidates: list[dict[str, Any]],
    top_n: int,
) -> list[dict[str, Any]]:
    """Rerank candidates using Jina Reranker v3.

    Falls back to cosine distance sorting if Jina is unavailable or fails.

    Args:
        http_client: httpx.AsyncClient for the Jina API call.
        jina_api_key: Jina API key for authorization.
        query: Original search query.
        candidates: Merged candidate list from all three RPCs.
        top_n: Number of top results to return.

    Returns:
        Top N candidates sorted by relevance.
    """
    # Skip Jina if no API key
    if not jina_api_key:
        logger.info("No Jina API key -- falling back to cosine distance sort")
        return _cosine_fallback(candidates, top_n)

    # Build document texts for reranking
    documents: list[str] = []
    for c in candidates:
        text = c.get("_text", "")
        if not text:
            text = c.get("content", "") or c.get("title", "")
        # Jina has a document length limit; truncate very long texts
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
                "top_n": top_n,
            },
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()

        reranked_results = data.get("results", [])
        if not reranked_results:
            logger.warning("Jina returned empty results -- falling back to cosine")
            return _cosine_fallback(candidates, top_n)

        # Map reranked indices back to candidates
        top_candidates: list[dict[str, Any]] = []
        for item in reranked_results:
            idx = item.get("index", 0)
            if 0 <= idx < len(candidates):
                candidate = candidates[idx]
                candidate["reranker_score"] = item.get("relevance_score", 0.0)
                top_candidates.append(candidate)

        logger.info(
            "Jina reranking complete: %d candidates -> %d results",
            len(candidates), len(top_candidates),
        )
        return top_candidates

    except Exception as e:
        logger.warning("Jina reranking failed: %s -- falling back to cosine", e)
        return _cosine_fallback(candidates, top_n)


def _cosine_fallback(
    candidates: list[dict[str, Any]], top_n: int
) -> list[dict[str, Any]]:
    """Sort candidates by cosine distance (ascending = more similar) and take top N."""
    sorted_candidates = sorted(
        candidates,
        key=lambda c: c.get("distance", float("inf")),
    )
    return sorted_candidates[:top_n]
