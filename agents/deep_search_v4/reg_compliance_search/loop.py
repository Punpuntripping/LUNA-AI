"""Graph nodes and entry point for the reg_search loop.

Three nodes forming the expand-search-rerank pipeline:
- ExpanderNode: LLM query expansion (QueryExpander agent)
- SearchNode: Programmatic search execution (no LLM)
- RerankerNode: Per-sub-query classification-only reranker

Final synthesis is done by the unified aggregator at the URA pipeline layer
(see agents/deep_search_v3/aggregator/). This loop terminates at End() with a
placeholder RegSearchResult; the reranker_results on LoopState carry the
structured output consumed by the URA merger.
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone
from typing import Union

from pydantic_graph import BaseNode, End, Graph, GraphRunContext

from agents.deep_search_v4.shared import DEFAULT_SEARCH_CONCURRENCY
from agents.deep_search_v4.shared.context import ContextBlock
from agents.utils.tracking import track_stage

# Divisor floor for the dynamic result-budget model (MODE_PROFILES.md §1).
# When the planner passes a ``result_budget``, the per-sub-query reranker keep
# is ceil(result_budget / max(N, MIN_EXPANDER_DIVISOR)) where N is the
# expander's actual emitted query count. The floor stops a degenerate 1-2
# query expansion from piling the whole budget onto one sub-query.
MIN_EXPANDER_DIVISOR = 3

from .expander import EXPANDER_LIMITS, create_expander_agent, get_expander_model_id as _get_expander_model_id


def _resolve_models(model_override: str | None) -> dict[str, str]:
    """Expander honors --model override; reranker always uses its default."""
    from agents.model_registry import MODEL_REGISTRY
    from agents.utils.agent_models import AGENT_MODELS

    def resolve(override: str | None, default_key: str) -> str:
        key = override or AGENT_MODELS.get(default_key, "")
        config = MODEL_REGISTRY.get(key)
        return config.model_id if config else key

    return {
        "expander": resolve(model_override, "reg_compliance_expander"),
        "reranker": resolve(None, "reg_compliance_reranker"),
    }
from .prompts import (
    build_expander_dynamic_instructions,
    build_expander_user_message,
    get_expander_prompt,
)
from .logger import (
    save_expander_md,
    save_reranker_md,
    save_search_query_md,
)
from .models import (
    ExpanderOutput,
    LoopState,
    RegComplianceSearchDeps,
    RegSearchResult,
    RerankerQueryResult,
    SearchOutcome,
    SearchResult,
)
from .search import search_regulations_pipeline

logger = logging.getLogger(__name__)


def _stamp_span(span, *, outcome: str | None = None, **attrs) -> None:
    """Stamp attrs (and optionally an outcome) onto a span, swallowing all of it.

    Two reasons this is not just ``span.set(...)``: telemetry must never be the
    reason retrieval raises, and the ``LUNA_TRACK_DISABLE`` no-op handle does
    not implement ``set_outcome`` at all (see ``agents/utils/tracking.py``).
    Same pattern as ``agents/memory/convo_compactor/runner.py::_set_span``.
    """
    if outcome is not None:
        try:
            span.set_outcome(outcome)
        except Exception:  # noqa: BLE001 - telemetry is best-effort
            pass
    try:
        span.set(**attrs)
    except Exception:  # noqa: BLE001 - telemetry is best-effort
        pass


# -- ExpanderNode --------------------------------------------------------------


class ExpanderNode(BaseNode[LoopState, RegComplianceSearchDeps, RegSearchResult]):
    """Runs QueryExpander agent with structured output.

    Creates 2-7 search queries from the focus_instruction (LLM decides count based on complexity).
    On round 2+, injects weak_axes as dynamic instructions.
    Always transitions to SearchNode.
    """

    async def run(
        self,
        ctx: GraphRunContext[LoopState, RegComplianceSearchDeps],
    ) -> SearchNode:
        import time as _time
        _t0 = _time.perf_counter()

        state = ctx.state
        state.round_count += 1

        logger.info(
            "ExpanderNode round %d -- focus: %s",
            state.round_count,
            state.focus_instruction[:80],
        )

        # Create expander agent with selected prompt variant. Reasoning effort
        # is fixed at medium by the slot policy (see expander.py).
        expander = create_expander_agent(
            prompt_key=state.expander_prompt_key,
            model_override=state.model_override,
        )

        # Build base user message — pass planner-curated context bundle so the
        # expander sees the <context_blocks> XML when non-empty.
        user_message = build_expander_user_message(
            state.focus_instruction,
            state.user_context,
            context_blocks=state.context_blocks,
        )

        # Always build dynamic instructions — weak-axes guidance when in
        # round 2+. Sectors are applied at search time directly from
        # state.sectors_override (the LLM is no longer told about them). The
        # sub-query count is no longer capped — the expander decides it.
        weak_axes = state.weak_axes if state.round_count > 1 else []
        dynamic_instructions = build_expander_dynamic_instructions(
            weak_axes,
            state.round_count,
        )
        if dynamic_instructions:
            user_message = f"{user_message}\n\n{dynamic_instructions}"

        try:
            result = await expander.run(
                user_message,
                usage_limits=EXPANDER_LIMITS,
            )
            output: ExpanderOutput = result.output

            # Capture usage
            eu = result.usage()
            usage_entry = {
                "agent": "expander",
                "round": state.round_count,
                "requests": eu.requests,
                "input_tokens": eu.input_tokens,
                "output_tokens": eu.output_tokens,
                "total_tokens": eu.total_tokens,
                "cached_tokens": int(getattr(eu, "cache_read_tokens", 0) or 0),
            }
            if eu.details:
                usage_entry["details"] = dict(eu.details)
            state.inner_usage.append(usage_entry)

            # Store in state
            state.expander_output = output
            state.all_queries_used.extend(output.queries)

            # SSE status
            state.sse_events.append({
                "type": "status",
                "text": (
                    f"تم توليد {len(output.queries)} استعلامات بحث "
                    f"(الجولة {state.round_count})"
                ),
            })

            logger.info(
                "ExpanderNode: %d queries -- %s",
                len(output.queries),
                ", ".join(q[:40] for q in output.queries),
            )

            # Per-round markdown log
            if ctx.deps._log_id:
                save_expander_md(
                    log_id=ctx.deps._log_id,
                    round_num=state.round_count,
                    prompt_key=state.expander_prompt_key,
                    system_prompt=get_expander_prompt(state.expander_prompt_key),
                    user_message=user_message,
                    output=output,
                    usage=result.usage(),
                    messages_json=result.all_messages_json(),
                )

        except Exception as e:
            logger.error("ExpanderNode error: %s", e, exc_info=True)
            state.sse_events.append({
                "type": "status",
                "text": "حدث خطأ أثناء توسيع الاستعلامات.",
            })
            # Fallback: use focus_instruction as a single query
            state.expander_output = ExpanderOutput(
                queries=[state.focus_instruction],
                rationales=["Fallback: expander failed"],
            )
            state.all_queries_used.append(state.focus_instruction)

        state.step_timings.setdefault("expander", 0.0)
        state.step_timings["expander"] += _time.perf_counter() - _t0
        return SearchNode()


# -- SearchNode ----------------------------------------------------------------


class SearchNode(BaseNode[LoopState, RegComplianceSearchDeps, RegSearchResult]):
    """Programmatic search -- no LLM. Runs queries via asyncio.gather.

    Reads state.expander_output.queries, executes them concurrently
    via search_regulations_pipeline, appends to state.all_search_results.
    Always transitions to RerankerNode.
    """

    async def run(
        self,
        ctx: GraphRunContext[LoopState, RegComplianceSearchDeps],
    ) -> RerankerNode:
        import time as _time
        _t0 = _time.perf_counter()

        state = ctx.state
        deps = ctx.deps

        queries = state.expander_output.queries if state.expander_output else []
        if not queries:
            logger.warning("SearchNode: no queries to execute")
            return RerankerNode()

        # Sector filter: either the parallel ``sector_picker`` future (planner
        # path) or the static ``sectors_override`` (CLI / smoke paths). The
        # future is awaited inside the per-query pipeline at step 6 so it
        # overlaps the embed + RPC + fetch chain. The static path passes
        # ``filter_sectors`` directly.
        if state.sectors_future is not None:
            logger.info(
                "SearchNode: sector filter source=picker (future, pending)",
            )
        elif state.sectors_override:
            logger.info(
                "SearchNode: sector filter source=override -- %s",
                ", ".join(state.sectors_override),
            )
            state.sse_events.append({
                "type": "status",
                "text": f"تصفية حسب القطاعات: {' | '.join(state.sectors_override)}",
            })
        static_filter_sectors: list[str] | None = (
            list(state.sectors_override) if state.sectors_override else None
        )

        logger.info("SearchNode: executing %d queries (concurrency=%d)", len(queries), state.concurrency)
        state.sse_events.append({
            "type": "status",
            "text": f"جاري تنفيذ {len(queries)} استعلامات بحث...",
        })

        # Own span for the retrieval stage (2026-08-22). Until now the only
        # span covering search was the orchestrator's
        # ``deep_search.phase.reg_compliance``, which reports ``rqr_count`` and
        # a flat ``outcome: "ok"`` — and on the incident turn it said exactly
        # that while all ten search_topics RPCs were dead. A phase that
        # produced zero rows and a phase whose sockets all timed out were
        # indistinguishable in telemetry. This span carries the retrieval
        # health signals themselves, and goes ``degraded`` the moment any
        # sub-query fails. No conversation_id on ``RegComplianceSearchDeps`` —
        # it rides on the parent phase span, which this one nests under.
        with track_stage(
            "deep_search.reg_compliance.search",
            agent_family="deep_search",
            subtype="search",
            round=state.round_count,
            total_queries=len(queries),
            concurrency=state.concurrency,
        ) as _search_span:
            # Batch-embed all queries in one API call
            from agents.utils.embeddings import embed_regulation_queries_alibaba

            embeddings = await embed_regulation_queries_alibaba(queries)

            # Execute queries with concurrency limit and pre-computed embeddings.
            # When the picker future is set, every parallel pipeline awaits the
            # same future at its step-6 join point — they all read the resolved
            # value once it lands.
            #
            # This semaphore is NOT the database ceiling and never was: it
            # bounds pipeline-level work for this executor only, while
            # case_search fans out through its own on the same Postgres
            # instance. The actual RPC now queues on the process-wide
            # ``search_gate`` (agents/deep_search_v4/shared/db_gate.py); this
            # stays because embed + merge + the three content fetches are real
            # work worth capping per executor.
            sem = asyncio.Semaphore(state.concurrency)
            tasks = [
                search_regulations_pipeline(
                    query=q, deps=deps,
                    filter_sectors=static_filter_sectors,
                    filter_sectors_future=state.sectors_future,
                    precomputed_embedding=emb,
                    semaphore=sem,
                )
                for q, emb in zip(queries, embeddings)
            ]

            # One SearchOutcome per sub-query. ``gather`` stays without
            # ``return_exceptions=True`` on purpose: the pipeline is
            # contractually non-raising (it converts its own failures into
            # ``outcome.error``), so there is nothing here for gather to
            # cancel the fan-out over.
            results_raw: list[SearchOutcome] = await asyncio.gather(*tasks)

            # Build rationale lookup from expander output
            rationales = (
                state.expander_output.rationales
                if state.expander_output and state.expander_output.rationales
                else []
            )

            # Create SearchResult for each and append to state
            for qi, (query, outcome) in enumerate(zip(queries, results_raw), 1):
                rationale = rationales[qi - 1] if qi <= len(rationales) else ""
                chunks = outcome.rows
                result_count = outcome.count

                search_result = SearchResult(
                    query=query,
                    chunks=chunks,
                    result_count=result_count,
                    max_score=outcome.max_score,
                    top_scores=list(outcome.top_scores),
                    error=outcome.error,
                )
                state.all_search_results.append(search_result)

                if outcome.error:
                    # Loud, per-sub-query, at ERROR. The pipeline already
                    # logged the traceback; this is the line that says which
                    # sub-query of which round lost its retrieval.
                    logger.error(
                        "SearchNode q%d: retrieval FAILED (%s) — '%s'",
                        qi, outcome.error, query[:60],
                    )

                # Log for debugging — chunk rows replace raw_markdown.
                # ``max_score`` / ``error`` ride along so RerankerNode (which
                # reads this log, not ``all_search_results``) can carry the
                # score across the rerank boundary.
                state.search_results_log.append({
                    "round": state.round_count,
                    "query": query,
                    "rationale": rationale,
                    "chunks": chunks,
                    "result_count": result_count,
                    "max_score": outcome.max_score,
                    "top_scores": list(outcome.top_scores),
                    "error": outcome.error,
                })

                # Per-query markdown log — render a compact summary of the chunk
                # rows since save_search_query_md still expects a markdown body.
                if deps._log_id:
                    save_search_query_md(
                        log_id=deps._log_id,
                        round_num=state.round_count,
                        query_index=qi,
                        query=query,
                        raw_markdown=_render_chunks_summary(chunks),
                        result_count=result_count,
                        rationale=rationale,
                    )

            total_count = sum(o.count for o in results_raw)
            failed = [o for o in results_raw if o.error]
            scores = [round(o.max_score, 4) for o in results_raw]
            # ``gate_wait_ms`` is reported as the MAX across sub-queries, not
            # the sum: the sub-queries queue concurrently, so a sum would count
            # overlapping waits several times over and read as minutes of
            # latency that never elapsed. The max answers the question actually
            # being asked — "how long did the worst-queued sub-query sit before
            # the database would take it".
            gate_wait_ms = max((o.gate_wait_ms for o in results_raw), default=0.0)
            max_score = max((o.max_score for o in results_raw), default=0.0)

            logger.info(
                "SearchNode: %d queries returned %d total results "
                "(max_score=%.3f, %d failed, gate_wait_max=%.0fms)",
                len(queries), total_count, max_score, len(failed), gate_wait_ms,
            )

            _stamp_span(
                _search_span,
                # "degraded" is the whole point: a phase that lost sub-queries
                # to the transport must never again report itself as "ok".
                outcome="degraded" if failed else None,
                sources=total_count,
                max_score=round(max_score, 4),
                scores=scores,
                failed_queries=len(failed),
                total_queries=len(queries),
                failed_errors=sorted({o.error for o in failed if o.error}),
                gate_wait_ms=round(gate_wait_ms, 1),
            )

            state.sse_events.append({
                "type": "status",
                "text": f"تم استلام {total_count} نتيجة -- جاري التقييم والتحليل...",
            })

        state.step_timings.setdefault("search", 0.0)
        state.step_timings["search"] += _time.perf_counter() - _t0
        return RerankerNode()


# -- RerankerNode --------------------------------------------------------------


class RerankerNode(BaseNode[LoopState, RegComplianceSearchDeps, RegSearchResult]):
    """Runs classification-only reranker per sub-query in parallel (v2).

    Launches all sub-queries concurrently via asyncio.gather, then
    collects results. Each run_reranker_for_query runs a single keep-only
    classification pass and derives the drop set by set-difference.
    Always terminates at End with a placeholder RegSearchResult — the
    reranker_results on LoopState are what downstream URA consumers care about.
    """

    async def run(
        self,
        ctx: GraphRunContext[LoopState, RegComplianceSearchDeps],
    ) -> End[RegSearchResult]:
        import time as _time
        _t0 = _time.perf_counter()

        state = ctx.state
        deps = ctx.deps

        # Skip reranker if disabled
        if state.skip_reranker:
            logger.info("RerankerNode: skipped (--skip-reranker)")
            return _end_placeholder(state)

        # Only process results from the current round
        current_round_results = [
            sr for sr in state.search_results_log
            if sr.get("round") == state.round_count
        ]

        if not current_round_results:
            logger.warning("RerankerNode: no search results for round %d", state.round_count)
            return _end_placeholder(state)

        # Dynamic result-budget model (MODE_PROFILES.md §1). When the planner
        # passes a ``result_budget``, derive the per-sub-query reranker keep
        # from the expander's ACTUAL emitted query count N. When it is None
        # (CLI / monitor path), fall back to the fixed ``reranker_max_keep``.
        reranker_max_keep = state.reranker_max_keep
        if state.result_budget is not None:
            n_queries = len(current_round_results)
            reranker_max_keep = math.ceil(
                state.result_budget / max(n_queries, MIN_EXPANDER_DIVISOR)
            )
            logger.info(
                "RerankerNode: dynamic keep — result_budget=%d, N=%d -> max_keep=%d",
                state.result_budget, n_queries, reranker_max_keep,
            )

        from .reranker import run_reranker_for_query

        # Get rationales from expander output
        rationales = (
            state.expander_output.rationales
            if state.expander_output and state.expander_output.rationales
            else []
        )

        state.sse_events.append({
            "type": "status",
            "text": f"جاري إعادة ترتيب وتصفية النتائج ({len(current_round_results)} استعلام بالتوازي)...",
        })

        # Live progress: the rerankers are the real "تقييم وترجيح" stage. The
        # `status` line above is batched + sanitized away (it leaks parallelism);
        # this carries the stage transition instead. Guarded — a broken progress
        # sink must never perturb retrieval. Peer executors emit this too; the
        # client keeps the stage monotonic, so a duplicate is a no-op.
        if deps.emit_sse is not None:
            try:
                deps.emit_sse({
                    "type": "agent_progress",
                    "stage": "evaluating",
                    "text": "تقييم النتائج وترجيحها",
                    "data": {},
                })
            except Exception:  # pragma: no cover - defensive
                logger.debug("RerankerNode: emit_sse failed", exc_info=True)

        # Wave 3 (plan §7 / decision D6): the reranker now receives the
        # planner's distilled brief. ONLY the ``planner_brief`` label — never
        # ``case_brief`` (raw case memory) or ``prior_search_lessons``; the
        # reranker grades one sub-query against candidates and must not be
        # handed the whole context bundle.
        #
        # Hoisted OUT of ``_process_one`` deliberately: every one of the N
        # concurrent reranker calls in this round carries the identical brief,
        # and it is rendered at the HEAD of the user message so the shared
        # prefix stays cacheable (see build_reranker_user_message). Resolving it
        # per task would be pure repeat work.
        planner_brief = next(
            (
                b.body
                for b in (state.context_blocks or [])
                if b.label == "planner_brief" and (b.body or "").strip()
            ),
            None,
        )

        logger.info(
            "RerankerNode: launching %d parallel reranker tasks (planner_brief=%s)",
            len(current_round_results),
            planner_brief is not None,
        )

        # Build tasks for parallel execution
        async def _process_one(qi: int, sr_log: dict) -> None:
            query = sr_log["query"]
            chunks = sr_log.get("chunks", []) or []
            rationale = rationales[qi] if qi < len(rationales) else ""
            # Retrieval score head for this sub-query, from the SearchResult
            # this log entry was built from. Carried onto EVERY
            # RerankerQueryResult below — including the empty ones, which are
            # precisely the cases where it is the only evidence left. An empty
            # ``results`` with max_score 0.65 means the reranker dropped strong
            # material; with max_score 0.24 (the random-pair band) it means the
            # corpus genuinely has nothing; with max_score 0.0 AND an error it
            # means retrieval never happened.
            max_score = float(sr_log.get("max_score") or 0.0)

            if not chunks:
                state.reranker_results.append(RerankerQueryResult(
                    query=query,
                    rationale=rationale,
                    sufficient=False,
                    results=[],
                    dropped_count=0,
                    # Left verbatim even when ``sr_log["error"]`` is set:
                    # ``summary_note`` is LLM-visible downstream, and this
                    # change is instrumentation only. The failure is reported
                    # on the span and in ``SearchResult.error``, not by
                    # rewording a prompt.
                    summary_note="لا توجد نتائج بحث لهذا الاستعلام",
                    max_score=max_score,
                ))
                return

            try:
                round_trace: list[dict] = []
                query_result, usage_entries, decision_log = await run_reranker_for_query(
                    query=query,
                    rationale=rationale,
                    chunks=chunks,
                    supabase=deps.supabase,
                    max_keep=reranker_max_keep,
                    model_override=state.model_override,
                    round_trace=round_trace,
                    planner_brief=planner_brief,
                )

                # Capture usage entries
                for ue in usage_entries:
                    ue["round"] = state.round_count
                    ue["query_index"] = qi + 1
                state.inner_usage.extend(usage_entries)

                # Stash usage + decisions + round trace for JSON/MD logging
                query_result._usage_entries = usage_entries  # type: ignore[attr-defined]
                query_result._decision_log = decision_log    # type: ignore[attr-defined]
                query_result._round_trace = round_trace      # type: ignore[attr-defined]

                # The reranker builds the RQR from candidates alone and has no
                # view of the retrieval score, so the carry-over happens here —
                # the one place that holds both halves.
                query_result.max_score = max_score

                state.reranker_results.append(query_result)

                logger.info(
                    "RerankerNode q%d: %d results kept, %d dropped, sufficient=%s, max_score=%.3f",
                    qi + 1, len(query_result.results), query_result.dropped_count,
                    query_result.sufficient, max_score,
                )

                # Log per-query reranker output (md)
                if deps._log_id:
                    save_reranker_md(
                        log_id=deps._log_id,
                        round_num=state.round_count,
                        query_index=qi + 1,
                        query_result=query_result,
                    )

            except Exception as e:
                logger.error("RerankerNode q%d error: %s", qi + 1, e, exc_info=True)
                state.reranker_results.append(RerankerQueryResult(
                    query=query,
                    rationale=rationale,
                    sufficient=False,
                    results=[],
                    dropped_count=0,
                    summary_note=f"خطأ في إعادة الترتيب: {str(e)[:100]}",
                    # Retrieval succeeded here — the RERANKER blew up. Keeping
                    # the score says so: strong candidates existed and were
                    # lost after search, not before it.
                    max_score=max_score,
                ))

        # Run all sub-queries in parallel
        await asyncio.gather(
            *[_process_one(qi, sr_log) for qi, sr_log in enumerate(current_round_results)]
        )

        total_kept = sum(len(rr.results) for rr in state.reranker_results)
        total_dropped = sum(rr.dropped_count for rr in state.reranker_results)

        state.sse_events.append({
            "type": "status",
            "text": f"تم تصفية النتائج: {total_kept} نتيجة محتفظ بها، {total_dropped} محذوفة",
        })

        state.step_timings.setdefault("reranker", 0.0)
        state.step_timings["reranker"] += _time.perf_counter() - _t0

        logger.info(
            "RerankerNode: %d queries processed, %d results kept, %d dropped (%.1fs)",
            len(current_round_results), total_kept, total_dropped,
            state.step_timings["reranker"],
        )

        # Save reranker summary JSON
        if deps._log_id:
            from .logger import save_reranker_json
            save_reranker_json(
                log_id=deps._log_id,
                reranker_results=state.reranker_results,
            )

        return _end_placeholder(state)


def _end_placeholder(state: LoopState) -> End[RegSearchResult]:
    """Terminal placeholder — downstream reads LoopState.reranker_results directly."""
    return End(
        RegSearchResult(
            quality="pending",
            summary_md="reg_search loop complete; synthesis handled by URA aggregator.",
            citations=[],
            domain="regulations",
            queries_used=list(state.all_queries_used),
            rounds_used=state.round_count,
            expander_prompt_key=state.expander_prompt_key,
            aggregator_prompt_key=state.aggregator_prompt_key,
        )
    )


# -- (AggregatorNode removed — see module docstring) ---------------------------




# -- Graph assembly and entry point --------------------------------------------


reg_compliance_graph = Graph(
    nodes=[ExpanderNode, SearchNode, RerankerNode],
)


async def run_reg_search(
    focus_instruction: str,
    user_context: str,
    deps: RegComplianceSearchDeps,
    expander_prompt_key: str = "prompt_1",
    aggregator_prompt_key: str = "prompt_1",
    model_override: str | None = None,
    unfold_mode: str = "precise",
    concurrency: int = DEFAULT_SEARCH_CONCURRENCY,
    skip_reranker: bool = False,
    skip_aggregator: bool = False,
    sectors_override: list[str] | None = None,
    result_budget: int | None = None,
    context_blocks: list[ContextBlock] | None = None,
) -> RegSearchResult:
    """Run the complete reg_search loop for a focus instruction.

    Creates fresh LoopState, runs the graph from ExpanderNode,
    and returns the RegSearchResult. SSE events collected during
    the loop are transferred to deps._events.

    Args:
        focus_instruction: Arabic instruction -- what to search for.
        user_context: Arabic context -- user's situation/question.
        deps: RegComplianceSearchDeps with supabase, embedding_fn, etc.
        expander_prompt_key: Which expander prompt variant to use.
        aggregator_prompt_key: Which aggregator prompt variant to use.
        model_override: Registry key to override both expander and aggregator model.
        unfold_mode: "precise" (compact) or "detailed" (full content).
        concurrency: Max concurrent search pipelines. Defaults to
            ``DEFAULT_SEARCH_CONCURRENCY`` (10) — the docstring said 3 until
            2026-08-22, which is how the fan-out width stayed unexamined. Note
            this bounds PIPELINE work only; the ``search_topics`` RPC itself
            queues on the process-wide gate in
            ``agents/deep_search_v4/shared/db_gate.py``.
        result_budget: Optional target total results (dynamic-budget model,
            MODE_PROFILES.md §1). When set, the per-sub-query reranker keep is
            derived at runtime from the expander's actual query count. When
            None, the fixed ``reranker_max_keep`` on LoopState is used.

    Returns:
        RegSearchResult with quality, summary_md, citations, metadata.
    """
    from .logger import create_run_dir, make_log_id, save_run_json, save_run_overview_md

    logger.info(
        "run_reg_search: focus='%s', expander_prompt=%s, aggregator_prompt=%s",
        focus_instruction[:80],
        expander_prompt_key,
        aggregator_prompt_key,
    )

    # Create log directory: logs/query_{id}/{timestamp}/
    import time

    log_id = make_log_id(deps._query_id) if deps._query_id else datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    deps._log_id = log_id
    create_run_dir(log_id)

    state = LoopState(
        focus_instruction=focus_instruction,
        user_context=user_context,
        expander_prompt_key=expander_prompt_key,
        aggregator_prompt_key=aggregator_prompt_key,
        model_override=model_override,
        unfold_mode=unfold_mode,
        concurrency=concurrency,
        skip_reranker=skip_reranker,
        skip_aggregator=skip_aggregator,
        sectors_override=list(sectors_override) if sectors_override else None,
        result_budget=result_budget,
        context_blocks=list(context_blocks) if context_blocks else [],
    )

    t0 = time.perf_counter()
    error_msg: str | None = None

    try:
        graph_result = await reg_compliance_graph.run(
            ExpanderNode(),
            state=state,
            deps=deps,
        )

        # Transfer SSE events from loop state to deps
        deps._events.extend(state.sse_events)

        output = graph_result.output

        logger.info(
            "run_reg_search complete: quality=%s, rounds=%d, citations=%d, queries=%d",
            output.quality,
            output.rounds_used,
            len(output.citations),
            len(output.queries_used),
        )

    except Exception as e:
        logger.error("run_reg_search failed: %s", e, exc_info=True)
        error_msg = str(e)
        deps._events.extend(state.sse_events)
        deps._events.append({
            "type": "status",
            "text": "حدث خطأ أثناء حلقة البحث في الأنظمة.",
        })

        output = RegSearchResult(
            quality="weak",
            summary_md="حدث خطأ أثناء البحث في الأنظمة.",
            citations=[],
            domain="regulations",
            queries_used=list(state.all_queries_used),
            rounds_used=state.round_count,
            expander_prompt_key=expander_prompt_key,
            aggregator_prompt_key=aggregator_prompt_key,
        )

    duration = time.perf_counter() - t0

    # Build round summaries from state
    round_summaries = _build_round_summaries(state)

    # Save overview + JSON
    save_run_overview_md(
        log_id=log_id,
        focus_instruction=focus_instruction,
        user_context=user_context,
        expander_prompt_key=expander_prompt_key,
        aggregator_prompt_key=aggregator_prompt_key,
        duration_s=duration,
        result=output,
        round_summaries=round_summaries,
    )
    save_run_json(
        log_id=log_id,
        focus_instruction=focus_instruction,
        user_context=user_context,
        expander_prompt_key=expander_prompt_key,
        aggregator_prompt_key=aggregator_prompt_key,
        duration_s=duration,
        result=output,
        events=list(deps._events),
        round_summaries=round_summaries,
        search_results_log=list(state.search_results_log),
        inner_usage=list(state.inner_usage),
        error=error_msg,
        query_id=deps._query_id,
        models=_resolve_models(state.model_override),
        step_timings=dict(state.step_timings),
    )

    return output


def _render_chunks_summary(chunks: list[dict]) -> str:
    """Render search-result rows into a compact markdown summary.

    search.py returns mixed-type content rows (chunk / circular / service). The
    per-query markdown log (``save_search_query_md``) still wants a text body, so
    we render a lightweight, source-type-aware digest of each row here.
    """
    if not chunks:
        return "_(لا توجد نتائج)_"
    parts: list[str] = [f"## نتائج البحث — {len(chunks)} نتيجة", ""]
    for i, ch in enumerate(chunks, 1):
        source_type = ch.get("source_type", "regulation")
        title = (
            ch.get("title")
            or ch.get("service_name_ar")
            or ch.get("intro_title")
            or "(بدون عنوان)"
        )
        mode = ch.get("_mode", "simple")
        rrf = ch.get("_rrf", 0.0)
        ref = ch.get("chunk_ref") or ch.get("circ_ref") or ch.get("service_ref") or ""
        parts.append(f"### {i}. [{source_type}] {title}")
        parts.append(f"- ref: `{ref}`")
        parts.append(f"- id: `{ch.get('id', '')}`")
        parts.append(f"- mode: `{mode}` | rrf: {float(rrf or 0.0):.4f}")
        body = ch.get("summary") or ch.get("service_context") or ch.get("content") or ""
        if body:
            parts.append(f"\n> {body[:500]}")
        parts.append("")
    return "\n".join(parts)


def _build_round_summaries(state: LoopState) -> list[dict]:
    """Build per-round summary dicts from state for logging."""
    summaries: list[dict] = []
    # Group search results by round
    search_by_round: dict[int, list] = {}
    for sr in state.search_results_log:
        rn = sr.get("round", 0)
        search_by_round.setdefault(rn, []).append(sr)

    for rn in range(1, state.round_count + 1):
        summary: dict = {"round": rn}

        exp_usage = [u for u in state.inner_usage if u.get("agent") == "expander" and u.get("round") == rn]

        round_searches = search_by_round.get(rn, [])
        if round_searches:
            summary["expander_queries"] = [s["query"] for s in round_searches]
            summary["search_queries"] = len(round_searches)
            summary["search_total"] = sum(s.get("result_count", 0) for s in round_searches)

        if exp_usage:
            summary["expander_usage"] = exp_usage[0]

        summaries.append(summary)

    return summaries
