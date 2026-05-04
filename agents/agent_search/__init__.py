"""agent_search — publishing adapter for deep_search results.

This package is intentionally NOT an LLM agent. It consumes the
``AggregatorOutput`` produced by ``agents/deep_search_v4`` and persists it
into the ``artifacts`` table (which migration 026 will rename to
``workspace_items``). It also emits the SSE events that the chat stream
forwards to the frontend.

Wave 8 Cut-1: this package replaces the inline persistence block previously
embedded at ``agents/orchestrator.py:436-510``. Same behavior, but isolated
so it can be tested independently and so the orchestrator stays thin.
"""
from __future__ import annotations

from agents.agent_search.deps import SearchPublishDeps
from agents.agent_search.models import SearchPublishInput, SearchPublishOutput
from agents.agent_search.publisher import publish_search_result

__all__ = [
    "publish_search_result",
    "SearchPublishInput",
    "SearchPublishOutput",
    "SearchPublishDeps",
]
