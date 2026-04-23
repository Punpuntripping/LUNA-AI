"""Models and dataclasses for the reg_search domain search loop.

Regulations-only search loop (pydantic_graph):
- ExpanderOutput: LLM query expansion result
- AggregatorOutput: LLM aggregation/synthesis result
- RegSearchResult: Final result returned to caller
- Citation: Regulations-only citation (no court field)
- WeakAxis: Identified gap for retry
- SearchResult: Programmatic search result (dataclass)
- LoopState: Mutable graph state (dataclass)
- RegSearchDeps: Dependencies injected into graph (dataclass)
"""
from __future__ import annotations

import json as _json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, Optional

import httpx
from pydantic import BaseModel, Field, field_validator
from supabase import Client as SupabaseClient


# -- Pydantic models (LLM output) ---------------------------------------------


class Citation(BaseModel):
    """Structured citation for a regulations source.

    Regulations-only variant -- no court field.
    """

    source_type: str = Field(
        description='Type of source: "regulation", "article", or "section"',
    )
    ref: str = Field(
        description="Unique identifier -- chunk_ref",
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
    relevance: str = Field(
        default="",
        description="Why this source supports the answer",
    )


class WeakAxis(BaseModel):
    """Identified gap in search results that needs re-searching."""

    reason: str = Field(
        description="Why this aspect is weak (Arabic)",
    )
    suggested_query: str = Field(
        description="Specific query to try on retry",
    )


class ExpanderOutput(BaseModel):
    """Output from the QueryExpander agent."""

    queries: list[str] = Field(
        description="2-10 Arabic search queries targeting single legal concepts",
    )
    rationales: list[str] = Field(
        default_factory=list,
        description="Internal rationale per query (logs only, not sent to LLM)",
    )
    sectors: list[str] | None = Field(
        default=None,
        description="1-4 legal sector names for filtering, or null if >4 or ambiguous",
    )

    @field_validator("sectors", mode="before")
    @classmethod
    def _parse_sectors(cls, v):
        if isinstance(v, str):
            try:
                parsed = _json.loads(v)
                if isinstance(parsed, list):
                    return parsed
            except (_json.JSONDecodeError, TypeError):
                pass
        return v


class AggregatorOutput(BaseModel):
    """Output from the Aggregator agent."""

    sufficient: bool = Field(
        description="True if results adequately answer the question",
    )
    quality: Literal["strong", "moderate", "weak"] = Field(
        description="Quality assessment of the search results",
    )
    weak_axes: list[WeakAxis] = Field(
        default_factory=list,
        description="What needs re-searching (empty if sufficient)",
    )
    synthesis_md: str = Field(
        description="Arabic legal analysis markdown",
    )
    citations: list[Citation] = Field(
        default_factory=list,
        description="Structured citations for every source referenced",
    )


# -- Reranker models (v2: classification-only, no content in LLM output) ------


class RerankerDecision(BaseModel):
    """LLM's decision about one search result (classification only)."""

    position: int = Field(
        description="1-based position matching [N] in the result header",
    )
    action: Literal["keep", "drop", "unfold"] = Field(
        description="keep: relevant, drop: unrelated, unfold: needs deeper context",
    )
    unfold_mode: Literal[
        "article_precise", "section_detailed", "regulation_detailed"
    ] | None = Field(
        default=None,
        description="Unfold mode (only when action='unfold')",
    )
    relevance: Literal["high", "medium"] | None = Field(
        default=None,
        description="Relevance level (only when action='keep')",
    )
    reasoning: str = Field(
        default="",
        description="Short Arabic note explaining the decision",
    )


class RerankerClassification(BaseModel):
    """Output of each reranker LLM run (decisions only, no content)."""

    sufficient: bool = Field(
        description="True if kept results are >=80% sufficient to answer the sub-query",
    )
    decisions: list[RerankerDecision] = Field(
        default_factory=list,
        description="Per-result classification decisions",
    )
    summary_note: str = Field(
        default="",
        description="Brief Arabic note on collective sufficiency assessment",
    )


class RerankedResult(BaseModel):
    """A single result kept by the reranker (assembled by code, not LLM).

    Can be an article or a section (up to 2 sections allowed).
    """

    source_type: Literal["article", "section"] = Field(
        description="Type of result",
    )
    title: str = Field(
        description="Arabic title",
    )
    content: str = Field(
        default="",
        description="Article content or section summary",
    )
    article_num: str | None = Field(
        default=None,
        description="Article number (if article)",
    )
    article_context: str = Field(
        default="",
        description="Article context",
    )
    references_content: str = Field(
        default="",
        description="Cross-references text",
    )
    regulation_title: str = Field(
        default="",
        description="Parent regulation name",
    )
    section_title: str = Field(
        default="",
        description="Parent section title",
    )
    section_summary: str = Field(
        default="",
        description="Section summary (for sections or parent of articles)",
    )
    relevance: Literal["high", "medium"] = Field(
        description="Relevance level to the sub-query",
    )
    reasoning: str = Field(
        default="",
        description="Arabic explanation of why this result is relevant",
    )
    db_id: str = Field(
        default="",
        description="DB UUID carried through from search (used for URA ref_id)",
    )


class RegSearchResult(BaseModel):
    """Final result from run_reg_search(), returned to the caller.

    Field names align with deep_search_v3 ExecutorResult for compatibility.
    """

    quality: Literal["strong", "moderate", "weak", "pending"] = Field(
        description='Overall quality assessment. "pending" used by --expand-only (no aggregator ran).',
    )
    summary_md: str = Field(
        description="Arabic legal analysis markdown",
    )
    citations: list[Citation] = Field(
        default_factory=list,
        description="Structured citations",
    )
    domain: Literal["regulations"] = "regulations"
    queries_used: list[str] = Field(
        default_factory=list,
        description="All queries executed across all rounds",
    )
    rounds_used: int = Field(
        default=1,
        description="How many rounds (1-3)",
    )
    expander_prompt_key: str = Field(
        default="prompt_1",
        description="Which expander prompt was used (for logging)",
    )
    aggregator_prompt_key: str = Field(
        default="prompt_1",
        description="Which aggregator prompt was used (for logging)",
    )


# -- Dataclasses (programmatic, not LLM output) --------------------------------


@dataclass
class SearchResult:
    """Result from a single search pipeline execution."""

    query: str
    raw_markdown: str
    result_count: int


@dataclass
class RerankerQueryResult:
    """Container for one sub-query's reranker output (not an LLM model)."""

    query: str
    rationale: str
    sufficient: bool
    results: list  # list[RerankedResult] — articles + up to 2 sections
    dropped_count: int
    summary_note: str
    unfold_rounds: int = 0   # how many LLM classification runs (1-3)
    total_unfolds: int = 0   # how many DB unfold calls were made


@dataclass
class LoopState:
    """Mutable graph state for the reg_search loop."""

    focus_instruction: str
    user_context: str
    expander_prompt_key: str = "prompt_1"
    aggregator_prompt_key: str = "prompt_1"
    thinking_effort: str | None = None
    model_override: str | None = None
    unfold_mode: str = "precise"
    concurrency: int = 10
    round_count: int = 0
    max_rounds: int = 3
    expander_output: ExpanderOutput | None = None
    all_search_results: list[SearchResult] = field(default_factory=list)
    aggregator_output: AggregatorOutput | None = None
    weak_axes: list[WeakAxis] = field(default_factory=list)
    all_queries_used: list[str] = field(default_factory=list)
    sse_events: list[dict] = field(default_factory=list)
    inner_usage: list[dict] = field(default_factory=list)
    search_results_log: list[dict] = field(default_factory=list)
    reranker_results: list[RerankerQueryResult] = field(default_factory=list)
    skip_reranker: bool = False
    skip_aggregator: bool = False
    step_timings: dict = field(default_factory=dict)  # {expander: float, search: float, reranker: float, aggregator: float}


@dataclass
class RegSearchDeps:
    """Dependencies injected into the reg_search graph."""

    supabase: SupabaseClient
    embedding_fn: Callable[[str], Awaitable[list[float]]]
    jina_api_key: str = ""
    http_client: httpx.AsyncClient | None = None
    use_reranker: bool = False
    score_threshold: float = 0.005
    rrf_min_score: float = 0.1  # Drop RRF positions below this before reranker (saves tokens)
    mock_results: dict | None = None
    _query_id: int = 0   # Set by CLI before run_reg_search()
    _log_id: str = ""    # Set by run_reg_search() before graph starts
    _events: list[dict] = field(default_factory=list)
    _search_log: list[dict] = field(default_factory=list)
