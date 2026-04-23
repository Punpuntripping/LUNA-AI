"""Models and dataclasses for the compliance_search domain search loop.

Compliance/government-services search loop (pydantic_graph):
- ExpanderOutput: LLM query expansion result
- ServiceDecision: LLM keep/drop decision for one service result
- ServiceRerankerOutput: LLM reranker output — classification only, no synthesis
- ComplianceSearchResult: Final result returned to caller (raw kept service dicts)
- WeakAxis: Identified gap for retry
- SearchResult: Programmatic search result (dataclass)
- LoopState: Mutable graph state (dataclass)
- ComplianceSearchDeps: Dependencies injected into graph (dataclass)
- RegHit: Regulation signal used to trigger compliance search (URA pipeline)
- ComplianceURASlice: URA-shaped compliance results (URA pipeline)
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, Optional

import httpx
from pydantic import BaseModel, Field
from supabase import Client as SupabaseClient


# -- Pydantic models (LLM output) ---------------------------------------------


class WeakAxis(BaseModel):
    """Identified gap in search results that needs re-searching."""

    reason: str = Field(
        description="Why this aspect is weak (Arabic)",
    )
    suggested_query: str = Field(
        description="Specific query to try on retry (Arabic)",
    )


class ExpanderOutput(BaseModel):
    """Output from the QueryExpander agent."""

    queries: list[str] = Field(
        description="Arabic search queries, one per distinct compliance need",
    )
    rationales: list[str] = Field(
        default_factory=list,
        description="Internal rationale per query (logs only, not sent to LLM)",
    )
    task_count: int = Field(
        default=0,
        description="Number of distinct compliance tasks identified",
    )


class ServiceDecision(BaseModel):
    """LLM's keep/drop decision for one service result."""

    position: int
    action: Literal["keep", "drop"]
    relevance: Literal["high", "medium"] | None = None
    reasoning: str = ""


class ServiceRerankerOutput(BaseModel):
    """Output from the ServiceReranker agent — classification only, no synthesis."""

    sufficient: bool = False
    decisions: list[ServiceDecision]
    weak_axes: list[WeakAxis] = []
    summary_note: str = ""


class ComplianceSearchResult(BaseModel):
    """Final result from run_compliance_search(), returned to the caller."""

    kept_results: list[dict] = Field(
        default_factory=list,
        description="Raw service rows kept by the reranker",
    )
    quality: Literal["strong", "moderate", "weak"] = Field(
        description="Overall quality assessment",
    )
    domain: Literal["compliance"] = "compliance"
    queries_used: list[str] = Field(
        default_factory=list,
        description="All queries executed across all rounds",
    )
    rounds_used: int = Field(
        default=1,
        description="How many rounds (1-3)",
    )


# -- Dataclasses (programmatic, not LLM output) --------------------------------


@dataclass
class SearchResult:
    """Result from a single search pipeline execution."""

    query: str
    raw_markdown: str
    result_count: int


@dataclass
class LoopState:
    """Mutable graph state for the compliance_search loop."""

    focus_instruction: str
    user_context: str
    log_id: str = ""
    round_count: int = 0
    expander_output: ExpanderOutput | None = None
    all_search_results: list[SearchResult] = field(default_factory=list)
    all_results_flat: list[dict] = field(default_factory=list)
    kept_results: list[dict] = field(default_factory=list)
    reranker_output: ServiceRerankerOutput | None = None
    weak_axes: list[WeakAxis] = field(default_factory=list)
    queries_used: list[str] = field(default_factory=list)
    sse_events: list[dict] = field(default_factory=list)
    inner_usage: list[dict] = field(default_factory=list)
    search_results_log: list[dict] = field(default_factory=list)
    round_summaries: list[dict] = field(default_factory=list)


@dataclass
class ComplianceSearchDeps:
    """Dependencies injected into the compliance_search graph."""

    supabase: SupabaseClient
    embedding_fn: Callable[[str], Awaitable[list[float]]]
    jina_api_key: str = ""
    http_client: httpx.AsyncClient | None = None
    use_reranker: bool = False
    mock_results: dict | None = None  # For testing: {"compliance": "...markdown..."}
    _events: list[dict] = field(default_factory=list)
    _search_log: list[dict] = field(default_factory=list)


# -- URA pipeline dataclasses --------------------------------------------------


@dataclass
class RegHit:
    """A regulation signal used to trigger compliance search from a partial URA."""

    ref_id: str
    regulation_title: str
    article_num: str | None = None
    section_title: str | None = None


@dataclass
class ComplianceURASlice:
    """URA-shaped compliance results, ready for merge_to_ura().

    `results` is a list of URAResult-compatible dicts so that this module
    does not import from deep_search_v3 (avoids circular imports).
    """

    results: list[dict] = field(default_factory=list)
    queries_used: list[str] = field(default_factory=list)
