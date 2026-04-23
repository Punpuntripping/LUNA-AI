"""Inner search loop graph assembly and entry point.

Assembles the pydantic_graph state machine for the search loop:
ExpanderNode -> SearchNode -> AggregateNode -> ReportNode
with optional loop-back from AggregateNode to ExpanderNode on weak results.

The entry point run_search_loop() is called by PlanAgent's invoke_search_loop tool.
"""
from __future__ import annotations

import logging

from pydantic_graph import Graph

from ..models import DeepSearchDeps, LoopResult, LoopState
from .nodes import AggregateNode, ExpanderNode, ReportNode, SearchNode

logger = logging.getLogger(__name__)


# -- Graph assembly ------------------------------------------------------------

search_loop_graph = Graph(
    nodes=[ExpanderNode, SearchNode, AggregateNode, ReportNode],
)


# -- Entry point ---------------------------------------------------------------


async def run_search_loop(
    sub_question: str,
    context: str,
    deps: DeepSearchDeps,
    is_edit_mode: bool = False,
) -> LoopResult:
    """Run the complete search loop for a sub-question.

    Creates fresh LoopState, runs the graph from ExpanderNode,
    and returns the LoopResult. SSE events collected during the loop
    are transferred to deps._sse_events.

    Args:
        sub_question: Focused legal sub-question to research (Arabic).
        context: Additional context from PlanAgent.
        deps: Top-level DeepSearchDeps (shared with PlanAgent).
        is_edit_mode: When True, ReportNode skips DB insert.
            Managed by checking deps.artifact_id existence.

    Returns:
        LoopResult with report_md, artifact_id, citations, etc.
    """
    logger.info(
        "run_search_loop: sub_question='%s', edit_mode=%s",
        sub_question[:80],
        is_edit_mode,
    )

    # Build fresh loop state
    state = LoopState(
        sub_question=sub_question,
        context=context,
    )

    try:
        # Run the graph
        graph_result = await search_loop_graph.run(
            ExpanderNode(),
            state=state,
            deps=deps,
        )

        # Transfer SSE events from loop state to top-level deps
        deps._sse_events.extend(state.sse_events)

        loop_result = graph_result.output

        logger.info(
            "run_search_loop complete: rounds=%d, report=%d chars, artifact=%s",
            loop_result.rounds_used,
            len(loop_result.report_md),
            loop_result.artifact_id,
        )

        return loop_result

    except Exception as e:
        logger.error("run_search_loop failed: %s", e, exc_info=True)
        # Transfer any events collected before failure
        deps._sse_events.extend(state.sse_events)
        deps._sse_events.append({
            "type": "status",
            "text": "حدث خطأ أثناء حلقة البحث.",
        })

        # Return a minimal LoopResult
        return LoopResult(
            sub_question=sub_question,
            report_md="",
            answer_summary="حدث خطأ أثناء البحث.",
            rounds_used=state.round_count,
        )
