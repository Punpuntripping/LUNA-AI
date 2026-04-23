"""Dependencies for regulation_executor agent."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx
from supabase import Client as SupabaseClient

logger = logging.getLogger(__name__)


@dataclass
class RegulationSearchDeps:
    """Dependencies injected into every tool call.

    Leaner than the planner's SearchDeps -- no user_id, conversation_id,
    case_id, etc.  This executor only needs database access, embedding
    function, and the Jina reranker client.
    """

    supabase: SupabaseClient
    embedding_fn: Callable[[str], Awaitable[list[float]]]
    jina_api_key: str
    http_client: httpx.AsyncClient
    _events: list[dict] = field(default_factory=list)
    _retrieval_logs: list[dict] = field(default_factory=list)   # Per-query retrieval tracking
