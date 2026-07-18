"""End-to-end URA pipeline runner (Loop V2).

Pipeline:
    reg_search phase   ┐  (regulations + appendixes + circulars + services,
                       │   unified ``search_topics`` corpus)
                       ├── asyncio.gather → list[shared.RerankerQueryResult] x 2
    case_search phase  ┘                                 │
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

Wave D replaced the old two-stage ``PartialURA -> URA`` plumbing with peer
executors running in parallel and a single-pass merger. The reg_compliance
executor spans the statutory ground AND the procedural/services ground (the
unified ``search_topics`` corpus). Phase failures are isolated: an individual
executor throwing does not kill the loop -- its phase is logged and returns an
empty ``list[RerankerQueryResult]``.
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
from agents.deep_search_v4.case_search.loop import run_case_search
from agents.deep_search_v4.case_search.models import CaseSearchDeps
from agents.deep_search_v4.reg_compliance_search.logger import (
    create_run_dir,
    make_log_id,
    save_run_json,
    save_run_overview_md,
)
from agents.deep_search_v4.reg_compliance_search.loop import ExpanderNode, reg_compliance_graph
from agents.deep_search_v4.reg_compliance_search.models import (
    LoopState as RegComplianceLoopState,
    RegComplianceSearchDeps,
    RegSearchResult,
)
from agents.deep_search_v4.shared import DEFAULT_SEARCH_CONCURRENCY
from agents.deep_search_v4.shared.context import ContextBlock
from agents.deep_search_v4.shared.models import RerankerQueryResult
from agents.deep_search_v4.ura.case_adapter import case_to_rqr
from agents.deep_search_v4.ura.enrich import enrich_ura
from agents.deep_search_v4.ura.merger import build_ura_from_phases
from agents.deep_search_v4.ura.reg_adapter import reg_compliance_to_rqr
from agents.deep_search_v4.ura.schema import UnifiedRetrievalArtifact
from agents.deep_search_v4.planner import PlannerDeps, RetrievalConfig
from agents.deep_search_v4.sector_picker import run_sector_picker
from agents.utils.agent_models import usage_by_model
from agents.utils.tracking import track_stage
from shared.observability import get_logfire

logger = logging.getLogger(__name__)
_logfire = get_logfire()


DetailLevel = Literal["low", "medium", "high"]


@dataclass
class FullLoopDeps:
    """Dependencies for :func:`run_full_loop`.

    Supabase + embedding_fn are shared across the reg + case phases. The reg
    executor spans regulations, appendixes, circulars and services (unified
    ``search_topics`` corpus). The aggregator gets its own deps constructed
    internally via :func:`build_aggregator_deps`, threaded with ``detail_level``.

    Callers that want to persist the retrieval artifact / reranker_runs can
    read back ``_ura``, ``_reg_rqrs``, ``_case_rqrs`` after ``run_full_loop``
    returns. ``_per_executor_stats`` carries per-phase timing + token totals
    (populated by the phase wrappers).
    """

    supabase: SupabaseClient
    embedding_fn: Callable[[str], Awaitable[list[float]]]
    model_override: str | None = None
    jina_api_key: str = ""
    http_client: httpx.AsyncClient | None = None
    use_reranker: bool = False
    expander_prompt_key: str = "prompt_1"
    case_expander_prompt_key: str = "prompt_3"   # sectioned default
    concurrency: int = DEFAULT_SEARCH_CONCURRENCY
    unfold_mode: str = "precise"
    include_reg_compliance: bool = True
    include_cases: bool = True
    detail_level: DetailLevel = "medium"
    # DEPRECATED — the planner redesign moved planning out of run_full_loop into
    # the planner-owned loop (``run_retrieval`` + ``handle_planner_turn``). These
    # two fields are inert: run_full_loop no longer runs a planner. Kept only so
    # existing CLI / monitor constructors don't break.
    enable_planner: bool = False
    planner_model: str | None = None
    # Planner-driven runtime knobs. ``run_retrieval`` populates these from the
    # mode-derived ``RetrievalConfig``.
    # Per-executor result budget ("reg" / "cases"). When set, the executor loop
    # derives reranker_max_keep dynamically from it; when None the fixed
    # ``*_max_keep`` caps apply. See MODE_PROFILES.md §1.
    result_budget: dict[str, int] | None = None
    sectors_override: list[str] | None = None
    # Sector AND-filter as an in-flight future, resolved by the sector_picker
    # agent running in parallel with the executors. When set, the executors'
    # filter steps ``await`` this future at the join point (post-RPC, before the
    # reranker). When ``None``, executors fall back to the static
    # ``sectors_override`` above. The future itself may resolve to
    # ``None`` (picker timed out / errored / emitted an out-of-bound list) —
    # the loops treat that as "no filter". Populated by ``run_retrieval``;
    # left ``None`` by CLI / monitor paths that construct ``FullLoopDeps``
    # directly without the planner-driven picker.
    sectors_future: "asyncio.Future[list[str] | None] | None" = None
    case_score_threshold: float | None = None
    # Phase 6 clarification hook — superseded by the deferred-tool path in
    # cut-2 (Task 13.7).  ask_user is now a @agent.tool_plain on the planner
    # that raises CallDeferred; the orchestrator receives a DeferredToolRequests
    # output and handles the resume cycle directly.  The callable field has been
    # removed; callers that previously set ask_user= should be updated.
    # Per-run flat keep cap for each domain's reranker. One cap over all kept
    # results; within the cap, high-relevance results are ordered ahead of
    # medium, ties broken by score (descending), before truncation.
    reg_max_keep: int = 8
    case_max_keep: int = 10
    # Optional logger injected by the monitor harness so the aggregator's
    # exact prompt + raw LLM output + thinking + validation all land on disk
    # alongside the per-phase logs. Production callers leave this None.
    aggregator_logger: Any | None = None
    # Planner-curated context bundle (§3.6 / §4 / §5.1 / §5.3). The same
    # filtered list flows to every executor's ``LoopState.context_blocks`` AND
    # to ``AggregatorInput.context_blocks``. ``run_retrieval`` populates this
    # from ``decision.context_labels``; ``run_full_loop`` reads it. Default
    # empty preserves pre-redesign behavior. NEVER threaded into any reranker.
    context_blocks: list[ContextBlock] = field(default_factory=list)
    _events: list[dict] = field(default_factory=list)
    # Telemetry stash — the planner's RetrievalConfig / decision, when run via
    # run_retrieval. Typed Any to keep this module decoupled from the schema.
    _plan: Any = None
    _ura: UnifiedRetrievalArtifact | None = None
    _reg_rqrs: list[RerankerQueryResult] = field(default_factory=list)
    _case_rqrs: list[RerankerQueryResult] = field(default_factory=list)
    _per_executor_stats: dict[str, dict] = field(default_factory=dict)
    # Log dirs created by each phase -- monitor reads these to copy raw
    # expander / search / reranker dumps into the monitor session folder.
    _reg_log_dir: str | None = None
    _case_log_dir: str | None = None
    # AggregatorInput as actually handed to handle_aggregator_turn (post-merge,
    # post-from_ura). Useful for the monitor to render exactly what the LLM saw.
    _aggregator_input: Any | None = None
    # Conversation identity — populated by ``run_retrieval`` from PlannerDeps so
    # every span emitted inside ``run_full_loop`` (per-phase, aggregator) can be
    # filtered by conversation_id in Logfire without a trace_id pivot. Defaults
    # to empty for CLI / smoke-test paths that have no real conversation.
    # NOTE: only conversation_id is propagated here (user_id is intentionally
    # NOT carried). The monitor / forensic agent recovers user_id via Supabase
    # join when needed; keeping user_id off ~10× more span surfaces is a
    # belt-and-suspenders PII reduction. router.classify + dispatch.specialist
    # still carry user_id from before — those are the canonical user-tagged spans.
    conversation_id: str = ""
    # Live SSE progress sink — copied from ``PlannerDeps.emit_sse`` by
    # ``run_retrieval``. Fired DIRECTLY (never via ``_events``) at four
    # boundaries only: the end of each executor phase and just before the
    # aggregator turn. ``None`` for CLI / monitor / smoke-test callers that
    # build FullLoopDeps by hand → every emit site is a guarded no-op.
    emit_sse: Callable[[dict], None] | None = None

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
# Live progress (SSE `agent_progress`) — phase-boundary events only
# ---------------------------------------------------------------------------

# Arabic labels for the three executor phases. Static labels + numbers ONLY —
# progress text is relayed to the client WITHOUT passing through the وضع السرية
# StreamDecoder (only `token` events are decoded), so it must never carry
# LLM-derived content (sector names, titles, …) that could leak a fake identifier.
_PHASE_TEXT_AR = {
    "reg_compliance": "اكتمل البحث في الأنظمة والتعاميم والخدمات",
    "case": "اكتمل البحث في الأحكام القضائية",
}


def _count_sources(rqrs: list[RerankerQueryResult]) -> int:
    """Total kept results across a phase's per-sub-query reranker outputs."""
    total = 0
    for rqr in rqrs or []:
        try:
            total += len(rqr.results or [])
        except Exception:  # pragma: no cover - defensive
            continue
    return total


