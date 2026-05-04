"""End-to-end URA pipeline runner (Loop V2).

Pipeline:
    reg_search phase        ┐
    compliance_search phase ├── asyncio.gather → list[shared.RerankerQueryResult] x 3
    case_search phase       ┘                                 │
                                                              ▼
                                                build_ura_from_phases()
                                                              │
                                                              ▼
                                                  UnifiedRetrievalArtifact (2.0)
                                                              │
                                                              ▼
                                                AggregatorInput.from_ura(...)
                                                              │
                                                              ▼
                                                handle_aggregator_turn(...)
                                                              │
                                                              ▼
                                                       AggregatorOutput

Wave D replaced the old two-stage ``PartialURA -> URA`` plumbing with the
three peer executors running in parallel and a single-pass merger. Phase
failures are isolated: an individual executor throwing does not kill the
loop -- its phase is logged and returns an empty ``list[RerankerQueryResult]``.
"""
from __future__ import annotations

import asyncio
import logging
import time as _time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

import httpx
from supabase import Client as SupabaseClient

from agents.deep_search_v4.aggregator.deps import build_aggregator_deps
from agents.deep_search_v4.aggregator.models import AggregatorInput, AggregatorOutput
from agents.deep_search_v4.aggregator.runner import handle_aggregator_turn
from agents.deep_search_v4.case_search.adapter import case_to_rqr
from agents.deep_search_v4.case_search.loop import run_case_search
from agents.deep_search_v4.case_search.models import CaseSearchDeps
from agents.deep_search_v4.compliance_search.adapter import compliance_to_rqr
from agents.deep_search_v4.compliance_search.logger import (
    create_run_dir as create_compliance_run_dir,
    make_ura_log_id as make_compliance_log_id,
    save_run_json as save_compliance_run_json,
    save_run_md as save_compliance_run_md,
)
from agents.deep_search_v4.compliance_search.loop import (
    ExpanderNode as ComplianceExpanderNode,
    compliance_search_graph,
)
from agents.deep_search_v4.compliance_search.models import (
    ComplianceSearchDeps,
    ComplianceSearchResult,
    LoopState as ComplianceLoopState,
)
from agents.deep_search_v4.reg_search.adapter import reg_to_rqr
from agents.deep_search_v4.reg_search.logger import (
    create_run_dir,
    make_log_id,
    save_run_json,
    save_run_overview_md,
)
from agents.deep_search_v4.reg_search.loop import ExpanderNode, reg_search_graph
from agents.deep_search_v4.reg_search.models import (
    LoopState as RegLoopState,
    RegSearchDeps,
    RegSearchResult,
)
from agents.deep_search_v4.shared.models import RerankerQueryResult
from agents.deep_search_v4.ura.merger import build_ura_from_phases
from agents.deep_search_v4.ura.schema import UnifiedRetrievalArtifact
from agents.deep_search_v4.planner import (
    PlannerDeps,
    PlannerOutput,
    apply_plan_to_deps,
    derive_aggregator_prompt_key,
    run_planner,
)
from shared.observability import get_logfire

logger = logging.getLogger(__name__)
_logfire = get_logfire()


DetailLevel = Literal["low", "medium", "high"]


