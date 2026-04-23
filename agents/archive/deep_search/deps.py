"""Dependencies for deep_search planner agent."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from supabase import Client as SupabaseClient

logger = logging.getLogger(__name__)


@dataclass
class SearchDeps:
    """Dependencies injected into the deep search planner by the orchestrator.

    Carries database access, embedding function, user/conversation/case IDs,
    case memory context, artifact tracking, and SSE event collection.
    """

    supabase: SupabaseClient
    embedding_fn: Callable[[str], Awaitable[list[float]]]
    user_id: str
    conversation_id: str
    case_id: str | None = None
    case_memory: str | None = None
    artifact_id: str | None = None  # Mutable -- updated by create_report tool
    _sse_events: list[dict] = field(default_factory=list)
    _tool_logs: list[dict] = field(default_factory=list)        # Per-tool call tracking (input/output/timing)
    mock_results: dict | None = None  # Per-query mock: {"regulations": ..., "cases": ..., "compliance": ...}


async def build_search_deps(
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    supabase: SupabaseClient,
    artifact_id: str | None = None,
) -> SearchDeps:
    """Build SearchDeps for a turn. Called by orchestrator.

    Pre-fetches case memory from the database when case_id is provided.
    Imports embed_text inside the function to avoid circular imports.
    """
    from agents.utils.embeddings import embed_text

    case_memory: str | None = None
    if case_id:
        try:
            result = (
                supabase.table("case_memories")
                .select("content_ar, memory_type")
                .eq("case_id", case_id)
                .is_("deleted_at", "null")
                .order("created_at", desc=True)
                .limit(20)
                .execute()
            )
            if result.data:
                lines = [
                    f"- [{m['memory_type']}] {m['content_ar']}"
                    for m in result.data
                ]
                case_memory = "\n".join(lines)
        except Exception as e:
            logger.warning("Error loading case memory %s: %s", case_id, e)

    return SearchDeps(
        supabase=supabase,
        embedding_fn=embed_text,
        user_id=user_id,
        conversation_id=conversation_id,
        case_id=case_id,
        case_memory=case_memory,
        artifact_id=artifact_id,
    )
