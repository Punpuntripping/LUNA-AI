"""Models and dataclasses for the case_search domain loop.

Architecture (legacy — prompt_1, prompt_2):
    ExpanderNode → SearchNode → RerankerNode → End

Architecture (sectioned — prompt_3):
    SectionedExpanderNode → SectionedSearchNode → FusionNode → RerankerNode → End

No retry, no local aggregator. The shared deep_search_v3/aggregator/ handles synthesis.

Models:
- ExpanderOutput: legacy LLM query expansion result (flat list of strings)
- ExpanderOutputV2: sectioned — typed queries (channel-tagged)
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
    """Sectioned output (prompt_3+). Structural: typed queries only.

    The legacy `ExpanderOutput.queries: list[str]` is replaced by
    `queries: list[TypedQuery]` so each query carries its channel routing tag.

    Sectors are decided by the planner upstream and applied at search time
    via ``LoopState.sectors_override`` — the LLM no longer picks them.
    """

    queries: list[TypedQuery] = Field(
        default_factory=list,
        description="Channel-tagged Arabic queries (usually 3-5, one per channel per angle)",
    )

    @field_validator("queries", mode="before")
    @classmethod
    def _coerce_queries(cls, v):
        # LLM output quirks:
        #   ''         -> []   (uncertain → empty list, not crash)
        #   '[...]'    -> list (JSON-stringified array, same as planner sectors)
        if isinstance(v, str):
            if v.strip() == "":
                return []
            try:
                parsed = _json.loads(v)
                if isinstance(parsed, list):
                    return parsed
            except (_json.JSONDecodeError, TypeError):
                pass
        return v


# Re-export from shared so all three domains share one definition.
# Case reranker treats action="unfold" as "drop" (no DB hierarchy for cases).
from agents.deep_search_v4.shared.reranker_models import (  # noqa: E402
    RerankerClassification,
    RerankerDecision,
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
    expander_prompt_key: str = Field(default="prompt_3")
    inner_usage: list[dict] = Field(
        default_factory=list,
        description=(
            "Per-LLM-call usage entries (expander + per-query rerankers) — "
            "mirrors LoopState.inner_usage so the orchestrator can total tokens "
            "without a state reference."
        ),
    )


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
    caps_applied: dict = field(default_factory=dict)
    # ``caps_applied`` carries {"max_high", "max_medium", "truncated_by_cap"}
    # when keep caps were applied. Empty dict when caps were not active.


@dataclass
class LoopState:
    """Mutable graph state for the single-round case_search loop.

    Legacy path (prompt_1 / prompt_2): `expander_output: ExpanderOutput`
    Sectioned path (prompt_3+):        `expander_output_v2: ExpanderOutputV2`
    Exactly one of the two is populated per run, selected by `expander_prompt_key`.
    """

    focus_instruction: str
    user_context: str
    expander_prompt_key: str = "prompt_3"
    # Planner-supplied sector list applied at search time. None → no filter.
    sectors_override: list[str] | None = None
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

    Sectioned-pipeline channel filter:
    - cli_channels: if set, typed queries whose channel is not in this list
      are dropped before dispatch. None = no filter (use all queries).

    Sector filtering is driven by ``LoopState.sectors_override`` (populated
    upstream by the planner or the CLI ``--sectors`` flag). There is no
    expander-side sector pick anymore.
    """

    supabase: SupabaseClient
    embedding_fn: Callable[[str], Awaitable[list[float]]]
    score_threshold: float = 0.005
    mock_results: dict | None = None
    cli_channels: list[str] | None = None
    reranker_max_high: int = 6    # Max high-relevance results per sub-query
    reranker_max_medium: int = 4  # Max medium-relevance results per sub-query
    _query_id: int = 0
    _log_id: str = ""
    _events: list[dict] = field(default_factory=list)
    _search_log: list[dict] = field(default_factory=list)
