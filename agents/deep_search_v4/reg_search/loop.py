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
from datetime import datetime, timezone
from typing import Union

from pydantic_graph import BaseNode, End, Graph, GraphRunContext

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
        "expander": resolve(model_override, "reg_search_expander"),
        "reranker": resolve(None, "reg_search_reranker"),
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
    RegSearchDeps,
    RegSearchResult,
    RerankerQueryResult,
    SearchResult,
)
from .search import search_regulations_pipeline

logger = logging.getLogger(__name__)


# -- ExpanderNode --------------------------------------------------------------


class ExpanderNode(BaseNode[LoopState, RegSearchDeps, RegSearchResult]):
    """Runs QueryExpander agent with structured output.

    Creates 2-7 search queries from the focus_instruction (LLM decides count based on complexity).
    On round 2+, injects weak_axes as dynamic instructions.
    Always transitions to SearchNode.
    """

    async def run(
        self,
        ctx: GraphRunContext[LoopState, RegSearchDeps],
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

        # Create expander agent with selected prompt variant and thinking effort
        expander = create_expander_agent(
            prompt_key=state.expander_prompt_key,
            thinking_effort=state.thinking_effort,
            model_override=state.model_override,
        )

        # Build base user message
        user_message = build_expander_user_message(
            state.focus_instruction,
            state.user_context,
        )

        # Always build dynamic instructions — picks up planner caps and
        # weak-axes guidance when in round 2+. Sectors are applied at search
        # time directly from state.sectors_override (the LLM is no longer
        # told about them).
        weak_axes = state.weak_axes if state.round_count > 1 else []
        dynamic_instructions = build_expander_dynamic_instructions(
            weak_axes,
            state.round_count,
            max_queries=state.expander_max_queries,
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


class SearchNode(BaseNode[LoopState, RegSearchDeps, RegSearchResult]):
    """Programmatic search -- no LLM. Runs queries via asyncio.gather.

    Reads state.expander_output.queries, executes them concurrently
    via search_regulations_pipeline, appends to state.all_search_results.
    Always transitions to RerankerNode.
    """

    async def run(
        self,
        ctx: GraphRunContext[LoopState, RegSearchDeps],
    ) -> RerankerNode:
        import time as _time
        _t0 = _time.perf_counter()

        state = ctx.state
        deps = ctx.deps

        queries = state.expander_output.queries if state.expander_output else []
        if not queries:
            logger.warning("SearchNode: no queries to execute")
            return RerankerNode()

        # Sector filter: planner is the only source. The LLM no longer picks
        # sectors. ``state.sectors_override`` is applied verbatim (planner
        # already canonicalized to the regulations vocab).
        filter_sectors: list[str] | None = (
            list(state.sectors_override) if state.sectors_override else None
        )

        if filter_sectors:
            logger.info(
                "SearchNode: sector filter active -- %s",
                ", ".join(filter_sectors),
            )
            state.sse_events.append({
                "type": "status",
                "text": f"تصفية حسب القطاعات: {' | '.join(filter_sectors)}",
            })

        logger.info("SearchNode: executing %d queries (concurrency=%d)", len(queries), state.concurrency)
        state.sse_events.append({
            "type": "status",
            "text": f"جاري تنفيذ {len(queries)} استعلامات بحث...",
        })

        # Batch-embed all queries in one API call
        from agents.utils.embeddings import embed_regulation_queries_alibaba

        embeddings = await embed_regulation_queries_alibaba(queries)

        # Execute queries with concurrency limit and pre-computed embeddings
        sem = asyncio.Semaphore(state.concurrency)
        tasks = [
            search_regulations_pipeline(
                query=q, deps=deps, filter_sectors=filter_sectors,
                unfold_mode=state.unfold_mode,
                precomputed_embedding=emb,
                semaphore=sem,
            )
            for q, emb in zip(queries, embeddings)
        ]

        results_raw: list[tuple[str, int]] = await asyncio.gather(*tasks)

        # Build rationale lookup from expander output
        rationales = (
            state.expander_output.rationales
            if state.expander_output and state.expander_output.rationales
            else []
        )

        # Create SearchResult for each and append to state
        for qi, (query, (raw_markdown, result_count)) in enumerate(
            zip(queries, results_raw), 1
        ):
            rationale = rationales[qi - 1] if qi <= len(rationales) else ""

            search_result = SearchResult(
                query=query,
                raw_markdown=raw_markdown,
                result_count=result_count,
            )
            state.all_search_results.append(search_result)

            # Log for debugging
            state.search_results_log.append({
                "round": state.round_count,
                "query": query,
                "rationale": rationale,
                "result_count": result_count,
                "raw_markdown_length": len(raw_markdown),
                "raw_markdown": raw_markdown,
            })

            # Per-query markdown log
            if deps._log_id:
                save_search_query_md(
                    log_id=deps._log_id,
                    round_num=state.round_count,
                    query_index=qi,
                    query=query,
                    raw_markdown=raw_markdown,
                    result_count=result_count,
                    rationale=rationale,
                )

        total_count = sum(rc for _, rc in results_raw)
        logger.info(
            "SearchNode: %d queries returned %d total results",
            len(queries),
            total_count,
        )
        state.sse_events.append({
            "type": "status",
            "text": f"تم استلام {total_count} نتيجة -- جاري التقييم والتحليل...",
        })

        state.step_timings.setdefault("search", 0.0)
        state.step_timings["search"] += _time.perf_counter() - _t0
        return RerankerNode()


# -- RerankerNode --------------------------------------------------------------


class RerankerNode(BaseNode[LoopState, RegSearchDeps, RegSearchResult]):
    """Runs classification-only reranker per sub-query in parallel (v2).

    Launches all sub-queries concurrently via asyncio.gather, then
    collects results. Each run_reranker_for_query handles its own
    multi-round classify→unfold→reclassify loop.
    Always terminates at End with a placeholder RegSearchResult — the
    reranker_results on LoopState are what downstream URA consumers care about.
    """

    async def run(
        self,
        ctx: GraphRunContext[LoopState, RegSearchDeps],
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

        logger.info(
            "RerankerNode: launching %d parallel reranker tasks",
            len(current_round_results),
        )

        # Build tasks for parallel execution
        async def _process_one(qi: int, sr_log: dict) -> None:
            query = sr_log["query"]
            raw_markdown = sr_log.get("raw_markdown", "")
            rationale = rationales[qi] if qi < len(rationales) else ""
            result_count = sr_log.get("result_count", 0)

            if result_count == 0 or not raw_markdown.strip():
                state.reranker_results.append(RerankerQueryResult(
                    query=query,
                    rationale=rationale,
                    sufficient=False,
                    results=[],
                    dropped_count=0,
                    summary_note="لا توجد نتائج بحث لهذا الاستعلام",
                ))
                return

            try:
                round_trace: list[dict] = []
                query_result, usage_entries, decision_log = await run_reranker_for_query(
                    query=query,
                    rationale=rationale,
                    raw_markdown=raw_markdown,
                    supabase=deps.supabase,
                    max_high=state.reranker_max_high,
                    max_medium=state.reranker_max_medium,
                    model_override=state.model_override,
                    round_trace=round_trace,
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

                state.reranker_results.append(query_result)

                logger.info(
                    "RerankerNode q%d: %d results kept, %d dropped, sufficient=%s, "
                    "%d unfold rounds, %d unfolds",
                    qi + 1, len(query_result.results), query_result.dropped_count,
                    query_result.sufficient, query_result.unfold_rounds,
                    query_result.total_unfolds,
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


reg_search_graph = Graph(
    nodes=[ExpanderNode, SearchNode, RerankerNode],
)


async def run_reg_search(
    focus_instruction: str,
    user_context: str,
    deps: RegSearchDeps,
    expander_prompt_key: str = "prompt_1",
    aggregator_prompt_key: str = "prompt_1",
    thinking_effort: str | None = None,
    model_override: str | None = None,
    unfold_mode: str = "precise",
    concurrency: int = 10,
    skip_reranker: bool = False,
    skip_aggregator: bool = False,
    sectors_override: list[str] | None = None,
) -> RegSearchResult:
    """Run the complete reg_search loop for a focus instruction.

    Creates fresh LoopState, runs the graph from ExpanderNode,
    and returns the RegSearchResult. SSE events collected during
    the loop are transferred to deps._events.

    Args:
        focus_instruction: Arabic instruction -- what to search for.
        user_context: Arabic context -- user's situation/question.
        deps: RegSearchDeps with supabase, embedding_fn, etc.
        expander_prompt_key: Which expander prompt variant to use.
        aggregator_prompt_key: Which aggregator prompt variant to use.
        thinking_effort: Reasoning effort for expander — "low"/"medium"/"high"/"none"/None.
        model_override: Registry key to override both expander and aggregator model.
        unfold_mode: "precise" (compact) or "detailed" (full content).
        concurrency: Max concurrent search pipelines (default 3).

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
        thinking_effort=thinking_effort,
        model_override=model_override,
        unfold_mode=unfold_mode,
        concurrency=concurrency,
        skip_reranker=skip_reranker,
        skip_aggregator=skip_aggregator,
        sectors_override=list(sectors_override) if sectors_override else None,
    )

    t0 = time.perf_counter()
    error_msg: str | None = None

    try:
        graph_result = await reg_search_graph.run(
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
        thinking_effort=thinking_effort,
        step_timings=dict(state.step_timings),
    )

    return output


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