def _emit_progress(deps: FullLoopDeps, stage: str, text: str = "", **data: Any) -> None:
    """Fire ONE ``agent_progress`` event on ``deps.emit_sse`` — guarded, best-effort.

    Called DIRECTLY and never appended to ``deps._events``: that list is
    batch-flushed by the orchestrator right before the answer tokens, so an event
    living on both surfaces would be delivered twice. Never raises — a broken
    progress sink must not perturb retrieval.

    The ~50 free-text ``status`` emits inside the executor loops (loop.py /
    search.py) deliberately stay on ``_events`` (batched) — this is the
    phase-boundary feed only.
    """
    sink = getattr(deps, "emit_sse", None)
    if sink is None:
        return
    try:
        sink({
            "type": "agent_progress",
            "stage": stage,
            "text": text,
            "data": {k: v for k, v in data.items() if v is not None},
        })
    except Exception:
        logger.debug("emit_progress failed (stage=%s)", stage, exc_info=True)


# ---------------------------------------------------------------------------
# Per-call cost ledger (llm_calls) — per-stage emission
# ---------------------------------------------------------------------------

# inner_usage `agent` role → ledger stage label. Rerankers collapse to one
# "reranker" row per executor (all rounds summed); anything unmapped keeps its
# own role so cost is never silently dropped.
_STAGE_LABELS = {"expander": "expansion", "reranker": "reranker"}


