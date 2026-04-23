"""Turn runner for deep_search planner agent.

Entry point: handle_deep_search_turn() -- called by the orchestrator.
Uses agent.iter() with a manual .next() loop for SSE event interception.
"""
from __future__ import annotations

import logging
import time as _time
from datetime import datetime, timezone

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from pydantic_graph import End

from agents.models import PlannerResult, TaskContinue, TaskEnd
from agents.utils.history import messages_to_history

from .agent import planner_agent as _default_agent, PLANNER_LIMITS
from .deps import SearchDeps
from .logger import save_run_log

logger = logging.getLogger(__name__)

ERROR_MSG_AR = "عذراً، حدث خطأ أثناء البحث القانوني. يرجى المحاولة مرة أخرى."


# -- Helpers ------------------------------------------------------------------


def _format_task_history(task_history: list[dict]) -> list[ModelMessage]:
    """Convert serialized task-scoped history into Pydantic AI ModelMessage list."""
    return messages_to_history(task_history)


def _map_result(result: PlannerResult) -> TaskContinue | TaskEnd:
    """Map agent structured output to orchestrator task models.

    Handles the edge case where end_reason is "pending" on task_done=True
    by defaulting to "completed".
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


def _get_current_artifact(deps: SearchDeps) -> str | None:
    """Read current artifact content from DB to preserve on error."""
    if not deps.artifact_id:
        return None
    try:
        result = (
            deps.supabase.table("artifacts")
            .select("content_md")
            .eq("artifact_id", deps.artifact_id)
            .maybe_single()
            .execute()
        )
        if result and result.data:
            return result.data.get("content_md", "")
    except Exception:
        logger.debug("Could not read artifact %s for error fallback", deps.artifact_id)
    return None


def _error_fallback(deps: SearchDeps) -> TaskContinue:
    """Return TaskContinue with Arabic error message, preserving existing artifact.

    Uses TaskContinue (not TaskEnd) so the task stays pinned and the user
    can retry without losing accumulated state.
    """
    return TaskContinue(
        response=ERROR_MSG_AR,
        artifact=_get_current_artifact(deps) or "",
    )


# -- Main runner --------------------------------------------------------------


async def handle_deep_search_turn(
    message: str,
    deps: SearchDeps,
    task_history: list[dict] | None = None,
    *,
    agent: Agent | None = None,
) -> tuple[TaskContinue | TaskEnd, list[dict]]:
    """Run one turn of the deep search planner.

    Called by the orchestrator::

        result, events = await handle_deep_search_turn(
            message=question, deps=deps,
            task_history=task.history if task.history else None,
        )

    Args:
        agent: Optional Agent override (e.g. from create_planner_agent with
               a different prompt).  Defaults to the module-level planner_agent.

    Returns:
        (result, sse_events) -- result is TaskContinue or TaskEnd,
        sse_events is a list of SSE event dicts collected during the run.
    """
    start = _time.time()
    log_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")

    # Reset events and tool logs for this turn
    deps._sse_events = []
    deps._tool_logs = []

    # Format task history
    history: list[ModelMessage] | None = None
    if task_history:
        history = _format_task_history(task_history)

    active_agent = agent or _default_agent

    # Run the agent with manual .next() loop
    try:
        async with active_agent.iter(
            message,
            deps=deps,
            message_history=history,
            usage_limits=PLANNER_LIMITS,
        ) as run:
            node = run.next_node
            while not isinstance(node, End):
                node = await run.next(node)

        # Extract result
        planner_output: PlannerResult = run.result.output
        events = list(deps._sse_events)

        # Capture full model conversation (reasoning, tool calls, tool results)
        all_messages_json = run.all_messages_json()

        # Log usage
        usage = run.usage()
        duration = _time.time() - start
        logger.info(
            "Deep search turn -- requests=%s, output_tokens=%s, "
            "tool_calls=%s, task_done=%s, duration=%.1fs",
            usage.requests,
            usage.output_tokens,
            usage.tool_calls,
            planner_output.task_done,
            duration,
        )

        mapped = _map_result(planner_output)

        # Save structured folder log (v3)
        save_run_log(
            log_id=log_id,
            message=message,
            task_history=task_history,
            result=mapped,
            events=events,
            duration_s=duration,
            tool_logs=list(deps._tool_logs),
            usage={
                "requests": usage.requests,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "total_tokens": usage.total_tokens,
                "tool_calls": usage.tool_calls,
            },
            planner_output=planner_output,
            model_messages_json=all_messages_json,
        )

        return mapped, events

    except Exception as e:
        logger.error("Error in deep_search planner: %s", e, exc_info=True)
        events = list(deps._sse_events)
        duration = _time.time() - start
        fallback = _error_fallback(deps)

        # Save error log
        save_run_log(
            log_id=log_id,
            message=message,
            task_history=task_history,
            result=fallback,
            events=events,
            duration_s=duration,
            tool_logs=list(deps._tool_logs),
            error=f"{type(e).__name__}: {e}",
        )

        return fallback, events
