"""Search pipeline for the compliance_search domain loop.

Adapted from agents/deep_search_v3/executors/search_pipeline.py
(search_compliance_pipeline and its helpers). This package owns its
own copy to avoid cross-package imports and allow independent evolution.

Pipeline: mock check -> embed -> hybrid_search_services RPC -> return raw dicts

Formatting and reranking are handled downstream by RerankerNode and
reranker_prompts.py — this module is a pure search/retrieval layer.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import ComplianceSearchDeps

logger = logging.getLogger(__name__)

# Jina reranking configuration (kept for backward compat — not called by search_compliance_raw)
JINA_RERANK_URL = "https://api.jina.ai/v1/rerank"
JINA_MODEL = "jina-reranker-v3"

# Result count for RPC — reduced from 30 to 20 (RerankerNode handles capping)
MATCH_COUNT = 20


# -- Public entry point --------------------------------------------------------


async def search_compliance_raw(
    query: str,
    deps: "ComplianceSearchDeps",
    sectors: list[str] | None = None,
) -> list[dict]:
    """Search government services and return raw candidate dicts.

    Returns raw service rows from hybrid_search_services RPC.
    No formatting, no capping — the RerankerNode handles those.

    Args:
        query: Arabic search query.
        deps: ComplianceSearchDeps with supabase, embedding_fn.
        sectors: Optional unified-vocabulary sector tags. When non-empty,
            forwarded to the RPC's ``filter_sectors`` (array-overlap filter).
            An empty/None value means "no filter" — never pass ``[]`` to the
            RPC (``sectors && '{}'`` is always false → zero rows).

    Returns:
        List of raw service row dicts with all fields from the RPC,
        sorted by RRF score DESC. Empty list on failure.

    Fields present in each returned dict:
        id, service_ref, service_name_ar, provider_name, service_context,
        service_url, url, is_most_used, is_proactive, sectors, entity_id,
        score (RRF score).
    """
    # Mock check — only honour list mocks in raw mode
    if deps.mock_results and "compliance" in deps.mock_results:
        mock_val = deps.mock_results["compliance"]
        if isinstance(mock_val, list):
            logger.info("search_compliance_raw: returning mock list (%d items)", len(mock_val))
            return mock_val
        # String mock (old format) — no mock in raw mode
        logger.debug(
            "search_compliance_raw: mock_results['compliance'] is a string (old format) "
            "— ignoring, returning []"
        )
        return []

    try:
        # Step 1: Embed query
        embedding = await deps.embedding_fn(query)

        # Step 2: Hybrid search via RPC
        candidates = await _hybrid_rpc_search(
            deps.supabase, "services", query, embedding, MATCH_COUNT,
            filter_sectors=sectors or None,
        )

        if not candidates:
            logger.info(
                "search_compliance_raw: no candidates for '%s'",
                query[:80],
            )
            return []

        logger.info(
            "search_compliance_raw: %d candidates for '%s'",
            len(candidates),
            query[:80],
        )

        # Step 3: Sort by RRF score DESC (RPC already returns sorted, but make it explicit)
        candidates.sort(key=lambda c: c.get("score", 0.0), reverse=True)

        return candidates

    except Exception as e:
        logger.error(
            "search_compliance_raw failed for '%s': %s",
            query[:80],
            e,
            exc_info=True,
        )
        return []


# -- Shared helpers ------------------------------------------------------------


async def _hybrid_rpc_search(
    supabase: Any,
    domain: str,
    query_text: str,
    embedding: list[float],
    match_count: int,
    full_text_weight: float = 0.2,
    semantic_weight: float = 0.8,
    filter_sectors: list[str] | None = None,
) -> list[dict]:
    """Call a Supabase hybrid search RPC (BM25 + semantic via RRF).

    ``filter_sectors`` is passed through to the RPC only when non-empty;
    otherwise it is omitted so the RPC default (``NULL`` = no filter)
    applies. An empty list must never reach the RPC — ``sectors && '{}'``
    is always false and would zero out every row.
    """
    rpc_name = f"hybrid_search_{domain}"

    def _call() -> list[dict]:
        try:
            params: dict = {
                "query_text": query_text,
                "query_embedding": embedding,
                "match_count": match_count,
                "full_text_weight": full_text_weight,
                "semantic_weight": semantic_weight,
            }
            if filter_sectors:
                params["filter_sectors"] = filter_sectors
            result = supabase.rpc(rpc_name, params).execute()
            return result.data or []
        except Exception as e:
            logger.warning("%s RPC failed: %s", rpc_name, e)
            return []

    return await asyncio.to_thread(_call)


async def _rerank(
    query: str,
    candidates: list[dict[str, Any]],
    http_client: Any,
    jina_api_key: str,
    top_n: int,
) -> list[dict[str, Any]]:
    """Rerank candidates using Jina Reranker v3. Falls back to score sort.

    Not called by search_compliance_raw — the LLM reranker (RerankerNode)
    handles classification. Kept for potential future use.
    """
    if not jina_api_key:
        logger.info("No Jina API key -- falling back to hybrid score sort")
        return _score_fallback(candidates, top_n)

    documents: list[str] = []
    for c in candidates:
        text = c.get("_text", "")
        if not text:
            text = (
                c.get("service_context", "")
                or c.get("service_name_ar", "")
            )
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

        reranked = data.get("results", [])
        if not reranked:
            logger.warning("Jina returned empty -- falling back to hybrid score")
            return _score_fallback(candidates, top_n)

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
        logger.warning(
            "Jina reranking failed: %s -- falling back to hybrid score", e,
        )
        return _score_fallback(candidates, top_n)


def _score_fallback(
    candidates: list[dict[str, Any]],
    top_n: int,
) -> list[dict[str, Any]]:
    """Sort by hybrid RRF score (descending, higher=better) and take top N.

    Not called by search_compliance_raw — kept for potential future use.
    """
    return sorted(
        candidates,
        key=lambda c: c.get("score", 0.0),
        reverse=True,
    )[:top_n]
