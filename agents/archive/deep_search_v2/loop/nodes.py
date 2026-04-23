"""Graph nodes for the inner search loop.

Four nodes forming the expand-search-aggregate-report loop:
- ExpanderNode: LLM query expansion (QueryExpander agent)
- SearchNode: Programmatic search execution (no LLM)
- AggregateNode: LLM result evaluation and synthesis (Aggregator agent)
- ReportNode: Programmatic report creation and optional DB insert (no LLM)
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Union

from pydantic_graph import BaseNode, End, GraphRunContext

from ..models import (
    AggregatorOutput,
    DeepSearchDeps,
    ExpanderOutput,
    LoopResult,
    LoopState,
    SearchResult,
)
from ..prompts import build_aggregator_user_message
from ..report_builder import build_report
from ..search_pipeline import run_search_pipeline
from .aggregator import AGGREGATOR_LIMITS, create_aggregator_agent
from .expander import EXPANDER_LIMITS, create_expander_agent

logger = logging.getLogger(__name__)

MAX_ROUNDS = 1


def _extract_thinking(result) -> list[str]:
    """Extract thinking text from an agent RunResult."""
    parts: list[str] = []
    try:
        for msg in result.all_messages():
            for part in msg.parts:
                if getattr(part, "part_kind", "") == "thinking":
                    text = getattr(part, "thinking", "") or getattr(part, "content", "")
                    if text:
                        parts.append(text)
    except Exception:
        pass
    return parts


# -- ExpanderNode --------------------------------------------------------------


class ExpanderNode(BaseNode[LoopState, DeepSearchDeps, LoopResult]):
    """Runs QueryExpander agent with structured output.

    Creates 2-4 search queries from the sub-question.
    On round 2+, passes weak_axes for targeted re-search.
    Always transitions to SearchNode.
    """

    async def run(
        self,
        ctx: GraphRunContext[LoopState, DeepSearchDeps],
    ) -> SearchNode:
        state = ctx.state
        state.round_count += 1

        logger.info(
            "ExpanderNode round %d -- sub_question: %s",
            state.round_count,
            state.sub_question[:80],
        )

        # Create expander agent, with weak_axes on round 2+
        weak_axes = state.weak_axes if state.round_count > 1 else None
        strong_summary = None
        if state.aggregator_output and state.aggregator_output.strong_results_summary:
            strong_summary = state.aggregator_output.strong_results_summary

        expander = create_expander_agent(
            weak_axes=weak_axes,
            strong_results_summary=strong_summary,
        )

        # Build user message: sub_question + context
        user_message = state.sub_question
        if state.context:
            user_message = f"{state.sub_question}\n\nسياق إضافي:\n{state.context}"

        try:
            result = await expander.run(
                user_message,
                usage_limits=EXPANDER_LIMITS,
            )
            output: ExpanderOutput = result.output

            # Capture usage
            eu = result.usage()
            state.inner_usage.append({
                "agent": "expander",
                "round": state.round_count,
                "requests": eu.requests,
                "input_tokens": eu.input_tokens,
                "output_tokens": eu.output_tokens,
                "total_tokens": eu.total_tokens,
            })

            # Capture thinking
            thinking_parts = _extract_thinking(result)
            if thinking_parts:
                state.inner_thinking.append({
                    "agent": "expander",
                    "round": state.round_count,
                    "thinking": thinking_parts,
                })

            # Store in state
            state.expander_output = output

            # Capture expander queries for logging
            state.expander_queries.append({
                "round": state.round_count,
                "queries": [
                    {"tool": q.tool, "query": q.query, "rationale": q.rationale}
                    for q in output.queries
                ],
                "status_message": output.status_message,
            })

            # Emit SSE status
            if output.status_message:
                state.sse_events.append({
                    "type": "status",
                    "text": output.status_message,
                })

            logger.info(
                "ExpanderNode: %d queries -- %s",
                len(output.queries),
                ", ".join(f"{q.tool}:{q.query[:40]}" for q in output.queries),
            )

        except Exception as e:
            logger.error("ExpanderNode error: %s", e, exc_info=True)
            state.sse_events.append({
                "type": "status",
                "text": "حدث خطأ أثناء توسيع الاستعلامات.",
            })
            # Create minimal fallback output so SearchNode can proceed
            from ..models import SearchQuery
            state.expander_output = ExpanderOutput(
                queries=[
                    SearchQuery(
                        tool="regulations",
                        query=state.sub_question,
                        rationale="Fallback: expander failed",
                    ),
                ],
                status_message="جاري البحث...",
            )

        return SearchNode()


# -- SearchNode ----------------------------------------------------------------


class SearchNode(BaseNode[LoopState, DeepSearchDeps, LoopResult]):
    """Programmatic search -- no LLM. Runs queries via asyncio.gather.

    Reads state.expander_output.queries, executes them concurrently,
    appends to state.all_search_results, returns AggregateNode.
    """

    async def run(
        self,
        ctx: GraphRunContext[LoopState, DeepSearchDeps],
    ) -> AggregateNode:
        state = ctx.state
        deps = ctx.deps

        queries = state.expander_output.queries if state.expander_output else []
        if not queries:
            logger.warning("SearchNode: no queries to execute")
            return AggregateNode()

        logger.info("SearchNode: executing %d queries concurrently", len(queries))
        state.sse_events.append({
            "type": "status",
            "text": f"جاري تنفيذ {len(queries)} استعلامات بحث...",
        })

        # Execute all queries concurrently
        tasks = [
            run_search_pipeline(
                query=sq.query,
                tool=sq.tool,
                deps=deps,
                sse_events=state.sse_events,
            )
            for sq in queries
        ]

        results: list[SearchResult] = await asyncio.gather(*tasks)

        # Append results to state
        state.all_search_results.extend(results)

        # Log raw search results for debugging
        for r in results:
            state.search_results_log.append({
                "round": state.round_count,
                "tool": r.tool,
                "query": r.query,
                "result_count": r.result_count,
                "is_mock": r.is_mock,
                "raw_markdown": r.raw_markdown,
            })

        total_count = sum(r.result_count for r in results)
        logger.info(
            "SearchNode: %d queries returned %d total results",
            len(results),
            total_count,
        )
        state.sse_events.append({
            "type": "status",
            "text": f"تم استلام {total_count} نتيجة -- جاري التقييم والتحليل...",
        })

        return AggregateNode()


# -- AggregateNode -------------------------------------------------------------


class AggregateNode(BaseNode[LoopState, DeepSearchDeps, LoopResult]):
    """Runs Aggregator agent -- evaluates sufficiency, synthesizes results.

    Routes to ExpanderNode (weak, loop back) or ReportNode (sufficient/max rounds).
    """

    async def run(
        self,
        ctx: GraphRunContext[LoopState, DeepSearchDeps],
    ) -> Union[ExpanderNode, ReportNode, End[LoopResult]]:
        state = ctx.state
        deps = ctx.deps

        logger.info(
            "AggregateNode round %d -- %d total results, %d strong locked",
            state.round_count,
            len(state.all_search_results),
            len(state.strong_results),
        )

        # Build the aggregator agent
        aggregator = create_aggregator_agent()

        # Build user message with ALL search results (strong + new)
        all_results = state.strong_results + state.all_search_results
        user_message = build_aggregator_user_message(state.sub_question, all_results)

        try:
            state.sse_events.append({
                "type": "status",
                "text": "جاري تقييم جودة النتائج وإنتاج التحليل...",
            })

            result = await aggregator.run(
                user_message,
                usage_limits=AGGREGATOR_LIMITS,
            )
            output: AggregatorOutput = result.output

            # Capture usage
            au = result.usage()
            state.inner_usage.append({
                "agent": "aggregator",
                "round": state.round_count,
                "requests": au.requests,
                "input_tokens": au.input_tokens,
                "output_tokens": au.output_tokens,
                "total_tokens": au.total_tokens,
            })

            # Capture thinking
            thinking_parts = _extract_thinking(result)
            if thinking_parts:
                state.inner_thinking.append({
                    "agent": "aggregator",
                    "round": state.round_count,
                    "thinking": thinking_parts,
                })

            # Store in state
            state.aggregator_output = output

            logger.info(
                "AggregateNode: sufficient=%s, weak_axes=%d, synthesis=%d chars",
                output.sufficient,
                len(output.weak_axes),
                len(output.synthesis_md),
            )

            # Route based on output
            if not output.sufficient and state.round_count < MAX_ROUNDS:
                # Loop back: selective re-search for weak axes only
                state.weak_axes = output.weak_axes

                # Move strong results from all_search_results to strong_results
                weak_tools = {wa.tool for wa in output.weak_axes}
                new_strong = [
                    r for r in state.all_search_results if r.tool not in weak_tools
                ]
                state.strong_results.extend(new_strong)

                # Clear all_search_results for next round (keep only weak tool results)
                state.all_search_results = [
                    r for r in state.all_search_results if r.tool in weak_tools
                ]

                state.sse_events.append({
                    "type": "status",
                    "text": (
                        f"النتائج غير كافية لبعض المحاور -- "
                        f"جاري إعادة البحث (الجولة {state.round_count + 1})..."
                    ),
                })

                logger.info(
                    "AggregateNode: looping back -- weak tools: %s",
                    ", ".join(weak_tools),
                )
                return ExpanderNode()

            # Sufficient or max rounds reached -- proceed to report
            return ReportNode()

        except Exception as e:
            logger.error("AggregateNode error: %s", e, exc_info=True)
            state.sse_events.append({
                "type": "status",
                "text": "حدث خطأ أثناء تقييم النتائج.",
            })

            # Try to produce a basic result if we have any search results
            return ReportNode()


# -- ReportNode ----------------------------------------------------------------


class ReportNode(BaseNode[LoopState, DeepSearchDeps, LoopResult]):
    """Programmatic report creation. Optionally inserts artifact in DB.

    In new report mode: inserts artifact, sets deps.artifact_id.
    In edit mode (is_edit_mode flag on state): skips DB insert.
    Returns End(LoopResult).
    """

    # is_edit_mode is communicated via the existence of deps.artifact_id
    # and is managed by run_search_loop caller

    async def run(
        self,
        ctx: GraphRunContext[LoopState, DeepSearchDeps],
    ) -> End[LoopResult]:
        state = ctx.state
        deps = ctx.deps

        agg = state.aggregator_output
        if not agg:
            logger.warning("ReportNode: no aggregator output available")
            return End(
                LoopResult(
                    sub_question=state.sub_question,
                    report_md="",
                    answer_summary="لم يتم إنتاج تحليل.",
                    rounds_used=state.round_count,
                )
            )

        # Build the report
        report_md = build_report(
            synthesis_md=agg.synthesis_md,
            citations=agg.citations,
            question=state.sub_question,
        )

        logger.info("ReportNode: built report -- %d chars", len(report_md))

        artifact_id: str | None = None

        # Only insert into DB if NOT in edit mode and no existing artifact
        # Edit mode is when deps.artifact_id already exists (PlanAgent handles update)
        if not deps.artifact_id:
            try:
                now = datetime.now(timezone.utc).isoformat()
                insert_data = {
                    "user_id": deps.user_id,
                    "conversation_id": deps.conversation_id,
                    "content_md": report_md,
                    "agent_family": "deep_search",
                    "artifact_type": "report",
                    "title": f"تقرير بحث: {state.sub_question[:60]}",
                    "metadata": json.dumps(
                        {"citations": agg.citations}, ensure_ascii=False,
                    ),
                    "created_at": now,
                    "updated_at": now,
                }
                if deps.case_id:
                    insert_data["case_id"] = deps.case_id

                result = (
                    deps.supabase.table("artifacts")
                    .insert(insert_data)
                    .execute()
                )

                if result.data:
                    artifact_id = result.data[0].get("artifact_id")
                    deps.artifact_id = artifact_id  # Mutable exception
                    logger.info("ReportNode: inserted artifact %s", artifact_id)

                    state.sse_events.append({
                        "type": "artifact_created",
                        "artifact_id": artifact_id,
                        "artifact_type": "report",
                        "title": f"تقرير بحث: {state.sub_question[:60]}",
                    })
                else:
                    logger.warning("ReportNode: insert returned no data")

            except Exception as e:
                logger.error("ReportNode DB insert failed: %s", e)
                state.sse_events.append({
                    "type": "status",
                    "text": "تم إنتاج التقرير لكن لم يتم حفظه في قاعدة البيانات.",
                })
        else:
            # Edit mode -- PlanAgent will call update_report
            artifact_id = deps.artifact_id
            logger.info(
                "ReportNode: edit mode -- skipping DB insert (artifact_id=%s)",
                artifact_id,
            )

        return End(
            LoopResult(
                sub_question=state.sub_question,
                report_md=report_md,
                artifact_id=artifact_id,
                answer_summary=agg.answer_summary or "تم إنتاج التقرير.",
                citations=agg.citations,
                rounds_used=state.round_count,
                inner_usage=list(state.inner_usage),
                inner_thinking=list(state.inner_thinking),
                expander_queries=list(state.expander_queries),
                search_results_log=list(state.search_results_log),
            )
        )
