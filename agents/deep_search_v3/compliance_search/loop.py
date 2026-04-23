"""Graph nodes and entry point for the compliance_search loop.

Three nodes forming the expand-search-rerank loop:
- ExpanderNode: LLM query expansion (QueryExpander agent)
- SearchNode: Programmatic search execution, dedup by service_ref (no LLM)
- RerankerNode: LLM result classification — keep/drop, sufficiency gate, retry control

Retry loop: when RerankerNode returns sufficient=False and retries remain,
weak_axes are fed back to ExpanderNode as dynamic instructions.
Max 3 rounds (1 initial + 2 retries).
"""
from __future__ import annotations

import asyncio
import logging
import time as _time
from datetime import datetime, timezone
from typing import Union

from pydantic_graph import BaseNode, End, Graph, GraphRunContext

from .expander import EXPANDER_LIMITS, create_expander_agent
from .logger import (
    save_expander_md,
    save_reranker_md,
    save_search_query_md,
)
from .models import (
    ComplianceSearchDeps,
    ComplianceSearchResult,
    ExpanderOutput,
    LoopState,
    ServiceRerankerOutput,
)
from .prompts import EXPANDER_SYSTEM_PROMPT, build_expander_dynamic_instructions
from .reranker import RERANKER_LIMITS, create_reranker_agent
from .prompts import build_reranker_user_message
from .search import search_compliance_raw

logger = logging.getLogger(__name__)

MAX_ROUNDS = 3


# -- ExpanderNode --------------------------------------------------------------


class ExpanderNode(BaseNode[LoopState, ComplianceSearchDeps, ComplianceSearchResult]):
    """Runs QueryExpander agent with structured output.

    Identifies distinct compliance tasks in the focus_instruction and generates
    one query per independent need (1–5 queries). On round 2+, injects weak_axes
    as dynamic instructions targeting only the identified gaps.
    Always transitions to SearchNode.
    """

    async def run(
        self,
        ctx: GraphRunContext[LoopState, ComplianceSearchDeps],
    ) -> SearchNode:
        state = ctx.state
        state.round_count += 1

        logger.info(
            "ExpanderNode round %d -- focus: %s",
            state.round_count,
            state.focus_instruction[:80],
        )

        # Create expander agent, with weak_axes on round 2+
        weak_axes = state.weak_axes if state.round_count > 1 else None
        expander = create_expander_agent(weak_axes=weak_axes)

        # Build user message from focus_instruction + user_context
        user_message = state.focus_instruction
        if state.user_context:
            user_message += f"\n\nسياق المستخدم:\n{state.user_context}"

        # On round 2+, append dynamic instructions for weak axes
        if state.round_count > 1 and state.weak_axes:
            dynamic_instructions = build_expander_dynamic_instructions(state.weak_axes)
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
            usage_dict = {
                "agent": "expander",
                "round": state.round_count,
                "requests": eu.requests,
                "input_tokens": eu.input_tokens,
                "output_tokens": eu.output_tokens,
                "total_tokens": eu.total_tokens,
            }
            state.inner_usage.append(usage_dict)

            # Store in state
            state.expander_output = output
            state.queries_used.extend(output.queries)

            task_count = getattr(output, "task_count", len(output.queries))
            state.sse_events.append({
                "type": "status",
                "text": (
                    f"تم توليد {len(output.queries)} استعلامات بحث "
                    f"({task_count} احتياج تنفيذي — الجولة {state.round_count})"
                ),
            })

            logger.info(
                "ExpanderNode: %d queries, task_count=%d -- %s",
                len(output.queries),
                task_count,
                ", ".join(q[:40] for q in output.queries),
            )

            # Log to file
            if state.log_id:
                try:
                    save_expander_md(
                        log_id=state.log_id,
                        round_num=state.round_count,
                        system_prompt=EXPANDER_SYSTEM_PROMPT,
                        user_message=user_message,
                        output=output,
                        usage=usage_dict,
                        messages_json=result.all_messages_json(),
                    )
                except Exception as le:
                    logger.warning("save_expander_md failed: %s", le)

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
                task_count=1,
            )
            state.queries_used.append(state.focus_instruction)

        return SearchNode()


# -- SearchNode ----------------------------------------------------------------