def _emit_executor_ledger(executor_short: str, inner_usage: list[dict]) -> None:
    """Emit per-stage ``llm_calls`` rows for one executor.

    Splits ``inner_usage`` by sub-agent role → ``deep_search.expansion.{exec}``
    and ``deep_search.reranker.{exec}`` (reranker rounds summed), each priced per
    model. Runs inside the dispatch's capture scope; a no-op outside it. Never
    raises — telemetry must not perturb retrieval.
    """
    try:
        from agents.utils.usage_sink import record_call
        buckets: dict[str, list[dict]] = {}
        for u in inner_usage or []:
            role = str(u.get("agent") or "")
            stage = _STAGE_LABELS.get(role, role or "unknown")
            buckets.setdefault(stage, []).append(u)
        for stage, entries in buckets.items():
            for model, toks in usage_by_model(entries).items():
                ti, to = int(toks.get("input", 0) or 0), int(toks.get("output", 0) or 0)
                if not ti and not to:
                    continue
                record_call(
                    agent=f"deep_search.{stage}.{executor_short}",
                    model=model,
                    agent_family="deep_search",
                    tokens_in=ti,
                    tokens_out=to,
                    tokens_reasoning=int(toks.get("reasoning", 0) or 0),
                    tokens_cached=int(toks.get("cached", 0) or 0),
                )
    except Exception:
        logger.debug("executor ledger emit failed for %s", executor_short, exc_info=True)


# ---------------------------------------------------------------------------
# Phase wrappers
# ---------------------------------------------------------------------------


