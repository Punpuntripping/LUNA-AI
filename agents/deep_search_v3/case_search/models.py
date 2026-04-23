"""Models and dataclasses for the case_search domain loop.

Architecture (legacy — prompt_1, prompt_2):
    ExpanderNode → SearchNode → RerankerNode → End

Architecture (sectioned — prompt_3):
    SectionedExpanderNode → SectionedSearchNode → FusionNode → RerankerNode → End

No retry, no local aggregator. The shared deep_search_v3/aggregator/ handles synthesis.

Models:
- ExpanderOutput: legacy LLM query expansion result (flat list of strings)
- ExpanderOutputV2: sectioned — typed queries (channel-tagged) + legal_sectors
- TypedQuery: one channel-tagged Arabic query
- RerankerDecision / RerankerClassification: per-query LLM reranker output
- RerankedCaseResult: assembled kept case (code, not LLM)
- RerankerQueryResult: per-query reranker summary (dataclass)
- CaseSearchResult: final result returned to caller
- SearchResult: single search pipeline result (dataclass)
- ChannelRank / FusedRank: fusion intermediates (dataclass)
- LoopState: mutable graph state (dataclass)
- CaseSearchDeps: injected dependencies (dataclass)
"""
from __future__ import annotations

import json as _json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator
from supabase import Client as SupabaseClient

CaseChannel = Literal["principle", "facts", "basis"]
CHANNEL_NAMES: tuple[str, ...] = ("principle", "facts", "basis")


# -- Pydantic models (LLM output) ---------------------------------------------


class ExpanderOutput(BaseModel):
    """Output from the legacy QueryExpander agent (prompt_1 / prompt_2)."""

    queries: list[str] = Field(
        description="1-4 Arabic search queries targeting court rulings and judicial precedents",
    )
    rationales: list[str] = Field(
        default_factory=list,
        description="Internal rationale per query (logs only, not sent to LLM)",
    )


class TypedQuery(BaseModel):
    """One channel-tagged Arabic search query produced by the sectioned expander.

    The channel dictates which vector space the query is dispatched against in
    `search_case_section` — `principle` for doctrinal reasoning, `facts` for
    narrative, `basis` for statutory/procedural grounds.
    """

    text: str = Field(
        description="Arabic search query, 5-15 words, targeting one aspect of the issue",
    )
    channel: CaseChannel = Field(
        description="Which channel to retrieve against: principle | facts | basis",
    )
    rationale: str = Field(
        default="",
        description="Short Arabic note on the query's purpose (logs only)",
    )


class ExpanderOutputV2(BaseModel):
    """Sectioned output (prompt_3+). Structural: sectors + typed queries.

    The legacy `ExpanderOutput.queries: list[str]` is replaced by
    `queries: list[TypedQuery]` so each query carries its channel routing tag.
    `legal_sectors` narrows retrieval to cases whose `legal_domains` overlap
    any of the LLM's picks — set to `None` (or empty) when the classifier is
    uncertain.
    """

    legal_sectors: list[str] | None = Field(
        default=None,
        description="1-4 legal-domain names for pre-filter, or null if ambiguous",
    )
    queries: list[TypedQuery] = Field(
        description="Channel-tagged Arabic queries (usually 3-5, one per channel per angle)",
    )

    @field_validator("legal_sectors", mode="before")
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


class RerankerDecision(BaseModel):
    """LLM decision about one case result within a single sub-query."""

    position: int = Field(
        description="1-based position matching [N] in the result header",
    )
    action: Literal["keep", "drop"] = Field(
        description="keep: relevant case ruling, drop: unrelated case",
    )
    relevance: Literal["high", "medium"] | None = Field(
        default=None,
        description="Relevance level — only set when action='keep'",
    )
    reasoning: str = Field(
        default="",
        description="Short Arabic note explaining the decision",
    )


class RerankerClassification(BaseModel):
    """Output of one per-query reranker LLM call."""

    sufficient: bool = Field(
        description="True if kept results are >=80% sufficient to answer this sub-query",
    )
    decisions: list[RerankerDecision] = Field(
        default_factory=list,
        description="Per-result keep/drop decisions",
    )
    summary_note: str = Field(
        default="",
        description="Brief Arabic note on collective sufficiency assessment",
    )


class RerankedCaseResult(BaseModel):
    """A single case result kept by the reranker (assembled by code, not LLM)."""

    source_type: str = Field(default="case")
    title: str = Field(default="", description="court + case_number + date_hijri")
    content: str = Field(default="", description="Ruling text (truncated)")
    court: Optional[str] = Field(default=None, description="Court name")
    city: Optional[str] = Field(default=None, description="City")
    court_level: Optional[str] = Field(default=None, description="first_instance or appeal")
    case_number: Optional[str] = Field(default=None, description="Case number")
    judgment_number: Optional[str] = Field(default=None, description="Judgment number")
    date_hijri: Optional[str] = Field(default=None, description="Hijri date")
    legal_domains: list[str] = Field(default_factory=list)
    referenced_regulations: list[dict] = Field(default_factory=list)
    appeal_result: Optional[str] = Field(default=None)
    score: float = Field(default=0.0, description="RRF hybrid score")
    relevance: Literal["high", "medium"] = Field(default="medium")
    reasoning: str = Field(default="", description="Arabic explanation of relevance")
    db_id: str = Field(default="", description="case_ref — used as URA ref_id seed")


