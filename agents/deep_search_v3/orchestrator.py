"""End-to-end URA pipeline runner.

Pipeline:
    reg_search (graph)
      └─ reranker_results  ──merge_partial_ura──▶ PartialURA
                                                      │
                                                      ▼
                         ComplianceURASlice  ◀──run_compliance_from_partial_ura──▶
                                                      │
                                                      ▼
                                               merge_to_ura ──▶ UnifiedRetrievalArtifact
                                                      │
                                                      ▼
                                     load_aggregator_input_from_ura ──▶ AggregatorInput
                                                      │
                                                      ▼
                                              handle_aggregator_turn
                                                      │
                                                      ▼
                                               AggregatorOutput

case_search is out of scope for the URA pipeline (cases=None everywhere).

We invoke the reg_search graph directly (not run_reg_search) so we can read
LoopState.reranker_results straight out of the graph run -- that list carries
RerankedResult.db_id, which we need for URA ref_id generation. Reparsing from
the markdown logs would lose db_id.
"""
from __future__ import annotations

import asyncio
import logging
import time as _time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from supabase import Client as SupabaseClient

from agents.deep_search_v3.ura.schema import PartialURA, UnifiedRetrievalArtifact
from agents.deep_search_v3.aggregator.deps import build_aggregator_deps
from agents.deep_search_v3.aggregator.log_parser import load_aggregator_input_from_ura
from agents.deep_search_v3.aggregator.models import AggregatorOutput
from agents.deep_search_v3.aggregator.runner import handle_aggregator_turn
from agents.deep_search_v3.compliance_search.loop import run_compliance_search
from agents.deep_search_v3.compliance_search.models import (
    ComplianceSearchDeps,
    ComplianceURASlice,
)
from agents.deep_search_v3.compliance_search.slice_builder import build_compliance_slice
from agents.deep_search_v3.reg_search.loop import ExpanderNode, reg_search_graph
from agents.deep_search_v3.reg_search.logger import (
    create_run_dir,
    make_log_id,
    save_run_json,
    save_run_overview_md,
)
from agents.deep_search_v3.reg_search.models import (
    LoopState,
    RegSearchDeps,
    RegSearchResult,
    RerankerQueryResult,
)
from agents.deep_search_v3.ura.merger import merge_partial_ura, merge_to_ura

logger = logging.getLogger(__name__)


@dataclass
class FullLoopDeps:
    """Dependencies for run_full_loop().

    Supabase + embedding_fn are shared across reg_search and compliance_search.
    The aggregator gets its own deps constructed internally via build_aggregator_deps.
    """

    supabase: SupabaseClient
    embedding_fn: Callable[[str], Awaitable[list[float]]]
    model_override: str | None = None
    jina_api_key: str = ""
    http_client: httpx.AsyncClient | None = None
    use_reranker: bool = False
    expander_prompt_key: str = "prompt_1"
    reg_aggregator_prompt_key: str = "prompt_1"
    concurrency: int = 10
    unfold_mode: str = "precise"
    include_compliance: bool = True
    _events: list[dict] = field(default_factory=list)



