"""Regulations executor -- search tool registration for deep_search_v3.

Provides the search_regulations tool that wraps the regulations search pipeline.
The tool is registered on a given executor Agent via register_search_regulations().
"""
from __future__ import annotations

import logging

from pydantic_ai import Agent, RunContext

from ..models import ExecutorDeps, ExecutorResult

logger = logging.getLogger(__name__)


def register_search_regulations(
    executor: Agent[ExecutorDeps, ExecutorResult],
) -> None:
    """Register the search_regulations tool on the executor agent."""

    @executor.tool
    async def search_regulations(
        ctx: RunContext[ExecutorDeps],
        query: str,
    ) -> str:
        """Search Saudi regulations, laws, bylaws, and legal articles.

        Queries 3 vector tables: articles, sections, and regulations.
        Returns formatted markdown results.

        Args:
            query: Arabic search query describing a legal concept.
        """
        from .search_pipeline import search_regulations_pipeline

        logger.info("search_regulations: query='%s'", query[:80])

        ctx.deps._events.append({
            "type": "status",
            "text": f"جاري البحث في الأنظمة: {query[:50]}...",
        })

        result_md, result_count = await search_regulations_pipeline(
            query=query,
            deps=ctx.deps,
        )

        logger.info(
            "search_regulations: %d results for '%s'",
            result_count,
            query[:60],
        )

        # Log raw search results (full, before truncation — includes scores)
        ctx.deps._search_log.append({
            "domain": "regulations",
            "query": query,
            "result_count": result_count,
            "raw_markdown": result_md,
        })


        # Truncate to avoid context overflow (MiniMax 204k limit)
        if len(result_md) > 15_000:
            result_md = result_md[:15_000] + "\n\n... (تم اقتطاع النتائج للاختصار)"

        return result_md