class SearchNode(BaseNode[LoopState, ComplianceSearchDeps, ComplianceSearchResult]):
    """Programmatic search -- no LLM. Runs queries via asyncio.gather.

    Reads state.expander_output.queries, executes them concurrently via
    search_compliance_raw, deduplicates by service_ref (keeping highest score),
    and merges new unique results into state.all_results_flat.
    Always transitions to RerankerNode.
    """

    async def run(
        self,
        ctx: GraphRunContext[LoopState, ComplianceSearchDeps],
    ) -> RerankerNode:
        state = ctx.state
        deps = ctx.deps

        queries = state.expander_output.queries if state.expander_output else []
        if not queries:
            logger.warning("SearchNode: no queries to execute")
            return RerankerNode()

        logger.info("SearchNode: executing %d queries concurrently", len(queries))
        state.sse_events.append({
            "type": "status",
            "text": f"جاري تنفيذ {len(queries)} استعلامات بحث...",
        })

        # Run all queries concurrently (search_compliance_raw handles embedding internally)
        tasks = [search_compliance_raw(q, deps) for q in queries]
        results_per_query: list[list[dict]] = await asyncio.gather(*tasks)

        # Log per-query results
        for qi, (query, results) in enumerate(zip(queries, results_per_query), 1):
            state.search_results_log.append({
                "round": state.round_count,
                "query": query,
                "result_count": len(results),
            })
            if state.log_id:
                try:
                    rationale = (
                        state.expander_output.rationales[qi - 1]
                        if state.expander_output and qi <= len(state.expander_output.rationales)
                        else ""
                    )
                    save_search_query_md(
                        log_id=state.log_id,
                        round_num=state.round_count,
                        query_index=qi,
                        query=query,
                        results=results,
                        rationale=rationale,
                    )
                except Exception as le:
                    logger.warning("save_search_query_md failed: %s", le)

        # Dedup by service_ref within this batch — keep row with highest score
        service_map: dict[str, dict] = {}
        for results in results_per_query:
            for row in results:
                ref = row.get("service_ref", "")
                if not ref:
                    continue
                existing = service_map.get(ref)
                if existing is None or row.get("score", 0.0) > existing.get("score", 0.0):
                    service_map[ref] = row

        # Merge new unique results into all_results_flat (accumulated across rounds)
        existing_refs = {r.get("service_ref") for r in state.all_results_flat}
        new_rows = [r for ref, r in service_map.items() if ref not in existing_refs]
        state.all_results_flat.extend(new_rows)

        # Sort flat list by score DESC
        state.all_results_flat.sort(key=lambda r: r.get("score", 0.0), reverse=True)

        total = len(state.all_results_flat)
        logger.info(
            "SearchNode: %d new unique services added, %d total unique services",
            len(new_rows),
            total,
        )
        state.sse_events.append({
            "type": "status",
            "text": f"تم استرجاع {total} خدمة حكومية فريدة.",
        })

        return RerankerNode()


# -- RerankerNode --------------------------------------------------------------


class RerankerNode(BaseNode[LoopState, ComplianceSearchDeps, ComplianceSearchResult]):
    """Runs ServiceReranker agent -- classifies all results as keep/drop.

    Receives the full flat list of unique services (state.all_results_flat),
    classifies each as keep or drop, accumulates kept results across rounds,
    and gates the retry loop via the sufficient flag.

    Routes to ExpanderNode (loop back) or End(ComplianceSearchResult).
    """

    async def run(
        self,
        ctx: GraphRunContext[LoopState, ComplianceSearchDeps],
    ) -> Union[ExpanderNode, End[ComplianceSearchResult]]:
        state = ctx.state

        logger.info(
            "RerankerNode round %d -- %d flat results to classify",
            state.round_count,
            len(state.all_results_flat),
        )

        if not state.all_results_flat:
            # Nothing to classify — exit with weak quality
            logger.warning("RerankerNode: no results to classify, exiting with weak quality")
            state.sse_events.append({
                "type": "status",
                "text": "لم يتم العثور على خدمات حكومية ذات صلة.",
            })
            return End(ComplianceSearchResult(
                kept_results=[],
                quality="weak",
                queries_used=list(state.queries_used),
                rounds_used=state.round_count,
            ))

        reranker = create_reranker_agent()
        n_queries = len(state.expander_output.queries) if state.expander_output else 1
        user_message = build_reranker_user_message(
            state.focus_instruction,
            state.all_results_flat,
            state.round_count,
            n_queries,
        )

        state.sse_events.append({
            "type": "status",
            "text": f"جاري تصنيف {len(state.all_results_flat)} خدمة...",
        })

        try:
            result = await reranker.run(user_message, usage_limits=RERANKER_LIMITS)
            output: ServiceRerankerOutput = result.output
            state.reranker_output = output

            # Track usage
            ru = result.usage()
            reranker_usage = {
                "agent": "reranker",
                "round": state.round_count,
                "requests": ru.requests,
                "input_tokens": ru.input_tokens,
                "output_tokens": ru.output_tokens,
                "total_tokens": ru.total_tokens,
            }
            state.inner_usage.append(reranker_usage)

            # Accumulate kept results (dedup by service_ref across rounds)
            kept_refs = {r.get("service_ref") for r in state.kept_results}
            for dec in output.decisions:
                if dec.action == "keep":
                    idx = dec.position - 1
                    if 0 <= idx < len(state.all_results_flat):
                        row = dict(state.all_results_flat[idx])  # copy to avoid mutation
                        ref = row.get("service_ref", "")
                        if ref and ref not in kept_refs:
                            row["_relevance"] = dec.relevance or "medium"
                            row["_reasoning"] = dec.reasoning
                            state.kept_results.append(row)
                            kept_refs.add(ref)

            kept_count = len(state.kept_results)
            logger.info(
                "RerankerNode: sufficient=%s, kept=%d, weak_axes=%d",
                output.sufficient,
                kept_count,
                len(output.weak_axes),
            )
            state.sse_events.append({
                "type": "status",
                "text": (
                    f"تم الاحتفاظ بـ {kept_count} خدمة ذات صلة — "
                    f"الجودة: {'كافية' if output.sufficient else 'غير كافية'}"
                ),
            })

            # Track round summary
            state.round_summaries.append({
                "round": state.round_count,
                "expander_queries": list(state.expander_output.queries) if state.expander_output else [],
                "search_total": len(state.all_results_flat),
                "reranker_kept": kept_count,
                "reranker_sufficient": output.sufficient,
                "weak_axes_count": len(output.weak_axes),
            })

            # Log reranker to file
            if state.log_id:
                try:
                    save_reranker_md(
                        log_id=state.log_id,
                        round_num=state.round_count,
                        user_message=user_message,
                        output=output,
                        all_results_flat=state.all_results_flat,
                        usage=reranker_usage,
                        messages_json=result.all_messages_json(),
                    )
                except Exception as le:
                    logger.warning("save_reranker_md failed: %s", le)

            # Route: loop back if not sufficient and rounds remain
            if not output.sufficient and state.round_count < MAX_ROUNDS:
                state.weak_axes = output.weak_axes
                state.sse_events.append({
                    "type": "status",
                    "text": f"جاري إعادة البحث (الجولة {state.round_count + 1})...",
                })
                logger.info(
                    "RerankerNode: looping back -- %d weak axes",
                    len(output.weak_axes),
                )
                return ExpanderNode()

            # Sufficient or max rounds reached — determine quality
            if output.sufficient and state.round_count == 1:
                quality = "strong"
            elif output.sufficient:
                quality = "moderate"
            else:
                quality = "weak"

            state.sse_events.append({
                "type": "status",
                "text": f"اكتمل البحث — الجودة: {quality}",
            })

            return End(ComplianceSearchResult(
                kept_results=list(state.kept_results),
                quality=quality,
                queries_used=list(state.queries_used),
                rounds_used=state.round_count,
            ))

        except Exception as e:
            logger.error("RerankerNode error: %s", e, exc_info=True)
            state.sse_events.append({
                "type": "status",
                "text": "حدث خطأ أثناء تصنيف النتائج.",
            })

            return End(ComplianceSearchResult(
                kept_results=list(state.kept_results),
                quality="weak",
                queries_used=list(state.queries_used),
                rounds_used=state.round_count,
            ))