async def _run_reg_search_phase(
    query: str,
    query_id: int,
    deps: FullLoopDeps,
) -> tuple[list[RerankerQueryResult], str, RegSearchResult | None]:
    """Run reg_search up to (and including) the reranker; skip its aggregator.

    Returns (reranker_results, log_id, optional_reg_result). The
    RegSearchResult is a best-effort wrapper -- it has quality="pending"
    since the URA pipeline runs its own aggregator downstream.
    """
    log_id = make_log_id(query_id) if query_id else datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    create_run_dir(log_id)

    reg_deps = RegSearchDeps(
        supabase=deps.supabase,
        embedding_fn=deps.embedding_fn,
        jina_api_key=deps.jina_api_key,
        http_client=deps.http_client,
        use_reranker=deps.use_reranker,
        _query_id=query_id,
    )
    reg_deps._log_id = log_id

    state = LoopState(
        focus_instruction=query,
        user_context="",
        expander_prompt_key=deps.expander_prompt_key,
        aggregator_prompt_key=deps.reg_aggregator_prompt_key,
        model_override=deps.model_override,
        unfold_mode=deps.unfold_mode,
        concurrency=deps.concurrency,
        skip_aggregator=True,
    )

    t0 = _time.perf_counter()
    error_msg: str | None = None
    try:
        await reg_search_graph.run(ExpanderNode(), state=state, deps=reg_deps)
    except Exception as exc:
        logger.error("reg_search phase failed: %s", exc, exc_info=True)
        error_msg = str(exc)

    duration = _time.perf_counter() - t0
    deps._events.extend(reg_deps._events + state.sse_events)

    reranker_results = list(state.reranker_results)
    logger.info(
        "_run_reg_search_phase: log_id=%s, %d reranker sub-queries, duration=%.2fs",
        log_id, len(reranker_results), duration,
    )

    # Write a best-effort run.md / run.json so the log directory stays consistent
    placeholder = RegSearchResult(
        quality="pending",
        summary_md="URA pipeline: reg_search phase completed; aggregator runs downstream.",
        citations=[],
        domain="regulations",
        queries_used=list(state.all_queries_used),
        rounds_used=state.round_count,
        expander_prompt_key=deps.expander_prompt_key,
        aggregator_prompt_key=deps.reg_aggregator_prompt_key,
    )
    try:
        save_run_overview_md(
            log_id=log_id,
            focus_instruction=query,
            user_context="",
            expander_prompt_key=deps.expander_prompt_key,
            aggregator_prompt_key=deps.reg_aggregator_prompt_key,
            duration_s=duration,
            result=placeholder,
            round_summaries=[],
        )
        save_run_json(
            log_id=log_id,
            focus_instruction=query,
            user_context="",
            expander_prompt_key=deps.expander_prompt_key,
            aggregator_prompt_key=deps.reg_aggregator_prompt_key,
            duration_s=duration,
            result=placeholder,
            events=list(reg_deps._events),
            round_summaries=[],
            search_results_log=list(state.search_results_log),
            inner_usage=list(state.inner_usage),
            error=error_msg,
            query_id=query_id,
            models={},
            thinking_effort=None,
            step_timings=dict(state.step_timings),
        )
    except Exception as exc:
        logger.debug("reg_search phase: log write failed: %s", exc)

    sectors: list[str] = []
    if state.expander_output and state.expander_output.sectors:
        sectors = list(state.expander_output.sectors)

    return reranker_results, log_id, placeholder, sectors


async def _run_compliance_phase(
    query: str,
    deps: FullLoopDeps,
) -> ComplianceURASlice | None:
    """Run compliance_search standalone and shape its output as a URA slice."""
    if not deps.include_compliance:
        return None

    compliance_deps = ComplianceSearchDeps(
        supabase=deps.supabase,
        embedding_fn=deps.embedding_fn,
        jina_api_key=deps.jina_api_key,
        http_client=deps.http_client,
        use_reranker=deps.use_reranker,
    )
    result = await run_compliance_search(
        focus_instruction=query,
        user_context="",
        deps=compliance_deps,
    )
    deps._events.extend(compliance_deps._events)
    return build_compliance_slice(result)


async def run_full_loop(
    query: str,
    query_id: int,
    deps: FullLoopDeps,
    prompt_key: str = "prompt_1",
) -> AggregatorOutput:
    """Run the end-to-end URA pipeline and return the final aggregator output.

    reg_search and compliance_search run in parallel as peer domain executors.
    Their outputs merge into a single URA consumed by the unified aggregator.
    """
    logger.info("run_full_loop[%s]: launching reg_search + compliance in parallel", query_id)

    reg_task = _run_reg_search_phase(query, query_id, deps)
    compliance_task = _run_compliance_phase(query, deps)

    (reranker_results, log_id, _, sectors), compliance_slice = await asyncio.gather(
        reg_task, compliance_task,
    )

    partial = merge_partial_ura(
        reg_reranker_results=reranker_results,
        original_query=query,
        query_id=query_id,
        log_id=log_id,
        sector_filter=sectors,
    )
    logger.info(
        "run_full_loop[%s]: partial URA -- %d reg results, compliance slice=%s",
        query_id,
        len(partial.results),
        None if compliance_slice is None else f"{len(compliance_slice.results)} results",
    )

    ura: UnifiedRetrievalArtifact = merge_to_ura(partial, compliance_slice)
    logger.info(
        "run_full_loop[%s]: full URA assembled -- %d results "
        "(%d reg, %d compliance)",
        query_id,
        len(ura.results),
        sum(1 for r in ura.results if r.domain == "regulations"),
        sum(1 for r in ura.results if r.domain == "compliance"),
    )

    # -- 5. URA -> AggregatorInput ------------------------------------------
    agg_input = load_aggregator_input_from_ura(ura, prompt_key=prompt_key)

    # -- 6. run aggregator ---------------------------------------------------
    agg_deps = build_aggregator_deps()
    agg_output = await handle_aggregator_turn(agg_input, agg_deps)
    deps._events.extend(agg_deps._events)

    logger.info(
        "run_full_loop[%s]: done -- %d references, confidence=%s",
        query_id, len(agg_output.references), agg_output.confidence,
    )

    return agg_output


__all__ = [
    "FullLoopDeps",
    "run_full_loop",
]