async def _run_reg_compliance_phase(
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
    if not deps.include_reg_compliance:
        # Mirror the disabled-phase contract used by the case phase below.
        # We still record a zeroed ``_per_executor_stats`` entry so monitor
        # logs see the phase as "ran with 0 work" rather than missing entirely.
        deps._per_executor_stats["reg_search"] = {
            "duration_ms": 0,
            "total_tokens_in": 0,
            "total_tokens_out": 0,
        }
        _logfire.info(
            "deep_search.phase.reg_compliance.skipped",
            query_id=query_id,
            conversation_id=deps.conversation_id or None,
        )
        return ([], "", [])

    log_id = (
        make_log_id(query_id)
        if query_id
        else datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    )
    create_run_dir(log_id)
    with track_stage(
        "deep_search.phase.reg_compliance",
        conversation_id=deps.conversation_id or None,
        agent_family="deep_search",
        query_id=query_id,
        log_id=log_id,
        expander_prompt_key=deps.expander_prompt_key,
        unfold_mode=deps.unfold_mode,
        concurrency=deps.concurrency,
        reg_max_keep=deps.reg_max_keep,
    ) as _phase_span:
        reg_deps = RegComplianceSearchDeps(
            supabase=deps.supabase,
            embedding_fn=deps.embedding_fn,
            _query_id=query_id,
            # Thread the live progress sink so the per-sub-query topic line
            # streams as it happens instead of arriving in the terminal batch.
            emit_sse=deps.emit_sse,
        )
        reg_deps._log_id = log_id

        reg_budget: int | None = None
        if deps.result_budget:
            reg_budget = deps.result_budget.get("reg_compliance")

        state = RegComplianceLoopState(
            focus_instruction=query,
            user_context="",
            expander_prompt_key=deps.expander_prompt_key,
            model_override=deps.model_override,
            unfold_mode=deps.unfold_mode,
            concurrency=deps.concurrency,
            skip_aggregator=True,
            reranker_max_keep=deps.reg_max_keep,
            result_budget=reg_budget,
            sectors_override=(
                list(deps.sectors_override) if deps.sectors_override else None
            ),
            sectors_future=deps.sectors_future,
            context_blocks=list(deps.context_blocks) if deps.context_blocks else [],
        )

        t0 = _time.perf_counter()
        error_msg: str | None = None
        try:
            await reg_compliance_graph.run(ExpanderNode(), state=state, deps=reg_deps)
        except Exception as exc:
            logger.error("reg_search phase failed: %s", exc, exc_info=True)
            error_msg = str(exc)

        duration = _time.perf_counter() - t0
        deps._events.extend(reg_deps._events + state.sse_events)

        reranker_results = list(state.reranker_results)
        logger.info(
            "_run_reg_compliance_phase: log_id=%s, %d reranker sub-queries, duration=%.2fs",
            log_id, len(reranker_results), duration,
        )

        total_in = sum(int(u.get("input_tokens", 0) or 0) for u in state.inner_usage)
        total_out = sum(int(u.get("output_tokens", 0) or 0) for u in state.inner_usage)
        total_cached = sum(int(u.get("cached_tokens", 0) or 0) for u in state.inner_usage)
        deps._per_executor_stats["reg_search"] = {
            "duration_ms": int(duration * 1000),
            "total_tokens_in": total_in,
            "total_tokens_out": total_out,
            # Prompt-cache-read subset of total_tokens_in (per-query visibility).
            "total_tokens_cached": total_cached,
            # Per-model token split — kept for the monitor / per_phase_stats.
            "per_model": usage_by_model(state.inner_usage),
        }
        _emit_executor_ledger("reg_compliance", state.inner_usage)

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
            from agents.deep_search_v4.reg_compliance_search.logger import LOGS_DIR as _REG_LOGS
            deps._reg_log_dir = str(_REG_LOGS / log_id)
        except Exception:
            pass

        _phase_span.set(
            duration_ms=int(duration * 1000),
            total_tokens_in=total_in,
            total_tokens_out=total_out,
            rqr_count=len(reranker_results),
            sectors=sectors,
            rounds_used=state.round_count,
        )
        if error_msg:
            _phase_span.set(error=error_msg)

        if error_msg and not reranker_results:
            # Graph crashed before producing anything; surface an empty phase so
            # the orchestrator can still assemble the URA from the other two.
            _emit_progress(
                deps, "searching", _PHASE_TEXT_AR["reg_compliance"],
                phase="reg_compliance", sources=0, queries=0,
            )
            return ([], log_id, sectors)

        reg_rqrs = reg_compliance_to_rqr(reranker_results)
        _emit_progress(
            deps, "searching", _PHASE_TEXT_AR["reg_compliance"],
            phase="reg_compliance",
            sources=_count_sources(reg_rqrs),
            queries=len(reg_rqrs),
        )
        return (reg_rqrs, log_id, sectors)


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
        _logfire.info(
            "deep_search.phase.case.skipped",
            query_id=query_id,
            conversation_id=deps.conversation_id or None,
        )
        return []

    case_budget: int | None = None
    if deps.result_budget:
        case_budget = deps.result_budget.get("cases")

    case_deps = CaseSearchDeps(
        supabase=deps.supabase,
        embedding_fn=deps.embedding_fn,
        _query_id=query_id,
        reranker_max_keep=deps.case_max_keep,
        result_budget=case_budget,
        # Thread the live progress sink so each per-sub-query topic line streams
        # as it happens instead of arriving in the terminal batch.
        emit_sse=deps.emit_sse,
    )
    # Best-effort: thread case score threshold when the deps object exposes it.
    if deps.case_score_threshold is not None:
        try:
            case_deps.score_threshold = deps.case_score_threshold  # type: ignore[attr-defined]
        except Exception:
            pass

    t0 = _time.perf_counter()
    try:
        result = await run_case_search(
            focus_instruction=query,
            user_context="",
            deps=case_deps,
            expander_prompt_key=deps.case_expander_prompt_key,
            model_override=deps.model_override,
            concurrency=deps.concurrency,
            sectors_override=deps.sectors_override,
            sectors_future=deps.sectors_future,
            score_threshold=deps.case_score_threshold,
            context_blocks=list(deps.context_blocks) if deps.context_blocks else None,
        )
    except Exception as exc:
        logger.error("case_search phase failed: %s", exc, exc_info=True)
        deps._events.extend(case_deps._events)
        deps._per_executor_stats["case_search"] = {
            "duration_ms": int((_time.perf_counter() - t0) * 1000),
            "total_tokens_in": 0,
            "total_tokens_out": 0,
        }
        _emit_progress(
            deps, "searching", _PHASE_TEXT_AR["case"],
            phase="case", sources=0, queries=0,
        )
        return []

    deps._events.extend(case_deps._events)
    # `run_case_search` mirrors `state.inner_usage` onto the result before
    # returning, so we can total tokens here the same way the reg phase does.
    total_in = sum(int(u.get("input_tokens", 0) or 0) for u in result.inner_usage)
    total_out = sum(int(u.get("output_tokens", 0) or 0) for u in result.inner_usage)
    total_cached = sum(int(u.get("cached_tokens", 0) or 0) for u in result.inner_usage)
    deps._per_executor_stats["case_search"] = {
        "duration_ms": int((_time.perf_counter() - t0) * 1000),
        "total_tokens_in": total_in,
        "total_tokens_out": total_out,
        "total_tokens_cached": total_cached,
        "per_model": usage_by_model(result.inner_usage),
    }
    _emit_executor_ledger("case", result.inner_usage)
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
        conversation_id=deps.conversation_id or None,
        log_id=getattr(case_deps, "_log_id", None),
        duration_ms=deps._per_executor_stats["case_search"]["duration_ms"],
        total_tokens_in=total_in,
        total_tokens_out=total_out,
        rqr_count=len(rqrs),
        case_max_keep=deps.case_max_keep,
    )
    _emit_progress(
        deps, "searching", _PHASE_TEXT_AR["case"],
        phase="case",
        sources=_count_sources(rqrs),
        queries=len(rqrs),
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
    """Two-way parallel executor pipeline -> URA 2.0 -> aggregator.

    Runs ``_run_reg_compliance_phase`` (regulations + appendixes + circulars + services)
    and ``_run_case_phase`` concurrently via :func:`asyncio.gather`, merges
    their shared ``RerankerQueryResult`` streams into a single
    ``UnifiedRetrievalArtifact`` via :func:`build_ura_from_phases`, then
    invokes the aggregator with URA + ``detail_level`` threaded through.
    """
    with track_stage(
        "deep_search.run_full_loop",
        conversation_id=deps.conversation_id or None,
        agent_family="deep_search",
        query_id=query_id,
        query_length=len(query),
        detail_level=deps.detail_level,
        include_reg_compliance=deps.include_reg_compliance,
        include_cases=deps.include_cases,
        enable_planner=deps.enable_planner,
        prompt_key=prompt_key,
        concurrency=deps.concurrency,
    ) as _full_span:
        # No planner branch here. The planner redesign moved planning into the
        # planner-owned loop: the planner derives a RetrievalConfig and calls
        # ``run_retrieval``, which assembles a populated ``FullLoopDeps`` (executor
        # toggles, caps, budgets, ``prompt_key``) before invoking ``run_full_loop``.
        # ``run_full_loop`` is now a thin gather → merge → aggregator pass.

        logger.info(
            "run_full_loop[%s]: launching reg + case in parallel "
            "(include_reg_compliance=%s include_cases=%s)",
            query_id,
            deps.include_reg_compliance,
            deps.include_cases,
        )

        (reg_sqs, reg_log_id, sectors), case_sqs = await asyncio.gather(
            _run_reg_compliance_phase(query, query_id, deps),
            _run_case_phase(query, query_id, deps),
        )

        # Stash raw RQR lists on deps so the orchestrator layer can persist
        # pre-merge reranker_runs without re-invoking the phases.
        deps._reg_rqrs = list(reg_sqs or [])
        deps._case_rqrs = list(case_sqs or [])

        # Sector filter source: the sector_picker future (when wired via
        # ``run_retrieval``) or the static ``sectors_override`` (CLI / monitor
        # paths). The gather above has finished, so retrieval no longer needs
        # the picker. The executors consumed it with a bounded grace and never
        # cancel it, so it may still be running (the picker is uncapped by
        # design). Read it if already resolved; otherwise REAP it here — the
        # uncapped picker is bounded by retrieval lifetime, not a fixed timeout.
        sector_filter: list[str] = []
        sector_source = "none"
        if deps.sectors_future is not None:
            fut = deps.sectors_future
            picked = None
            if fut.cancelled():
                picked = None
            elif fut.done():
                try:
                    picked = fut.result()
                except Exception as exc:
                    logger.warning(
                        "run_full_loop[%s]: sector_picker future raised %s; "
                        "treating as no filter",
                        query_id, type(exc).__name__,
                    )
                    picked = None
            else:
                # Still pending at end of retrieval — reap so it does not orphan.
                fut.cancel()
                await asyncio.gather(fut, return_exceptions=True)
                logger.info(
                    "run_full_loop[%s]: sector_picker still pending at end of "
                    "retrieval — reaped, no filter recorded",
                    query_id,
                )
            if picked:
                sector_filter = list(picked)
                sector_source = "picker"
        elif deps.sectors_override:
            sector_filter = list(deps.sectors_override)
            sector_source = "override"
        logger.info(
            "run_full_loop[%s]: sector_filter source=%s value=%s",
            query_id, sector_source, sector_filter,
        )

        # The reg phase carries regulations, appendixes, circulars AND services
        # (unified ``search_topics`` corpus); the merger routes each kept row by
        # its own ``result.domain``.
        ura = build_ura_from_phases(
            reg_rqrs=reg_sqs,
            case_rqrs=case_sqs,
            original_query=query,
            query_id=query_id,
            log_id=reg_log_id,
            sector_filter=sector_filter,
        )
        # v3.0 two-view URA: post-merge enrichment fills the heavy fields (full
        # chunk content, cross-refs, landing urls, entity names) for the surviving
        # deduped results, and drops empty-content reg results. Best-effort.
        await enrich_ura(ura, deps.supabase)
        deps._ura = ura
        logger.info(
            "run_full_loop[%s]: URA built -- high=%d medium=%d (reg=%s cases=%s)",
            query_id,
            len(ura.high_results),
            len(ura.medium_results),
            ura.produced_by.get("reg_search"),
            ura.produced_by.get("case_search"),
        )

        agg_input = AggregatorInput.from_ura(
            ura,
            prompt_key=prompt_key,
            detail_level=deps.detail_level,
        )
        # Thread the planner-curated context bundle into the aggregator user
        # message (rendered before <references> by build_aggregator_user_message).
        # SAME filtered list the executors received -- §3.6 (run_retrieval changes).
        # ``AggregatorInput.context_blocks`` defaults to []; setting it after
        # ``from_ura`` avoids touching the classmethod (out of scope per Phase D).
        if deps.context_blocks:
            agg_input.context_blocks = list(deps.context_blocks)
        deps._aggregator_input = agg_input
        agg_deps = build_aggregator_deps(
            detail_level=deps.detail_level,
            supabase=deps.supabase,
            logger=deps.aggregator_logger,
        )
        # Live progress: executors are done + the URA is merged — the aggregator
        # (one long LLM call) starts now. Counts stay OUT of this event: the
        # per-phase `searching` events already carry the source tallies the bar
        # displays, and a second (deduped, smaller) number here would make the
        # count visibly shrink mid-run.
        # The aggregator WRITES the answer (synthesis_md + [n] citations) — it is
        # not a merge step. The reranking that precedes it is the `evaluating`
        # stage, emitted from the executor loops' RerankerNodes.
        _emit_progress(deps, "aggregating", "كتابة الإجابة وتوثيق المراجع")

        with track_stage(
            "deep_search.aggregator",
            conversation_id=deps.conversation_id or None,
            agent_family="deep_search",
            query_id=query_id,
            prompt_key=prompt_key,
            detail_level=deps.detail_level,
            ura_high=len(ura.high_results),
            ura_medium=len(ura.medium_results),
        ) as _agg_span:
            agg_output = await handle_aggregator_turn(agg_input, agg_deps)
            _agg_span.set(
                references=len(agg_output.references),
                confidence=agg_output.confidence,
                model_used=getattr(agg_output, "model_used", None),
            )
        deps._events.extend(agg_deps._events)

        logger.info(
            "run_full_loop[%s]: done -- %d references, confidence=%s",
            query_id,
            len(agg_output.references),
            agg_output.confidence,
        )

        _full_span.set(
            references=len(agg_output.references),
            confidence=agg_output.confidence,
            ura_high=len(ura.high_results),
            ura_medium=len(ura.medium_results),
            sector_source=sector_source,
            sector_filter=sector_filter,
        )
        for _phase, _stats in (deps._per_executor_stats or {}).items():
            _full_span.set(**{
                f"phase.{_phase}.duration_ms": _stats.get("duration_ms"),
                f"phase.{_phase}.tokens_in": _stats.get("total_tokens_in"),
                f"phase.{_phase}.tokens_out": _stats.get("total_tokens_out"),
            })
        return agg_output


# ---------------------------------------------------------------------------
# Planner phase 2 — run_retrieval
# ---------------------------------------------------------------------------


def _spawn_sector_picker_task(
    *,
    query: str,
    decision: Any,
    deps: PlannerDeps,
    config: RetrievalConfig,
) -> "asyncio.Future[list[str] | None]":
    """Spawn the sector_picker as a background task, return the future to await.

    The picker runs in parallel with the executors. Each executor awaits this
    future at its sector-filter join point (post-RPC, before the reranker). The
    picker reads the assembled ``ContextBlock`` list the aggregator + expanders
    will see, so its view of the question matches what the rest of the pipeline
    gets.

    Resolves to ``list[str]`` (2-5 canonical sector names) when the picker
    emits a valid filter, or ``None`` for every failure mode (timeout, error,
    null output, under-min / over-max). The loops treat ``None`` as
    "no filter" and run unfiltered.
    """
    # Re-derive the context blocks the picker should see. We can't take the
    # ``selected_blocks`` list from the caller because at picker-spawn time
    # the local variable is in a different scope — recompute here. Cheap.
    # planner_brief is passed to the picker as a dedicated param below, so it is
    # excluded from this block list to avoid a double render.
    blocks = _select_forwarded_blocks(decision, deps, include_planner_brief=False)

    planner_brief = ""
    if decision is not None:
        planner_brief = (getattr(decision, "planner_brief", "") or "").strip()

    mode = (
        config.mode
        if getattr(config, "mode", None) is not None
        else (getattr(decision, "mode", None) or "reg_compliance_led")
    )

    coro = run_sector_picker(
        query=query,
        mode=mode,
        planner_brief=planner_brief,
        context_blocks=blocks,
        model_override=deps.model_override,
        query_id=getattr(deps, "query_id", 0) or 0,
        conversation_id=getattr(deps, "conversation_id", "") or "",
    )
    # asyncio.create_task wraps the coroutine into a Task (which is an
    # awaitable Future). Multiple awaiters share the resolved value.
    return asyncio.create_task(coro, name="deep_search.sector_picker")


def _build_candidate_context_blocks(
    decision: Any,
    deps: PlannerDeps,
) -> dict[str, ContextBlock]:
    """Build the candidate ``ContextBlock`` objects from planner decision + deps.

    Returns a dict keyed by label so the filter step in :func:`run_retrieval`
    can pick by label without scanning. A source is silently skipped when it
    would produce an empty body (e.g. ``deps.case_brief is None``,
    ``decision.planner_brief`` blank, ``deps.prior_searches`` empty).

    Three labels are emitted: ``case_brief``, ``planner_brief``,
    ``prior_search_lessons``. ``attached_artifacts`` is intentionally NOT
    forwarded — the planner reads attachments in its decider phase and, when
    relevant, distills them into ``planner_brief``. See §4.2 (vocabulary table)
    of the full_redesign spec. Note that ``recent_messages`` is NOT a context
    block — it reaches the decider prompt only via dynamic instructions
    (handled by the planner in Wave 2), never downstream.
    """
    candidates: dict[str, ContextBlock] = {}

    # case_brief — persistence="case" (lives with the case across turns).
    if deps.case_brief:
        candidates["case_brief"] = ContextBlock(
            label="case_brief",
            body=deps.case_brief,
            persistence="case",
        )

    # planner_brief — persistence="turn" (recomputed per dispatch).
    brief = (decision.planner_brief or "").strip() if decision is not None else ""
    if brief:
        candidates["planner_brief"] = ContextBlock(
            label="planner_brief",
            body=brief,
            persistence="turn",
        )

    # prior_search_lessons — persistence="conversation".
    # Rendered per §4.2: list of {title, describe_query, confidence, summary}.
    # NEVER the full content_md or original references. When summary is empty
    # (e.g. async trigger hasn't fired yet), render only the other three rows.
    if deps.prior_searches:
        rendered = []
        for ps in deps.prior_searches:
            lines = [
                f"- العنوان: {ps.title}",
                f"  السؤال: {ps.describe_query}",
                f"  الثقة: {ps.confidence}",
            ]
            if ps.summary:
                lines.append(f"  الخلاصة: {ps.summary}")
            rendered.append("\n".join(lines))
        candidates["prior_search_lessons"] = ContextBlock(
            label="prior_search_lessons",
            body="\n\n".join(rendered),
            persistence="conversation",
        )

    # NOTE: attached_artifacts is intentionally NOT forwarded downstream.
    # The planner decider reads ``deps.attached_items`` via its dynamic
    # instructions and, when something in them matters for the search, the
    # planner distills it into ``planner_brief``. Raw content_md never flows
    # to expanders / aggregator.

    return candidates


# Canonical render order for forwarded context blocks. ``case_brief`` and
# ``planner_brief`` are authoritative planner facts (case memory, fetched-article
# text, distilled attachments) and are ALWAYS forwarded when non-empty —
# regardless of whether the planner remembered to list them in
# ``context_labels``. A forgotten label must never again silently drop a
# fetched-article brief from the expander/aggregator (scar: convo ccd1afea —
# planner wrote the brief but emitted ``context_labels=["prior_search_lessons"]``
# so the 3 fetched articles reached nobody). ``prior_search_lessons`` stays
# opt-in via the declared labels.
_CANONICAL_LABEL_ORDER = ("case_brief", "planner_brief", "prior_search_lessons")
_FORCE_FORWARD_LABELS = frozenset({"case_brief", "planner_brief"})


def _select_forwarded_blocks(
    decision: Any,
    deps: PlannerDeps,
    *,
    include_planner_brief: bool,
) -> list[ContextBlock]:
    """Pick the ``ContextBlock`` list to forward downstream.

    The two briefs are force-included when present; ``prior_search_lessons`` is
    included only when the planner declared it in ``context_labels``.

    ``include_planner_brief=False`` is used by the sector_picker, which renders
    ``planner_brief`` through its own dedicated ``<planner_brief>`` tag (it is
    passed to ``run_sector_picker`` as a separate param) and would otherwise
    double-render it inside ``<context_blocks>``.
    """
    candidates = _build_candidate_context_blocks(decision, deps)
    declared = set(getattr(decision, "context_labels", None) or [])
    forced = _FORCE_FORWARD_LABELS if include_planner_brief else (
        _FORCE_FORWARD_LABELS - {"planner_brief"}
    )
    return [
        candidates[label]
        for label in _CANONICAL_LABEL_ORDER
        if label in candidates and (label in forced or label in declared)
    ]


async def run_retrieval(
    query: str,
    config: RetrievalConfig,
    deps: PlannerDeps,
) -> AggregatorOutput:
    """Planner phase 2 — assemble ``FullLoopDeps`` from a ``RetrievalConfig`` and run it.

    Builds the internal ``FullLoopDeps`` (executor toggles, expander caps,
    result budgets, sector override) from the mode-derived ``config``, runs
    ``run_full_loop`` (gather → URA merge → enrich → aggregator), then copies the
    read-back fields off the internal deps onto ``PlannerDeps`` so artifact
    persistence and the monitor still see them (PLANNER_REDESIGN_PLAN.md §6
    Blocking 3). The ``enrich_ura`` stage already runs inside ``run_full_loop``.

    Called by ``planner.handle_planner_turn`` (lazy-imported there to break the
    planner → orchestrator import cycle).
    """
    # Assemble the forwarded context blocks. case_brief + planner_brief are
    # force-forwarded when non-empty regardless of the planner's declared
    # context_labels (see _select_forwarded_blocks); prior_search_lessons is
    # opt-in. The SAME bundle flows to all executor expanders AND the aggregator
    # (§3.6). The reranker continues to receive zero blocks -- enforced by the
    # executors not threading context_blocks into reranker calls (Wave 3A).
    decision = deps._decision
    selected_blocks: list[ContextBlock] = _select_forwarded_blocks(
        decision, deps, include_planner_brief=True
    )
    if selected_blocks:
        logger.info(
            "run_retrieval: context_blocks selected = %s",
            [b.label for b in selected_blocks],
        )

    # Spawn the sector_picker in parallel with the executors. The picker reads
    # the query + planner_brief + assembled context_blocks and emits a 2-5
    # sector AND-filter (or null → unfiltered). Each executor awaits this
    # future at its sector-filter join point:
    #   - reg_search / case_search: post-RPC, before the reranker.
    # The expander + embed + RPC chain in reg/case runs concurrently with the
    # picker LLM call. Picker failure → future resolves to None → unfiltered.
    sectors_future = _spawn_sector_picker_task(
        query=query,
        decision=decision,
        deps=deps,
        config=config,
    )

    full_deps = FullLoopDeps(
        supabase=deps.supabase,
        embedding_fn=deps.embedding_fn,
        model_override=deps.model_override,
        jina_api_key=deps.jina_api_key,
        http_client=deps.http_client,
        concurrency=deps.concurrency,
        unfold_mode=deps.unfold_mode,
        include_reg_compliance=config.include_reg_compliance,
        include_cases=config.include_cases,
        detail_level=deps.detail_level,
        result_budget=dict(config.result_budget),
        # Wave A: ``sectors_override`` retained for non-picker callers (CLI,
        # monitor, smoke tests that build FullLoopDeps directly). When the
        # picker is wired (any planner-driven dispatch), ``sectors_future``
        # is the live source and ``sectors_override`` is left ``None``.
        sectors_override=None,
        sectors_future=sectors_future,
        aggregator_logger=deps.aggregator_logger,
        enable_planner=False,
        context_blocks=selected_blocks,
        conversation_id=getattr(deps, "conversation_id", "") or "",
        # Live progress sink — the SAME callback the planner's logger fires
        # (PlannerDeps.emit_sse). None outside a real dispatch → guarded no-op.
        emit_sse=getattr(deps, "emit_sse", None),
    )
    full_deps._plan = config

    agg_output = await run_full_loop(
        query=query,
        query_id=deps.query_id,
        deps=full_deps,
        prompt_key=config.aggregator_prompt_key,
    )

    # Publisher read-back — copy everything the orchestrator / monitor needs off
    # the internal FullLoopDeps onto the PlannerDeps. Without this, artifact
    # persistence breaks (§6 Blocking 3).
    deps._ura = full_deps._ura
    deps._reg_rqrs = list(full_deps._reg_rqrs or [])
    # PlannerDeps keeps _comp_rqrs / _comp_log_dir as the persistence read-back
    # contract (the parent orchestrator reads them via getattr for
    # SearchPublishInput). The standalone compliance phase is retired and
    # FullLoopDeps no longer sources them, so they stay empty/None — services
    # now ride inside _reg_rqrs.
    deps._comp_rqrs = []
    deps._case_rqrs = list(full_deps._case_rqrs or [])
    deps._per_executor_stats = dict(full_deps._per_executor_stats or {})
    deps._reg_log_dir = full_deps._reg_log_dir
    deps._comp_log_dir = None
    deps._case_log_dir = full_deps._case_log_dir
    deps._aggregator_input = full_deps._aggregator_input
    deps._events.extend(full_deps._events)
    deps._agg_output = agg_output
    return agg_output


__all__ = ["FullLoopDeps", "DetailLevel", "run_full_loop", "run_retrieval"]
