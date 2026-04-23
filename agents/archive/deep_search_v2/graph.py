"""Top-level entry points for deep_search_v2 (revised).

Provides handle_deep_search_turn() and build_search_deps() with identical
signatures to the previous implementation, so the orchestrator can switch
without changes.
"""
from __future__ import annotations

import logging
import time as _time
from datetime import datetime, timezone

import httpx
from pydantic_graph import End
from supabase import Client as SupabaseClient

from agents.models import PlannerResult, TaskContinue, TaskEnd

from .logger import save_run_log
from .models import DeepSearchDeps
from .plan_agent import PLAN_AGENT_LIMITS, plan_agent

logger = logging.getLogger(__name__)

ERROR_MSG_AR = "عذراً، حدث خطأ أثناء البحث القانوني. يرجى المحاولة مرة أخرى."


# -- Shared httpx client (lazy init) ------------------------------------------

_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    """Return a module-level httpx.AsyncClient, creating it on first call."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=30.0)
    return _http_client


# -- Result mapping ------------------------------------------------------------


def _map_result(result: PlannerResult) -> TaskContinue | TaskEnd:
    """Map PlannerResult to orchestrator task models.

    - task_done=True + end_reason="out_of_scope" -> TaskEnd(reason="out_of_scope")
    - task_done=True + end_reason="completed" -> TaskEnd(reason="completed")
    - task_done=False -> TaskContinue(response=answer_ar, artifact=artifact_md)
    """
    if result.task_done:
        reason = result.end_reason if result.end_reason != "pending" else "completed"
        return TaskEnd(
            reason=reason,
            summary=result.search_summary,
            artifact=result.artifact_md,
            last_response=result.answer_ar,
        )
    return TaskContinue(
        response=result.answer_ar,
        artifact=result.artifact_md,
    )


# -- Task history formatting ---------------------------------------------------


def _format_task_history(task_history: list[dict]) -> str | None:
    """Format task history into a string for planner context."""
    if not task_history:
        return None

    lines: list[str] = []
    for msg in task_history[-10:]:  # Last 10 messages
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if content:
            label = "المستخدم" if role == "user" else "المساعد"
            lines.append(f"[{label}]: {content[:200]}")

    return "\n".join(lines) if lines else None


# -- Main entry point ----------------------------------------------------------


async def handle_deep_search_turn(
    message: str,
    deps: DeepSearchDeps,
    task_history: list[dict] | None = None,
) -> tuple[TaskContinue | TaskEnd, list[dict]]:
    """Run one turn of deep search. Called by orchestrator.

    Same contract as previous implementation::

        result, events = await handle_deep_search_turn(
            message=question, deps=deps,
            task_history=task.history if task.history else None,
        )

    Returns:
        (result, sse_events) -- result is TaskContinue or TaskEnd,
        sse_events is list of SSE event dicts collected during the run.
    """
    start = _time.time()
    log_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")

    # Reset per-turn state on deps
    deps._sse_events = []
    deps._loop_results = []
    deps._task_history_formatted = _format_task_history(task_history)

    try:
        # Run PlanAgent via agent.iter() with manual .next() loop
        async with plan_agent.iter(
            message,
            deps=deps,
            usage_limits=PLAN_AGENT_LIMITS,
        ) as run:
            node = run.next_node
            while not isinstance(node, End):
                node = await run.next(node)

        output: PlannerResult = run.result.output
        events = list(deps._sse_events)
        duration = _time.time() - start

        # Get usage from the PlanAgent run
        usage = run.usage()

        logger.info(
            "deep_search_v2 turn -- requests=%s, tokens=%s, "
            "loops=%d, done=%s, %.1fs",
            usage.requests,
            usage.total_tokens,
            len(deps._loop_results),
            output.task_done,
            duration,
        )

        mapped = _map_result(output)

        # Log the full execution
        save_run_log(
            log_id=log_id,
            message=message,
            task_history=task_history,
            result=mapped,
            events=events,
            duration_s=duration,
            usage={
                "plan_agent_requests": usage.requests,
                "plan_agent_input_tokens": usage.input_tokens,
                "plan_agent_output_tokens": usage.output_tokens,
                "plan_agent_total_tokens": usage.total_tokens,
                "plan_agent_tool_calls": usage.tool_calls,
                "search_loops": len(deps._loop_results),
            },
            agent_output=output,
            model_messages_json=run.result.all_messages_json(),
            loop_results=[
                {
                    "sub_question": lr.sub_question,
                    "rounds_used": lr.rounds_used,
                    "report_md": lr.report_md,
                    "answer_summary": lr.answer_summary,
                    "artifact_id": lr.artifact_id,
                    "citations": lr.citations,
                    "inner_usage": lr.inner_usage,
                    "inner_thinking": lr.inner_thinking,
                    "expander_queries": lr.expander_queries,
                    "search_results_log": lr.search_results_log,
                }
                for lr in deps._loop_results
            ],
        )

        return mapped, events

    except Exception as e:
        logger.error("Error in deep_search_v2: %s", e, exc_info=True)
        events = list(deps._sse_events)
        duration = _time.time() - start

        # Preserve existing artifact if possible
        existing_artifact = ""
        if deps._previous_report_md:
            existing_artifact = deps._previous_report_md

        fallback = TaskContinue(response=ERROR_MSG_AR, artifact=existing_artifact)

        save_run_log(
            log_id=log_id,
            message=message,
            task_history=task_history,
            result=fallback,
            events=events,
            duration_s=duration,
            error=f"{type(e).__name__}: {e}",
        )

        return fallback, events


# -- Deps builder --------------------------------------------------------------


async def build_search_deps(
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    supabase: SupabaseClient,
    artifact_id: str | None = None,
) -> DeepSearchDeps:
    """Build DeepSearchDeps for a turn. Called by orchestrator.

    Pre-fetches case memory from DB when case_id is provided.
    Loads previous report content when artifact_id is provided.
    """
    from agents.utils.embeddings import embed_regulation_query
    from shared.config import get_settings

    settings = get_settings()

    # Pre-fetch case memory
    case_memory: str | None = None
    if case_id:
        try:
            result = (
                supabase.table("case_memories")
                .select("content_ar, memory_type")
                .eq("case_id", case_id)
                .is_("deleted_at", "null")
                .order("created_at", desc=True)
                .limit(20)
                .execute()
            )
            if result.data:
                lines = [
                    f"- [{m['memory_type']}] {m['content_ar']}"
                    for m in result.data
                ]
                case_memory = "\n".join(lines)
        except Exception as e:
            logger.warning("Error loading case memory %s: %s", case_id, e)

    # Pre-fetch previous report content if editing
    previous_report_md: str | None = None
    if artifact_id:
        try:
            result = (
                supabase.table("artifacts")
                .select("content_md")
                .eq("artifact_id", artifact_id)
                .maybe_single()
                .execute()
            )
            if result and result.data:
                previous_report_md = result.data.get("content_md", "")
        except Exception as e:
            logger.warning("Error loading artifact %s: %s", artifact_id, e)

    return DeepSearchDeps(
        supabase=supabase,
        embedding_fn=embed_regulation_query,
        user_id=user_id,
        conversation_id=conversation_id,
        case_id=case_id,
        artifact_id=artifact_id,
        jina_api_key=settings.JINA_RERANKER_API_KEY or "",
        http_client=_get_http_client(),
        _sse_events=[],
        _case_memory=case_memory,
        _previous_report_md=previous_report_md,
        _task_history_formatted=None,
        _loop_results=[],
    )
