"""PlanAgent definition with tools for deep_search_v2 (revised).

The PlanAgent is the supervisor that orchestrates the search process:
- Analyzes the user's legal question
- Invokes the inner search loop 1-3 times via invoke_search_loop
- Writes conversational Arabic responses
- Handles report editing via update_report
- Detects out-of-scope requests
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openrouter import OpenRouterModelSettings
from pydantic_ai.usage import UsageLimits

from agents.models import PlannerResult
from agents.utils.agent_models import get_agent_model

from .models import DeepSearchDeps
from .prompts import PLAN_AGENT_SYSTEM_PROMPT, build_plan_agent_dynamic_instructions

logger = logging.getLogger(__name__)


# -- Usage limits --------------------------------------------------------------

PLAN_AGENT_LIMITS = UsageLimits(
    response_tokens_limit=56_000,
    request_limit=15,    # Allows multiple invoke_search_loop calls
)


# -- Agent definition ----------------------------------------------------------

plan_agent = Agent(
    get_agent_model("deep_search_v2_plan_agent"),
    output_type=PlannerResult,
    deps_type=DeepSearchDeps,
    instructions=PLAN_AGENT_SYSTEM_PROMPT,
    model_settings=OpenRouterModelSettings(
        openrouter_reasoning={"effort": "high", "exclude": False},
        max_tokens=56_000,
    ),
    retries=2,
    end_strategy="early",
)


# -- Dynamic instructions ------------------------------------------------------


@plan_agent.instructions
def _dynamic(ctx: RunContext[DeepSearchDeps]) -> str:
    """Inject case memory, previous report, task history, loop results."""
    return build_plan_agent_dynamic_instructions(
        ctx.deps,
        ctx.deps._task_history_formatted,
        ctx.deps._loop_results,
    )


# -- Tools ---------------------------------------------------------------------


@plan_agent.tool
async def invoke_search_loop(
    ctx: RunContext[DeepSearchDeps],
    sub_question: str,
    context: str,
) -> str:
    """Run the full Search Loop for a sub-question. Returns summary.

    Can be called 1-3 times per turn. Each invocation runs the full
    ExpanderNode -> SearchNode -> AggregateNode -> ReportNode loop.

    Args:
        sub_question: Focused legal sub-question to research (Arabic).
        context: Additional context from the user question or prior results.
    """
    from .loop import run_search_loop

    logger.info(
        "invoke_search_loop: sub_question='%s'",
        sub_question[:80],
    )

    ctx.deps._sse_events.append({
        "type": "status",
        "text": f"جاري البحث عن: {sub_question[:60]}...",
    })

    # Determine edit mode from deps
    is_edit_mode = bool(ctx.deps.artifact_id)

    loop_result = await run_search_loop(
        sub_question=sub_question,
        context=context,
        deps=ctx.deps,
        is_edit_mode=is_edit_mode,
    )

    # Store loop result on deps for dynamic instruction injection
    ctx.deps._loop_results.append(loop_result)

    # Return summary string for the LLM to use
    summary_parts = [
        f"تم البحث عن: {loop_result.sub_question}",
        f"عدد الجولات: {loop_result.rounds_used}",
    ]
    if loop_result.answer_summary:
        summary_parts.append(f"الملخص: {loop_result.answer_summary}")
    if loop_result.artifact_id:
        summary_parts.append(f"معرّف التقرير: {loop_result.artifact_id}")
    if loop_result.citations:
        summary_parts.append(f"عدد الاستشهادات: {len(loop_result.citations)}")

    return "\n".join(summary_parts)


@plan_agent.tool
async def update_report(
    ctx: RunContext[DeepSearchDeps],
    content_md: str,
    citations: list[dict],
) -> str:
    """Update an existing report artifact in the database.

    Used when editing mode is active (artifact_id exists on deps).
    Provide FULL updated content, not a diff.

    Args:
        content_md: Complete updated markdown report.
        citations: Complete updated citation list.
    """
    if not ctx.deps.artifact_id:
        return "لا يوجد تقرير سابق لتحديثه. استخدم invoke_search_loop لإنشاء تقرير جديد."

    try:
        ctx.deps.supabase.table("artifacts").update({
            "content_md": content_md,
            "metadata": json.dumps(
                {"citations": citations}, ensure_ascii=False,
            ),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("artifact_id", ctx.deps.artifact_id).execute()

        ctx.deps._sse_events.append({
            "type": "artifact_updated",
            "artifact_id": ctx.deps.artifact_id,
        })

        logger.info("update_report: updated artifact %s", ctx.deps.artifact_id)
        return f"تم تحديث التقرير بنجاح (artifact_id={ctx.deps.artifact_id})"

    except Exception as e:
        logger.error("update_report failed: %s", e)
        return f"خطأ أثناء تحديث التقرير: {e}"


@plan_agent.tool
async def ask_user(
    ctx: RunContext[DeepSearchDeps],
    question: str,
) -> str:
    """Ask the user a clarifying question.

    Emits SSE ask_user event. Currently a stub that returns a fixed reply.
    Use only when the question is genuinely ambiguous.

    Args:
        question: Arabic clarifying question for the user.
    """
    ctx.deps._sse_events.append({
        "type": "ask_user",
        "question": question,
    })
    logger.info("ask_user: %s", question[:80])

    # Stub: return fixed reply (real pause/resume is a future enhancement)
    return "يرجى المتابعة بناءً على أفضل تقدير لديك."


@plan_agent.tool
async def quick_search(
    ctx: RunContext[DeepSearchDeps],
    query: str,
) -> str:
    """Direct lookup without running the full search loop.

    OUT OF SCOPE -- stub only. Raises NotImplementedError.

    Args:
        query: Arabic search query for direct lookup.
    """
    raise NotImplementedError("quick_search is not implemented yet")
