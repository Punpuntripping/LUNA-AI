"""Models and dataclasses for the compliance_search domain search loop.

Compliance/government-services search loop (pydantic_graph):
- ExpanderOutput: LLM query expansion result
- ServiceDecision: LLM keep/drop decision for one service result
- ServiceRerankerOutput: LLM reranker output — classification only, no synthesis
- ComplianceSearchResult: Final result returned to caller (raw kept service dicts)
- WeakAxis: Identified gap for retry
- SearchResult: Programmatic search result (dataclass)
- LoopState: Mutable graph state (dataclass). Carries
  ``per_query_service_refs`` so the orchestrator can recover per-sub-query
  attribution after the graph returns (URA 2.0 / Loop V2 Wave D).
- ComplianceSearchDeps: Dependencies injected into graph (dataclass)
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


class RerankedServiceResult(BaseModel):
    """A single government service result kept by the reranker.

    Mirrors ``RerankedCaseResult`` / ``RerankedResult`` in the other domains.
    Fields come from the raw service DB row; they are assembled programmatically
    (not by the LLM) after the reranker has issued a keep decision.
    """

    source_type: str = Field(default="gov_service")
    service_ref: str = Field(default="", description="Stable service identifier")
    title: str = Field(default="", description="Arabic service name")
    content: str = Field(
        default="",
        description="Compact service context — reranker AND aggregator view",
    )
    provider_name: str = Field(default="", description="Government entity name")
    service_url: str = Field(default="", description="Service URL")
    sectors: list[str] = Field(
        default_factory=list,
        description="Unified ministry-sector tags (same vocab as cases.legal_domains)",
    )
    is_proactive: bool = Field(
        default=False, description="Whether this is a proactive government service",
    )
    score: float = Field(default=0.0, description="RRF hybrid score")
    relevance: Literal["high", "medium"] = Field(default="medium")
    reasoning: str = Field(default="", description="Arabic explanation of relevance")


class ComplianceSearchResult(BaseModel):
    """Final result from run_compliance_search(), returned to the caller."""

    kept_results: list[RerankedServiceResult] = Field(
        default_factory=list,
        description="Typed service results kept by the reranker",
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
    """Mutable graph state for the compliance_search loop.

    ``per_query_service_refs`` is populated by ``SearchNode.run`` as each
    sub-query is dispatched: it maps expander query string ->
    ``list[service_ref]`` recording which services were surfaced by that
    specific mini-query. The orchestrator hands this mapping to the
    ``compliance_to_rqr`` adapter so the URA gets per-sub-query attribution
    (Loop V2 plan Q1 / Option A).
    """

    focus_instruction: str
    user_context: str
    log_id: str = ""
    round_count: int = 0
    expander_output: ExpanderOutput | None = None
    all_search_results: list[SearchResult] = field(default_factory=list)
    all_results_flat: list[dict] = field(default_factory=list)
    kept_results: list[RerankedServiceResult] = field(default_factory=list)
    reranker_output: ServiceRerankerOutput | None = None
    weak_axes: list[WeakAxis] = field(default_factory=list)
    queries_used: list[str] = field(default_factory=list)
    sse_events: list[dict] = field(default_factory=list)
    inner_usage: list[dict] = field(default_factory=list)
    search_results_log: list[dict] = field(default_factory=list)
    round_summaries: list[dict] = field(default_factory=list)
    per_query_service_refs: dict[str, list[str]] = field(default_factory=dict)
    # Planner-supplied caps (cap defaults match orchestrator FullLoopDeps).
    expander_max_queries: int | None = None
    # Planner-supplied sector list. Forwarded by SearchNode to
    # search_compliance_raw -> hybrid_search_services' ``filter_sectors``
    # array-overlap filter. NOTE: the planner currently canonicalizes
    # against the regulations vocabulary, whose names differ from the
    # unified vocab stored in services.sectors — see migration plan D2.
    sectors_override: list[str] | None = None


@dataclass
class ComplianceSearchDeps:
    """Dependencies injected into the compliance_search graph."""

    supabase: SupabaseClient
    embedding_fn: Callable[[str], Awaitable[list[float]]]
    jina_api_key: str = ""
    http_client: httpx.AsyncClient | None = None
    use_reranker: bool = False
    mock_results: dict | None = None  # For testing: {"compliance": "...markdown..."}
    model_override: str | None = None  # Optional model override for the reranker
    reranker_max_keep: int = 5  # Max results to keep — single flat cap over the total pool
    # Dynamic result-budget model (MODE_PROFILES.md §1). When set by the
    # planner/orchestrator, the keep cap is derived at runtime as
    # ceil(result_budget / max(N, 3)) from the expander's actual query count
    # N — and ``reranker_max_keep`` above is ignored. When None (CLI / monitor
    # path), the fixed ``reranker_max_keep`` is used.
    result_budget: int | None = None
    _events: list[dict] = field(default_factory=list)
    _search_log: list[dict] = field(default_factory=list)