@dataclass
class FullLoopDeps:
    """Dependencies for :func:`run_full_loop`.

    Supabase + embedding_fn are shared across reg / compliance / case phases.
    The aggregator gets its own deps constructed internally via
    :func:`build_aggregator_deps`, threaded with ``detail_level``.

    Callers that want to persist the retrieval artifact / reranker_runs can
    read back ``_ura``, ``_reg_rqrs``, ``_comp_rqrs``, ``_case_rqrs`` after
    ``run_full_loop`` returns. ``_per_executor_stats`` carries per-phase
    timing + token totals (populated by the phase wrappers).
    """

    supabase: SupabaseClient
    embedding_fn: Callable[[str], Awaitable[list[float]]]
    model_override: str | None = None
    jina_api_key: str = ""
    http_client: httpx.AsyncClient | None = None
    use_reranker: bool = False
    expander_prompt_key: str = "prompt_1"
    case_expander_prompt_key: str = "prompt_3"   # sectioned default
    concurrency: int = 10
    unfold_mode: str = "precise"
    include_reg: bool = True
    include_compliance: bool = True
    include_cases: bool = True
    detail_level: DetailLevel = "medium"
    # Planner integration (V4 cut-1). Default OFF — pre-existing behavior is
    # byte-identical when ``enable_planner=False``.
    enable_planner: bool = False
    planner_model: str | None = None
    # Planner-driven runtime knobs (forward-compat with ``apply_plan_to_deps``).
    expander_max_queries: dict[str, int] | None = None
    sectors_override: list[str] | None = None
    reg_rrf_min_score: float | None = None
    case_score_threshold: float | None = None
    # Phase 6 clarification hook — superseded by the deferred-tool path in
    # cut-2 (Task 13.7).  ask_user is now a @agent.tool_plain on the planner
    # that raises CallDeferred; the orchestrator receives a DeferredToolRequests
    # output and handles the resume cycle directly.  The callable field has been
    # removed; callers that previously set ask_user= should be updated.
    # Per-run keep caps for each domain's reranker (applied per sub-query
    # for reg/case, and to the total kept pool for compliance).
    reg_max_high: int = 8
    reg_max_medium: int = 4
    case_max_high: int = 6
    case_max_medium: int = 4
    compliance_max_high: int = 6
    compliance_max_medium: int = 4
    # Optional logger injected by the monitor harness so the aggregator's
    # exact prompt + raw LLM output + thinking + validation all land on disk
    # alongside the per-phase logs. Production callers leave this None.
    aggregator_logger: Any | None = None
    _events: list[dict] = field(default_factory=list)
    _plan: PlannerOutput | None = None
    _ura: UnifiedRetrievalArtifact | None = None
    _reg_rqrs: list[RerankerQueryResult] = field(default_factory=list)
    _comp_rqrs: list[RerankerQueryResult] = field(default_factory=list)
    _case_rqrs: list[RerankerQueryResult] = field(default_factory=list)
    _per_executor_stats: dict[str, dict] = field(default_factory=dict)
    # Log dirs created by each phase -- monitor reads these to copy raw
    # expander / search / reranker dumps into the monitor session folder.
    _reg_log_dir: str | None = None
    _comp_log_dir: str | None = None
    _case_log_dir: str | None = None
    # AggregatorInput as actually handed to handle_aggregator_turn (post-merge,
    # post-from_ura). Useful for the monitor to render exactly what the LLM saw.
    _aggregator_input: Any | None = None

    def __post_init__(self) -> None:
        # ``DetailLevel`` is a dataclass type hint, not a runtime constraint.
        # Defend against CLIs / tests / future callers that skip the preferences
        # service allow-list. Production path (``get_detail_level`` +
        # ``build_aggregator_deps``) already double-validates; this is the third
        # layer so the value that ends up in prompts is always one of three.
        if self.detail_level not in ("low", "medium", "high"):
            logger.warning(
                "FullLoopDeps.detail_level=%r invalid; falling back to 'medium'",
                self.detail_level,
            )
            self.detail_level = "medium"


# ---------------------------------------------------------------------------
# Phase wrappers
# ---------------------------------------------------------------------------