class CaseSearchResult(BaseModel):
    """Final result returned by run_case_search().

    reranker_results feeds the shared deep_search_v3/aggregator/ for synthesis.
    """

    reranker_results: list = Field(
        default_factory=list,
        description="list[RerankerQueryResult] — one per sub-query",
    )
    queries_used: list[str] = Field(
        default_factory=list,
        description="All Arabic search queries executed",
    )
    rounds_used: int = Field(default=1, description="Always 1 — no retry loop")
    domain: Literal["cases"] = "cases"
    expander_prompt_key: str = Field(default="prompt_2")


# -- Dataclasses (programmatic, not LLM output) -------------------------------


@dataclass
class SearchResult:
    """Result from a single search pipeline execution."""

    query: str
    raw_markdown: str
    result_count: int
    channel: str | None = None  # "principle" | "facts" | "basis" | None (legacy)


@dataclass
class ChannelCandidate:
    """One case-section hit returned by search_case_sections RPC.

    Carries the raw row payload so downstream stages (fusion, formatting)
    can assemble buckets without re-querying the DB.
    """

    case_id: str
    channel: str
    rank: int           # 1-based rank within the channel's result list
    score: float        # cosine similarity (1 - distance)
    row: dict           # full RPC row — case_ref, case metadata, section_text


@dataclass
class FusedCandidate:
    """One case after reciprocal-rank fusion across channels.

    `channel_ranks` maps channel → rank for diagnostics ("this case placed
    #3 in principle and #7 in facts"). Downstream formatters can use it to
    show why a case was surfaced.
    """

    case_id: str
    fused_score: float
    channel_ranks: dict[str, int]       # channel → rank (missing => not in channel)
    channel_scores: dict[str, float]    # channel → similarity score (missing => not in channel)
    row: dict                           # merged case metadata (first-seen wins for duplicates)


@dataclass
class RerankerQueryResult:
    """Per-query reranker summary — one per sub-query, fed to shared aggregator."""

    query: str
    rationale: str
    sufficient: bool
    results: list  # list[RerankedCaseResult]
    dropped_count: int
    summary_note: str
    unfold_rounds: int = 0   # always 0 for cases (flat documents, no unfold)
    total_unfolds: int = 0   # always 0 for cases


@dataclass
class LoopState:
    """Mutable graph state for the single-round case_search loop.

    Legacy path (prompt_1 / prompt_2): `expander_output: ExpanderOutput`
    Sectioned path (prompt_3+):        `expander_output_v2: ExpanderOutputV2`
    Exactly one of the two is populated per run, selected by `expander_prompt_key`.
    """

    focus_instruction: str
    user_context: str
    expander_prompt_key: str = "prompt_2"
    thinking_effort: str | None = None
    model_override: str | None = None
    concurrency: int = 10
    round_count: int = 0
    max_rounds: int = 1  # single round — no retry
    # Legacy expander shape (prompt_1 / prompt_2)
    expander_output: ExpanderOutput | None = None
    # Sectioned expander shape (prompt_3+)
    expander_output_v2: "ExpanderOutputV2 | None" = None
    # Channel candidates produced by the sectioned search node, grouped by channel
    channel_candidates: dict[str, list[ChannelCandidate]] = field(default_factory=dict)
    # Per-query enriched candidates — aligned 1:1 with expander's typed queries.
    # Each reranker call in the sectioned path consumes its own entry here
    # (mirroring reg_search's per-query reranker pattern) so no cross-query blending
    # happens at the LLM layer.
    per_query_candidates: list[tuple["TypedQuery", list[ChannelCandidate]]] = field(default_factory=list)
    # 4-bucket output of the fusion node (top-principle, top-facts, top-basis, top-fused)
    # — analytics only in the per-query rerank path; no longer feeds the reranker.
    fused_buckets: dict[str, list[FusedCandidate]] = field(default_factory=dict)
    all_search_results: list[SearchResult] = field(default_factory=list)
    reranker_results: list[RerankerQueryResult] = field(default_factory=list)
    all_queries_used: list[str] = field(default_factory=list)
    sse_events: list[dict] = field(default_factory=list)
    inner_usage: list[dict] = field(default_factory=list)
    search_results_log: list[dict] = field(default_factory=list)


@dataclass
class CaseSearchDeps:
    """Dependencies injected into the case_search graph.

    Sectioned-pipeline overrides (populated by CLI flags, ignored by legacy):
    - cli_channels: if set, typed queries whose channel is not in this list
      are dropped before dispatch. None = no filter (use all queries).
    - cli_sectors: if set, overrides the expander's legal_sectors pick.
      [] = explicit "no filter"; None = use expander's choice.
    """

    supabase: SupabaseClient
    embedding_fn: Callable[[str], Awaitable[list[float]]]
    score_threshold: float = 0.005
    mock_results: dict | None = None
    cli_channels: list[str] | None = None
    cli_sectors: list[str] | None = None
    _query_id: int = 0
    _log_id: str = ""
    _events: list[dict] = field(default_factory=list)
    _search_log: list[dict] = field(default_factory=list)
