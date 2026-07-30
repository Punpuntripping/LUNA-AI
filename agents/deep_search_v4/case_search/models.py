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
- CaseKeep / CaseRerankerClassification: per-query LLM reranker output (keep-only)
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

from agents.deep_search_v4.shared import DEFAULT_SEARCH_CONCURRENCY
from agents.deep_search_v4.shared.context import ContextBlock

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


# -- Case-local reranker models (bespoke — no shared-schema field leakage) -----
# Cases are flat documents: binary keep/drop (no `unfold`/`unfold_mode`), and no
# `weak_axes` (compliance-only). `reasoning`/`summary_note` are REQUIRED so the
# model can't silently drop them; `query_axes`/`satisfies_axes` carry the axis
# decomposition (#3). The salvager on the reranker agent rescues a text-finalised
# but schema-complete JSON without a retry.


class CaseKeep(BaseModel):
    """One KEPT case result (position-indexed). Keep-only contract: every
    emitted entry is a keep — un-listed positions are dropped by difference in
    the reranker. ``relevance`` is REQUIRED (no default) so the model cannot
    silently coerce a keep to ``medium``.
    """

    position: int = Field(
        description="1-based position matching [N] in the result header",
    )
    relevance: Literal["high", "medium"] = Field(
        description=(
            "Relevance tier — REQUIRED on every kept entry. "
            "'high' = primary-axis + direct operative reasoning; "
            "'medium' = secondary axis / partial / applicable principle."
        ),
    )
    reasoning: str = Field(
        description=(
            "Short Arabic note justifying the keep; on a partial keep, "
            "name the uncovered axis. Never assert an axis the result lacks."
        ),
    )
    satisfies_axes: list[int] = Field(
        default_factory=list,
        description="Indices into query_axes that this result covers",
    )


class CaseRerankerClassification(BaseModel):
    """Output of one case_search reranker LLM call — keeps + axis coverage.

    Keep-only contract: the model emits ONLY the rulings it keeps; the reranker
    derives the drop set by set-difference (candidate positions − kept).
    """

    sufficient: bool = Field(
        description=(
            "The 80% rule: True if the kept rulings suffice >=80% to answer the "
            "sub-query, False if coverage is incomplete. An uncovered MAIN axis "
            "from query_axes tilts toward False but is a guide, not an "
            "all-or-nothing test."
        ),
        # This description is part of the JSON schema the model actually sees, so
        # it must not contradict the system prompt. It previously read "True ONLY
        # if the kept set covers EVERY axis" — the pre-2026-07-25 all-or-nothing
        # rule. `prompts.py` prompt_2 moved to the reg reranker's 80% rule, and
        # leaving this text behind gave the model two conflicting definitions of
        # the same field in one request. Keep the two in lockstep.
    )
    query_axes: list[str] = Field(
        default_factory=list,
        description=(
            "2-4 discriminating legal axes restated from the sub-query before "
            "classifying (e.g. dispute type, procedural issue, statutory basis)"
        ),
    )
    keeps: list[CaseKeep] = Field(
        default_factory=list,
        description="One entry per KEPT ruling — un-listed positions are dropped",
    )
    summary_note: str = Field(
        description="Arabic note naming covered axes and any uncovered axis",
    )

    @field_validator("keeps", mode="before")
    @classmethod
    def _coerce_keeps(cls, v):
        # LLM output quirks (mirrors ExpanderOutputV2._coerce_queries):
        #   ''         -> []   (uncertain → empty keep list, not crash)
        #   '[...]'    -> list (JSON-stringified array, no retry)
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