async def _run_reg_phase(
    query: str,
    query_id: int,
    deps: FullLoopDeps,
) -> tuple[list[RerankerQueryResult], str, list[str]]:
    """Run the reg_search graph through the reranker and return shared RQRs.

    Returns ``(rqrs, log_id, sectors)``:
        - ``rqrs`` -- shared RerankerQueryResult list (typed to
          ``RegURAResult`` via the reg adapter). Empty on failure.
        - ``log_id`` -- always populated so downstream URA / aggregator
          share the same trace id.
        - ``sectors`` -- expander-picked legal sectors (passed on to
          ``build_ura_from_phases`` as ``sector_filter``).

    Mirrors the old ``_run_reg_search_phase`` behavior for the best-effort
    ``run.md`` / ``run.json`` log writes so the reg_search log directory
    stays consistent.
    """
    if not deps.include_reg:
        # Mirror the disabled-phase contract used by compliance / case below.
        # We still record a zeroed ``_per_executor_stats`` entry so monitor
        # logs see the phase as "ran with 0 work" rather than missing entirely.
        deps._per_executor_stats["reg_search"] = {
            "duration_ms": 0,
            "total_tokens_in": 0,
            "total_tokens_out": 0,
        }
        _logfire.info("deep_search.phase.reg.skipped", query_id=query_id)
        return ([], "", [])

    log_id = (
        make_log_id(query_id)
        if query_id
        else datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    )
    create_run_dir(log_id)
    _phase_span = _logfire.span(
        "deep_search.phase.reg",
        query_id=query_id,
        log_id=log_id,
        expander_prompt_key=deps.expander_prompt_key,
        unfold_mode=deps.unfold_mode,
        concurrency=deps.concurrency,
        use_reranker=deps.use_reranker,
        reg_max_high=deps.reg_max_high,
        reg_max_medium=deps.reg_max_medium,
    )
    _phase_span.__enter__()

    reg_deps = RegSearchDeps(
        supabase=deps.supabase,
        embedding_fn=deps.embedding_fn,
        jina_api_key=deps.jina_api_key,
        http_client=deps.http_client,
        use_reranker=deps.use_reranker,
        _query_id=query_id,
    )
    reg_deps._log_id = log_id

    reg_expander_cap: int | None = None
    if deps.expander_max_queries:
        reg_expander_cap = deps.expander_max_queries.get("reg")

    state = RegLoopState(
        focus_instruction=query,
        user_context="",
        expander_prompt_key=deps.expander_prompt_key,
        model_override=deps.model_override,
        unfold_mode=deps.unfold_mode,
        concurrency=deps.concurrency,
        skip_aggregator=True,
        reranker_max_high=deps.reg_max_high,
        reranker_max_medium=deps.reg_max_medium,
        expander_max_queries=reg_expander_cap,
        sectors_override=(
            list(deps.sectors_override) if deps.sectors_override else None
        ),
        rrf_min_score=deps.reg_rrf_min_score,
    )

    # Best-effort: thread RRF threshold onto reg's pre-reranker deps as well
    # (search-time rrf floor; distinct from the reranker drop-floor wired via
    # state.rrf_min_score).
    if deps.reg_rrf_min_score is not None:
        try:
            reg_deps.rrf_min_score = deps.reg_rrf_min_score
        except Exception:
            pass

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
        "_run_reg_phase: log_id=%s, %d reranker sub-queries, duration=%.2fs",
        log_id, len(reranker_results), duration,
    )

    total_in = sum(int(u.get("input_tokens", 0) or 0) for u in state.inner_usage)
    total_out = sum(int(u.get("output_tokens", 0) or 0) for u in state.inner_usage)
    deps._per_executor_stats["reg_search"] = {
        "duration_ms": int(duration * 1000),
        "total_tokens_in": total_in,
        "total_tokens_out": total_out,
    }

    placeholder = RegSearchResult(
        quality="pending",
        summary_md=(
            "URA pipeline: reg_search phase completed; aggregator runs downstream."
        ),
        citations=[],
        domain="regulations",
        queries_used=list(state.all_queries_used),
        rounds_used=state.round_count,
        expander_prompt_key=deps.expander_prompt_key,
        aggregator_prompt_key="prompt_1",
    )
    try:
        save_run_overview_md(
            log_id=log_id,
            focus_instruction=query,
            user_context="",
            expander_prompt_key=deps.expander_prompt_key,
            aggregator_prompt_key="prompt_1",
            duration_s=duration,
            result=placeholder,
            round_summaries=[],
        )
        save_run_json(
            log_id=log_id,
            focus_instruction=query,
            user_context="",
            expander_prompt_key=deps.expander_prompt_key,
            aggregator_prompt_key="prompt_1",
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

    # Reg expander no longer picks sectors (planner is the sole source).
    # The phase still returns this slot for backward compat — always empty.
    sectors: list[str] = []

    # Hand the per-phase log dir back to the monitor (best-effort path -- the
    # writer above stamps `reg_search/reports/query_{id}/{log_id}/`).
    try:
        from agents.deep_search_v4.reg_search.logger import LOGS_DIR as _REG_LOGS
        deps._reg_log_dir = str(_REG_LOGS / log_id)
    except Exception:
        pass

    try:
        _phase_span.set_attribute("duration_ms", int(duration * 1000))
        _phase_span.set_attribute("total_tokens_in", total_in)
        _phase_span.set_attribute("total_tokens_out", total_out)
        _phase_span.set_attribute("rqr_count", len(reranker_results))
        _phase_span.set_attribute("sectors", sectors)
        _phase_span.set_attribute("rounds_used", state.round_count)
        if error_msg:
            _phase_span.set_attribute("error", error_msg)
    except Exception:
        pass
    _phase_span.__exit__(None, None, None)

    if error_msg and not reranker_results:
        # Graph crashed before producing anything; surface an empty phase so
        # the orchestrator can still assemble the URA from the other two.
        return ([], log_id, sectors)

    return (reg_to_rqr(reranker_results), log_id, sectors)


async def _run_compliance_phase(
    query: str,
    query_id: int,
    deps: FullLoopDeps,
) -> list[RerankerQueryResult]:
    """Run the compliance_search graph and return shared RQRs.

    Runs the graph directly (rather than going through
    :func:`run_compliance_search`) so the orchestrator can read
    ``state.per_query_service_refs`` -- populated by ``SearchNode`` -- and
    hand it to :func:`compliance_to_rqr` for precise per-sub-query
    attribution (Option A, Loop V2 plan Q1). Returns ``[]`` when the phase
    is disabled via ``deps.include_compliance`` or when the graph fails.
    """
    if not deps.include_compliance:
        deps._per_executor_stats["compliance_search"] = {
            "duration_ms": 0,
            "total_tokens_in": 0,
            "total_tokens_out": 0,
        }
        _logfire.info("deep_search.phase.compliance.skipped", query_id=query_id)
        return []

    # Generate a log_id matching reg_search's convention so the per-round
    # reranker/expander/search dumps land in
    # ``compliance_search/reports/query_{id}/{ts}/`` instead of the empty-
    # log_id directory the graph defaults to. ``ComplianceSearchDeps`` has
    # no ``_log_id`` field (unlike ``RegSearchDeps``), so we only thread the
    # id through ``state.log_id`` -- which is what every per-node writer in
    # ``compliance_search/loop.py`` actually reads.
    log_id = (
        make_compliance_log_id(query_id)
        if query_id
        else datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    )
    create_compliance_run_dir(log_id)

    compliance_deps = ComplianceSearchDeps(
        supabase=deps.supabase,
        embedding_fn=deps.embedding_fn,
        jina_api_key=deps.jina_api_key,
        http_client=deps.http_client,
        use_reranker=deps.use_reranker,
        model_override=deps.model_override,
        compliance_max_high=deps.compliance_max_high,
        compliance_max_medium=deps.compliance_max_medium,
    )

    comp_expander_cap: int | None = None
    if deps.expander_max_queries:
        comp_expander_cap = deps.expander_max_queries.get("compliance")

    state = ComplianceLoopState(
        focus_instruction=query,
        user_context="",
        log_id=log_id,
        expander_max_queries=comp_expander_cap,
        reranker_max_high=deps.compliance_max_high,
        reranker_max_medium=deps.compliance_max_medium,
        sectors_override=(
            list(deps.sectors_override) if deps.sectors_override else None
        ),
    )

    t0 = _time.perf_counter()
    error_msg: str | None = None
    result: ComplianceSearchResult | None = None
    try:
        graph_result = await compliance_search_graph.run(
            ComplianceExpanderNode(),
            state=state,
            deps=compliance_deps,
        )
        result = graph_result.output
    except Exception as exc:
        logger.error("compliance_search phase failed: %s", exc, exc_info=True)
        error_msg = str(exc)

    duration = _time.perf_counter() - t0
    deps._events.extend(compliance_deps._events + state.sse_events)
    deps._per_executor_stats["compliance_search"] = {
        "duration_ms": int(duration * 1000),
        "total_tokens_in": sum(int(u.get("input_tokens", 0) or 0) for u in state.inner_usage),
        "total_tokens_out": sum(int(u.get("output_tokens", 0) or 0) for u in state.inner_usage),
    }

    # Best-effort run overview write so the compliance log directory is as
    # complete as reg_search's. Failures here must never fail the phase --
    # the orchestrator still has the in-memory result to ship downstream.
    overview_result = result or ComplianceSearchResult(
        kept_results=list(state.kept_results),
        quality="weak",
        queries_used=list(state.queries_used),
        rounds_used=state.round_count,
    )
    try:
        save_compliance_run_json(
            log_id=log_id,
            focus_instruction=query,
            user_context="",
            duration_s=duration,
            result=overview_result,
            events=list(compliance_deps._events),
            search_results_log=list(state.search_results_log),
            inner_usage=list(state.inner_usage),
            round_summaries=list(state.round_summaries),
            error=error_msg,
        )
        save_compliance_run_md(
            log_id=log_id,
            focus_instruction=query,
            duration_s=duration,
            result=overview_result,
            round_summaries=list(state.round_summaries),
        )
    except Exception as exc:
        logger.debug("compliance_search phase: log write failed: %s", exc)

    try:
        from agents.deep_search_v4.compliance_search.logger import LOGS_DIR as _COMP_LOGS
        deps._comp_log_dir = str(_COMP_LOGS / log_id)
    except Exception:
        pass

    rqrs = (
        []
        if error_msg or result is None
        else compliance_to_rqr(
            result,
            per_query_service_refs=state.per_query_service_refs or None,
            original_focus_instruction=query,
        )
    )

    _logfire.info(
        "deep_search.phase.compliance",
        query_id=query_id,
        log_id=log_id,
        duration_ms=int(duration * 1000),
        total_tokens_in=deps._per_executor_stats["compliance_search"]["total_tokens_in"],
        total_tokens_out=deps._per_executor_stats["compliance_search"]["total_tokens_out"],
        rqr_count=len(rqrs),
        rounds_used=state.round_count,
        error=error_msg,
    )

    if error_msg or result is None:
        return []
    return rqrs


async def _run_case_phase(
    query: str,
    query_id: int,
    deps: FullLoopDeps,
) -> list[RerankerQueryResult]:
    """Run the case_search pipeline and return shared RQRs.

    Returns ``[]`` when disabled via ``deps.include_cases`` or on phase
    failure.
    """
    if not deps.include_cases:
        deps._per_executor_stats["case_search"] = {
            "duration_ms": 0,
            "total_tokens_in": 0,
            "total_tokens_out": 0,
        }
        _logfire.info("deep_search.phase.case.skipped", query_id=query_id)
        return []

    case_deps = CaseSearchDeps(
        supabase=deps.supabase,
        embedding_fn=deps.embedding_fn,
        _query_id=query_id,
        reranker_max_high=deps.case_max_high,
        reranker_max_medium=deps.case_max_medium,
    )
    # Best-effort: thread case score threshold when the deps object exposes it.
    if deps.case_score_threshold is not None:
        try:
            case_deps.score_threshold = deps.case_score_threshold  # type: ignore[attr-defined]
        except Exception:
            pass

    case_expander_cap: int | None = None
    if deps.expander_max_queries:
        case_expander_cap = deps.expander_max_queries.get("cases")

    t0 = _time.perf_counter()
    try:
        result = await run_case_search(
            focus_instruction=query,
            user_context="",
            deps=case_deps,
            expander_prompt_key=deps.case_expander_prompt_key,
            model_override=deps.model_override,
            concurrency=deps.concurrency,
            expander_max_queries=case_expander_cap,
            sectors_override=deps.sectors_override,
            score_threshold=deps.case_score_threshold,
        )
    except Exception as exc:
        logger.error("case_search phase failed: %s", exc, exc_info=True)
        deps._events.extend(case_deps._events)
        deps._per_executor_stats["case_search"] = {
            "duration_ms": int((_time.perf_counter() - t0) * 1000),
            "total_tokens_in": 0,
            "total_tokens_out": 0,
        }
        return []

    deps._events.extend(case_deps._events)
    # `run_case_search` mirrors `state.inner_usage` onto the result before
    # returning, so we can total tokens here the same way the reg phase does.
    total_in = sum(int(u.get("input_tokens", 0) or 0) for u in result.inner_usage)
    total_out = sum(int(u.get("output_tokens", 0) or 0) for u in result.inner_usage)
    deps._per_executor_stats["case_search"] = {
        "duration_ms": int((_time.perf_counter() - t0) * 1000),
        "total_tokens_in": total_in,
        "total_tokens_out": total_out,
    }
    try:
        from agents.deep_search_v4.case_search.logger import LOGS_DIR as _CASE_LOGS
        if case_deps._log_id:
            deps._case_log_dir = str(_CASE_LOGS / case_deps._log_id)
    except Exception:
        pass
    rqrs = case_to_rqr(result)
    _logfire.info(
        "deep_search.phase.case",
        query_id=query_id,
        log_id=getattr(case_deps, "_log_id", None),
        duration_ms=deps._per_executor_stats["case_search"]["duration_ms"],
        total_tokens_in=total_in,
        total_tokens_out=total_out,
        rqr_count=len(rqrs),
        case_max_high=deps.case_max_high,
        case_max_medium=deps.case_max_medium,
    )
    return rqrs


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_full_loop(
    query: str,
    query_id: int,
    deps: FullLoopDeps,
    prompt_key: str = "prompt_1",
) -> AggregatorOutput:
    """Three-way parallel executor pipeline -> URA 2.0 -> aggregator.

    Runs ``_run_reg_phase`` / ``_run_compliance_phase`` / ``_run_case_phase``
    concurrently via :func:`asyncio.gather`, merges their shared
    ``RerankerQueryResult`` streams into a single
    ``UnifiedRetrievalArtifact`` via :func:`build_ura_from_phases`, then
    invokes the aggregator with URA + ``detail_level`` threaded through.
    """
    _full_span = _logfire.span(
        "deep_search.run_full_loop",
        query_id=query_id,
        query_length=len(query),
        detail_level=deps.detail_level,
        include_reg=deps.include_reg,
        include_compliance=deps.include_compliance,
        include_cases=deps.include_cases,
        enable_planner=deps.enable_planner,
        prompt_key=prompt_key,
        concurrency=deps.concurrency,
    )
    _full_span.__enter__()

    # Planner pass (V4 Phase 2). Default OFF — preserves byte-identical
    # behavior for callers that haven't opted into the planner. When ON, the
    # planner output overrides the caller-supplied ``prompt_key`` for the
    # aggregator (see V4_PLANNER_DESIGN.md §4.4).
    if deps.enable_planner:
        planner_deps = PlannerDeps(
            model_override=deps.planner_model,
            emit_sse=None,
        )
        try:
            with _logfire.span(
                "deep_search.planner",
                query_id=query_id,
                planner_model=deps.planner_model,
            ) as _planner_span:
                plan = await run_planner(query=query, deps=planner_deps)
                try:
                    if plan is not None:
                        _planner_span.set_attribute("invoke", list(getattr(plan, "invoke", []) or []))
                        _planner_span.set_attribute("sectors", list(getattr(plan, "sectors", []) or []))
                        _planner_span.set_attribute("focus", getattr(plan, "focus", None))
                except Exception:
                    pass
        except Exception as exc:
            # ``run_planner`` already has a degraded fallback; this catch is
            # only a last-resort guard so a planner crash never kills the run.
            logger.error(
                "run_full_loop[%s]: planner crashed unexpectedly: %s",
                query_id, exc, exc_info=True,
            )
            plan = None

        if plan is not None:
            deps = apply_plan_to_deps(deps, plan)
            deps._events.extend(planner_deps._events)
            deps._events.append({
                "event": "plan_ready",
                "plan": plan.model_dump(mode="json"),
            })
            deps._plan = plan
            # Planner's invoke set drives the aggregator prompt key
            # (programmatic mapping, not LLM-chosen).
            prompt_key = derive_aggregator_prompt_key(plan)

    logger.info(
        "run_full_loop[%s]: launching reg + compliance + case in parallel "
        "(include_reg=%s include_compliance=%s include_cases=%s)",
        query_id,
        deps.include_reg,
        deps.include_compliance,
        deps.include_cases,
    )

    (reg_sqs, reg_log_id, sectors), comp_sqs, case_sqs = await asyncio.gather(
        _run_reg_phase(query, query_id, deps),
        _run_compliance_phase(query, query_id, deps),
        _run_case_phase(query, query_id, deps),
    )

    # Stash raw RQR lists on deps so the orchestrator layer can persist
    # pre-merge reranker_runs without re-invoking the phases.
    deps._reg_rqrs = list(reg_sqs or [])
    deps._comp_rqrs = list(comp_sqs or [])
    deps._case_rqrs = list(case_sqs or [])

    # Sectors come from the planner only (the executors no longer pick).
    sector_filter = list(deps.sectors_override) if deps.sectors_override else []
    sector_source = "planner" if sector_filter else "none"
    logger.info(
        "run_full_loop[%s]: sector_filter source=%s value=%s",
        query_id, sector_source, sector_filter,
    )

    ura = build_ura_from_phases(
        reg_rqrs=reg_sqs,
        compliance_rqrs=comp_sqs,
        case_rqrs=case_sqs,
        original_query=query,
        query_id=query_id,
        log_id=reg_log_id,
        sector_filter=sector_filter,
    )
    deps._ura = ura
    logger.info(
        "run_full_loop[%s]: URA built -- high=%d medium=%d "
        "(reg=%s comp=%s cases=%s)",
        query_id,
        len(ura.high_results),
        len(ura.medium_results),
        ura.produced_by.get("reg_search"),
        ura.produced_by.get("compliance_search"),
        ura.produced_by.get("case_search"),
    )

    agg_input = AggregatorInput.from_ura(
        ura,
        prompt_key=prompt_key,
        detail_level=deps.detail_level,
    )
    deps._aggregator_input = agg_input
    agg_deps = build_aggregator_deps(
        detail_level=deps.detail_level,
        supabase=deps.supabase,
        logger=deps.aggregator_logger,
    )
    with _logfire.span(
        "deep_search.aggregator",
        query_id=query_id,
        prompt_key=prompt_key,
        detail_level=deps.detail_level,
        ura_high=len(ura.high_results),
        ura_medium=len(ura.medium_results),
    ) as _agg_span:
        agg_output = await handle_aggregator_turn(agg_input, agg_deps)
        try:
            _agg_span.set_attribute("references", len(agg_output.references))
            _agg_span.set_attribute("confidence", agg_output.confidence)
            _agg_span.set_attribute("model_used", getattr(agg_output, "model_used", None))
        except Exception:
            pass
    deps._events.extend(agg_deps._events)

    logger.info(
        "run_full_loop[%s]: done -- %d references, confidence=%s",
        query_id,
        len(agg_output.references),
        agg_output.confidence,
    )

    try:
        _full_span.set_attribute("references", len(agg_output.references))
        _full_span.set_attribute("confidence", agg_output.confidence)
        _full_span.set_attribute("ura_high", len(ura.high_results))
        _full_span.set_attribute("ura_medium", len(ura.medium_results))
        _full_span.set_attribute("sector_source", sector_source)
        _full_span.set_attribute("sector_filter", sector_filter)
        for _phase, _stats in (deps._per_executor_stats or {}).items():
            _full_span.set_attribute(f"phase.{_phase}.duration_ms", _stats.get("duration_ms"))
            _full_span.set_attribute(f"phase.{_phase}.tokens_in", _stats.get("total_tokens_in"))
            _full_span.set_attribute(f"phase.{_phase}.tokens_out", _stats.get("total_tokens_out"))
    except Exception:
        pass
    _full_span.__exit__(None, None, None)
    return agg_output


__all__ = ["FullLoopDeps", "DetailLevel", "run_full_loop"]
