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

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, Optional

from pydantic import BaseModel, Field
from supabase import Client as SupabaseClient

from agents.deep_search_v4.shared import DEFAULT_SEARCH_CONCURRENCY
from agents.deep_search_v4.shared.context import ContextBlock


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
    """Output from the QueryExpander agent.

    Sectors are NOT picked by the expander — the planner is the sole source
    of sector decisions, applied directly at search time via
    ``LoopState.sectors_override``.
    """

    queries: list[str] = Field(
        description="2-10 Arabic search queries targeting single legal concepts",
    )
    rationales: list[str] = Field(
        default_factory=list,
        description="Internal rationale per query (logs only, not sent to LLM)",
    )


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
# Re-export from shared so all three domains share one definition.
from agents.deep_search_v4.shared.reranker_models import (  # noqa: E402
    RerankerClassification,
    RerankerDecision,
)


# -- Reg-local reranker models (v2: chunk corpus, label-addressed) ------------
# The v2 reg_search reranker diverges from the shared RerankerDecision — chunks
# are addressed by a stable label (not a per-round position), and `unfold`
# targets a prev/next neighbour. The shared `reranker_models.py` stays untouched
# because case_search / compliance_search still depend on it.


class RegRerankerDecision(BaseModel):
    """One reranker decision for a v2 chunk, addressed by its stable label."""

    label: str = Field(
        description="Chunk label exactly as shown in the '### [Cn]' header, e.g. 'C7'",
    )
    action: Literal["keep", "drop", "unfold"] = Field(
        description="keep: relevant; drop: out of scope; unfold: pull a neighbour chunk",
    )
    direction: Optional[Literal["prev", "next"]] = Field(
        default=None,
        description="Which neighbour to pull — set only when action='unfold'",
    )
    relevance: Optional[Literal["high", "medium"]] = Field(
        default=None,
        description="Relevance tier — set only when action='keep'",
    )
    reasoning: str = Field(
        default="",
        description="Short Arabic note; states the regulation-scope-applicability verdict",
    )


class RegRerankerClassification(BaseModel):
    """Output of one v2 reg_search reranker LLM call — decisions only."""

    sufficient: bool = Field(
        description="True if the kept chunks are >=80% sufficient to answer the sub-query",
    )
    decisions: list[RegRerankerDecision] = Field(
        default_factory=list,
        description="Per-chunk classification decisions",
    )
    summary_note: str = Field(
        default="",
        description="Brief Arabic note on collective sufficiency",
    )


class RerankedResult(BaseModel):
    """A single result kept by the reranker (assembled by code, not LLM).

    v2: every result is a chunk (``source_type == "chunk"``). The legacy
    article/section fields below are kept — and left at their defaults — so the
    ``adapter.py`` -> shared ``RegURAResult`` boundary keeps compiling unchanged.
    """

    source_type: Literal["chunk", "article", "section"] = Field(
        description='Type of result — "chunk" for the v2 corpus',
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
    rrf: float = Field(
        default=0.0,
        description="RRF score carried through from search (used for URA rrf_max). Inherited (attenuated) by unfolded siblings/children.",
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
    chunks: list  # list[dict] — tagged chunks_v2 rows from search.py
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
    caps_applied: dict = field(default_factory=dict)
    # ``caps_applied`` carries {"max_keep", "truncated_by_cap"} when the keep
    # cap was applied. Empty dict when the cap was not active.


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
    concurrency: int = DEFAULT_SEARCH_CONCURRENCY
    reranker_max_keep: int = 8  # Max results kept per sub-query (single flat cap)
    # Dynamic result-budget model (MODE_PROFILES.md §1). When set by the
    # planner/orchestrator, the per-sub-query reranker keep is derived at
    # runtime as ceil(result_budget / max(N, 3)) from the expander's actual
    # query count N — and ``reranker_max_keep`` above is ignored. When None
    # (CLI / monitor path), the fixed ``reranker_max_keep`` is used.
    result_budget: int | None = None
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
    # Planner-pre-selected sectors. When set, the expander is told not to
    # reclassify and the sector filter is locked to this list (the LLM's
    # ``sectors`` output is ignored).
    sectors_override: list[str] | None = None
    # Hard cap on number of sub-queries from the expander, plumbed from
    # the planner's focus profile via the orchestrator.
    expander_max_queries: int | None = None
    # Structured context bundle from the planner (§4 / §5.1.A). Threaded into
    # the expander user message; the reranker is hardcoded to receive zero
    # blocks. Empty list → no <context_blocks> XML in the prompt.
    context_blocks: list[ContextBlock] = field(default_factory=list)
    step_timings: dict = field(default_factory=dict)  # {expander: float, search: float, reranker: float, aggregator: float}


@dataclass
class RegSearchDeps:
    """Dependencies injected into the reg_search graph."""

    supabase: SupabaseClient
    embedding_fn: Callable[[str], Awaitable[list[float]]]
    _query_id: int = 0   # Set by CLI before run_reg_search()
    _log_id: str = ""    # Set by run_reg_search() before graph starts
    _events: list[dict] = field(default_factory=list)
    _search_log: list[dict] = field(default_factory=list)