# -- Graph assembly and entry point --------------------------------------------


compliance_search_graph = Graph(
    nodes=[ExpanderNode, SearchNode, RerankerNode],
)


async def run_compliance_search(
    focus_instruction: str,
    user_context: str,
    deps: ComplianceSearchDeps,
    log_id: str | None = None,
) -> ComplianceSearchResult:
    """Run the compliance search loop.

    Creates fresh LoopState, runs the pydantic_graph from ExpanderNode,
    transfers SSE events to deps._events, returns ComplianceSearchResult.

    Args:
        focus_instruction: Arabic -- what to search for (regulation context + query).
        user_context: Arabic -- user's personal situation/question (may be empty).
        deps: ComplianceSearchDeps with supabase, embedding_fn, etc.

    Returns:
        ComplianceSearchResult with kept_results (raw service dicts), quality,
        queries_used, and rounds_used.
    """
    from .logger import create_run_dir, make_log_id, save_run_log

    logger.info(
        "run_compliance_search: focus='%s'",
        focus_instruction[:80],
    )

    if not log_id:
        log_id = make_log_id()
    create_run_dir(log_id)

    state = LoopState(
        focus_instruction=focus_instruction,
        user_context=user_context,
        log_id=log_id,
    )

    t0 = _time.perf_counter()
    error_msg: str | None = None

    try:
        graph_result = await compliance_search_graph.run(
            ExpanderNode(),
            state=state,
            deps=deps,
        )

        # Transfer SSE events from loop state to deps
        deps._events.extend(state.sse_events)

        output = graph_result.output

        logger.info(
            "run_compliance_search complete: quality=%s, rounds=%d, kept=%d, queries=%d",
            output.quality,
            output.rounds_used,
            len(output.kept_results),
            len(output.queries_used),
        )

    except Exception as e:
        logger.error("run_compliance_search failed: %s", e, exc_info=True)
        error_msg = str(e)
        deps._events.extend(state.sse_events)
        deps._events.append({
            "type": "status",
            "text": "حدث خطأ أثناء حلقة البحث في الخدمات الحكومية.",
        })

        output = ComplianceSearchResult(
            kept_results=list(state.kept_results),
            quality="weak",
            queries_used=list(state.queries_used),
            rounds_used=state.round_count,
        )

    duration = _time.perf_counter() - t0

    # Save logs (run.json + run.md + per-node MDs already written inline)
    save_run_log(
        log_id=log_id,
        focus_instruction=focus_instruction,
        user_context=user_context,
        duration_s=duration,
        result=output,
        events=list(deps._events),
        search_results_log=list(state.search_results_log),
        inner_usage=list(state.inner_usage),
        round_summaries=list(state.round_summaries),
        error=error_msg,
    )

    return output
