"""Models and dataclasses for deep_search_v2 (revised) agent.

Hierarchical supervisor pattern:
- PlanAgent uses PlannerResult from agents/models.py (NOT redefined here)
- QueryExpander uses ExpanderOutput (new)
- Aggregator uses AggregatorOutput (reused)
- Inner loop uses LoopState (mutable) and LoopResult (terminal)
- Top-level uses DeepSearchDeps (dataclass)
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, Optional

import httpx
from pydantic import BaseModel, Field
from supabase import Client as SupabaseClient

logger = logging.getLogger(__name__)


# -- LLM Output Models --------------------------------------------------------


class SearchQuery(BaseModel):
    """A single search query planned by the expander agent."""

    tool: Literal["regulations", "cases", "compliance"] = Field(
        description="Which search tool to use",
    )
    query: str = Field(
        description="Arabic search query to execute",
    )
    rationale: str = Field(
        default="",
        description="Internal rationale for this query (for logs only)",
    )


class ExpanderOutput(BaseModel):
    """Structured output from the QueryExpander agent."""

    queries: list[SearchQuery] = Field(
        description="2-4 search queries to execute",
    )
    status_message: str = Field(
        default="",
        description="Arabic status update for user (SSE)",
    )


class WeakAxis(BaseModel):
    """An axis where search results were insufficient."""

    tool: Literal["regulations", "cases", "compliance"] = Field(
        description="Which tool produced weak results",
    )
    reason: str = Field(
        description="What is missing or why the results are weak",
    )
    suggested_query: str = Field(
        description="Suggested re-search query for the next round",
    )


class AggregatorOutput(BaseModel):
    """Structured output from the aggregator LLM agent."""

    sufficient: bool = Field(
        description="True if ~80%+ of the question is covered by results",
    )
    coverage_assessment: str = Field(
        default="",
        description="Internal evaluation of what is covered vs missing",
    )
    weak_axes: list[WeakAxis] = Field(
        default_factory=list,
        description="Tools with insufficient results (empty if sufficient)",
    )
    strong_results_summary: str = Field(
        default="",
        description="Summary of what is already well covered (locked on re-search)",
    )
    synthesis_md: str = Field(
        default="",
        description="Arabic legal analysis -- ALL results combined as markdown",
    )
    answer_summary: str = Field(
        default="",
        description="1-3 sentence Arabic chat summary for user display",
    )
    citations: list[dict] = Field(
        default_factory=list,
        description="Structured citation dicts extracted from search results",
    )


class LoopResult(BaseModel):
    """Result from one Search Loop invocation, returned to PlanAgent."""

    sub_question: str = Field(
        description="The sub-question that was searched",
    )
    report_md: str = Field(
        default="",
        description="Full report markdown from ReportNode",
    )
    artifact_id: str | None = Field(
        default=None,
        description="DB artifact_id if new report was inserted",
    )
    answer_summary: str = Field(
        default="",
        description="From Aggregator",
    )
    citations: list[dict] = Field(
        default_factory=list,
        description="From Aggregator",
    )
    rounds_used: int = Field(
        default=1,
        description="How many loop rounds executed",
    )
    inner_usage: list[dict] = Field(
        default_factory=list,
        description="Per-call token usage for inner agents (expander, aggregator)",
    )
    inner_thinking: list[dict] = Field(
        default_factory=list,
        description="Thinking/reasoning traces from inner agents (expander, aggregator)",
    )
    expander_queries: list[dict] = Field(
        default_factory=list,
        description="Per-round expander output (queries generated)",
    )
    search_results_log: list[dict] = Field(
        default_factory=list,
        description="Raw search results per query (tool, query, raw_markdown)",
    )


class Citation(BaseModel):
    """Structured citation for a legal source referenced in a research report."""

    source_type: str = Field(
        description='Type of source: "regulation", "article", "section", "case", or "service"',
    )
    ref: str = Field(
        description="Unique identifier -- chunk_ref or case_ref",
    )
    title: str = Field(
        description="Arabic title of the source",
    )
    content_snippet: str = Field(
        default="",
        description="Relevant excerpt from the source",
    )
    regulation_title: Optional[str] = Field(
        default=None,
        description="Parent regulation name (if source is an article or section)",
    )
    article_num: Optional[str] = Field(
        default=None,
        description="Article number (if applicable)",
    )
    court: Optional[str] = Field(
        default=None,
        description="Court name (if source is a court case)",
    )
    relevance: str = Field(
        default="",
        description="Why this source supports the answer",
    )


# -- Dataclass: SearchResult (programmatic, not LLM output) -------------------


@dataclass
class SearchResult:
    """Result from a single search pipeline execution."""

    tool: str  # "regulations" | "cases" | "compliance"
    query: str  # The query that was executed
    raw_markdown: str  # Full pipeline output (formatted markdown)
    result_count: int  # Number of results returned
    is_mock: bool  # True for cases/compliance (mocked)


# -- Dataclass: LoopState (mutable graph state for inner loop) -----------------


@dataclass
class LoopState:
    """Mutable state that accumulates across inner loop nodes.

    Scoped to one invocation of run_search_loop().
    """

    sub_question: str
    context: str = ""
    expander_output: ExpanderOutput | None = None
    all_search_results: list[SearchResult] = field(default_factory=list)
    strong_results: list[SearchResult] = field(default_factory=list)
    aggregator_output: AggregatorOutput | None = None
    weak_axes: list[WeakAxis] = field(default_factory=list)
    round_count: int = 0
    sse_events: list[dict] = field(default_factory=list)
    inner_usage: list[dict] = field(default_factory=list)
    inner_thinking: list[dict] = field(default_factory=list)
    expander_queries: list[dict] = field(default_factory=list)
    search_results_log: list[dict] = field(default_factory=list)


# -- Dataclass: DeepSearchDeps (top-level, passed to PlanAgent) ----------------


@dataclass
class DeepSearchDeps:
    """Dependencies injected into PlanAgent tools and passed through to loop.

    artifact_id is mutable -- updated after ReportNode creates a new artifact
    or after PlanAgent calls update_report.
    """

    supabase: SupabaseClient
    embedding_fn: Callable[[str], Awaitable[list[float]]]
    user_id: str
    conversation_id: str
    case_id: str | None = None
    artifact_id: str | None = None  # Mutable -- set by ReportNode / update_report
    jina_api_key: str = ""
    http_client: httpx.AsyncClient | None = None
    use_reranker: bool = False  # Jina reranker opt-in (--rerank CLI flag)
    mock_results: dict | None = None  # For testing: {"regulations": ..., ...}
    # Top-level SSE events -- collected from all loops + PlanAgent tools
    _sse_events: list[dict] = field(default_factory=list)
    # Pre-fetched context (set by build_search_deps)
    _case_memory: str | None = None
    _previous_report_md: str | None = None
    # Runtime state (set by handle_deep_search_turn)
    _task_history_formatted: str | None = None
    _loop_results: list[LoopResult] = field(default_factory=list)