class RerankedCaseResult(BaseModel):
    """A single case result kept by the reranker (assembled by code, not LLM)."""

    source_type: str = Field(default="case")
    title: str = Field(default="", description="court + case_number + date_hijri")
    content: str = Field(default="", description="Ruling text (truncated)")
    court: Optional[str] = Field(default=None, description="Court name")
    city: Optional[str] = Field(default=None, description="City")
    court_level: Optional[str] = Field(
        default=None,
        description=(
            "first_instance, appeal, or supreme — all THREE are real values "
            "(supreme = 125 rulings). See shared/court_levels.py; never "
            "collapse this to a two-branch conditional."
        ),
    )
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
    db_uuid: str = Field(
        default="",
        description="cases.id UUID — forensic ref_id seed (db_id stays case_ref for citations)",
    )


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
    """One CASE surfaced by the `search_case_topics` RPC, with its matched topics.

    The RPC returns flat topic rows joined to the case header;
    `search.group_topic_rows` collapses them to one candidate per case and
    keeps every matched topic (decision D1 — plan §2). There is no enrichment
    round trip: `row` is the joined header, so downstream stages (reranker
    formatting, fusion, forensics) never re-query the DB.
    """

    case_id: str
    channel: str
    rank: int           # 1-based rank within this query's / channel's list
    score: float        # best matched-topic score for this case (1 - distance)
    row: dict           # RPC-joined case header — case_ref, court, city,
                        # court_level, case_number, date_hijri, short_summary
                        # (+ `score` mirrored for forensic dumps)
    topics: list[dict] = field(default_factory=list)
    # Every topic of THIS case appearing in THIS sub-query's result window,
    # score-desc (so topics[0] is the best match). Each entry:
    #   {topic_ref, topic_index, text, attrs, score, kind}
    # `attrs` is kind-shaped: basis → {الطرف, موقف المحكمة};
    # principle → {النوع}; fact → {}. Keys may be missing on any given row.
    # `kind` is the DB `case_topic_kind` verbatim (`fact`, NOT the `facts`
    # channel spelling) so the reranker formatter needs no second translation.


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
    # ``caps_applied`` carries {"max_keep", "truncated_by_cap"} when the flat
    # keep cap was applied. Empty dict when the cap was not active.
    dropped_results: list = field(default_factory=list)
    # Forensic descriptors for LLM-dropped + cap-truncated cases. Each dict:
    # {db_uuid, title, reasoning, drop_reason, source_type}. Reconstructed in
    # the loop (the markdown-based reranker is blind to cases.id). Empty on
    # the legacy non-sectioned path / reconstruction failure.


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
    # Sector list applied at search time. None → no filter.
    #
    # The case path no longer consumes ``sector_picker`` at all (decision D3 /
    # plan §§1.2, 9): sector filtering dropped the entire untagged batch —
    # exactly the 9,861 cases the `case_topics` retarget exists to recover —
    # while buying almost no selectivity (91% of tagged cases are
    # المعاملات التجارية). There is no ``sectors_future`` here anymore; this
    # field survives ONLY as the CLI ``--sectors`` experiment hatch and is
    # None on every production run.
    sectors_override: list[str] | None = None
    model_override: str | None = None
    concurrency: int = DEFAULT_SEARCH_CONCURRENCY
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
    # Structured context bundle from the planner (§4 / §5.1.A). Threaded into
    # the expander user message as <context_blocks> XML (empty list → no XML).
    #
    # SUPERSEDED 2026-07-24 (plan §7 / decision D6): the reranker no longer
    # receives zero blocks. ``SectionedRerankerNode`` picks the ``planner_brief``
    # body out of this list and passes it to ``run_reranker_for_query``, which
    # renders it as a <planner_brief> block at the head of the user message.
    # Still filtered to that ONE label — ``case_brief`` and
    # ``prior_search_lessons`` never reach a reranker.
    context_blocks: list[ContextBlock] = field(default_factory=list)


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
    # Max results per sub-query — a single flat cap on the TOTAL kept
    # (high + medium together), never per tier. Clamped by
    # ``loop.MAX_KEEP_PER_SUBQUERY`` (7); see that constant for why.
    reranker_max_keep: int = 7
    # Dynamic result-budget model (MODE_PROFILES.md §1). When set by the
    # planner/orchestrator, the per-sub-query reranker keep is derived at
    # runtime as ceil(result_budget / max(N, 3)) from the expander's actual
    # query count N — and ``reranker_max_keep`` above is ignored. When None
    # (CLI / monitor path), the fixed ``reranker_max_keep`` is used.
    result_budget: int | None = None
    _query_id: int = 0
    _log_id: str = ""
    _events: list[dict] = field(default_factory=list)
    _search_log: list[dict] = field(default_factory=list)
    # Live SSE sink — copied from ``FullLoopDeps.emit_sse`` by ``_run_case_phase``.
    # When set, each per-sub-query topic line ("جاري البحث في السوابق القضائية: …"
    # and the sectioned "بحث [channel]: …") is fired LIVE at emit time and its
    # batched copy tagged ``streamed`` so the orchestrator's terminal flush skips
    # it (no double-send). ``None`` (CLI / monitor / smoke paths) → topic lines
    # stay batch-only.
    emit_sse: Callable[[dict], None] | None = None
