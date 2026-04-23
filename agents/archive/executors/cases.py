"""Cases executor -- search tool registration for deep_search_v3.

Provides the search_cases tool that wraps the cases search pipeline.
The tool is registered on a given executor Agent via register_search_cases().
"""
from __future__ import annotations

import logging

from pydantic_ai import Agent, RunContext

from ..models import ExecutorDeps, ExecutorResult

logger = logging.getLogger(__name__)


def register_search_cases(
    executor: Agent[ExecutorDeps, ExecutorResult],
) -> None:
    """Register the search_cases tool on the executor agent."""

    @executor.tool
    async def search_cases(
        ctx: RunContext[ExecutorDeps],
        query: str,
    ) -> str:
        """Search Saudi court rulings, judicial precedents, and legal principles.

        Queries the cases vector table.
        Returns formatted markdown results.

        Args:
            query: Arabic search query describing a type of legal dispute.
        """
        from .search_pipeline import search_cases_pipeline

        logger.info("search_cases: query='%s'", query[:80])

        ctx.deps._events.append({
            "type": "status",
            "text": f"جاري البحث في الأحكام القضائية: {query[:50]}...",
        })

        result_md, result_count = await search_cases_pipeline(
            query=query,
            deps=ctx.deps,
        )

        logger.info(
            "search_cases: %d results for '%s'",
            result_count,
            query[:60],
        )

        # Log raw search results (full, before truncation)
        ctx.deps._search_log.append({
            "domain": "cases",
            "query": query,
            "result_count": result_count,
            "raw_markdown": result_md,
        })

        # Truncate to avoid context overflow
        if len(result_md) > 15_000:
            result_md = result_md[:15_000] + "\n\n... (تم اقتطاع النتائج للاختصار)"

        return result_md
