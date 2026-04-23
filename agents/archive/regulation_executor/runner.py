"""Turn runner for regulation_executor agent."""
from __future__ import annotations

import asyncio
import logging
import time as _time
from datetime import datetime, timezone

from pydantic_ai.exceptions import ModelHTTPError
from pydantic_graph import End

from .agent import ExecutorResult, regulation_executor, EXECUTOR_LIMITS
from .deps import RegulationSearchDeps
from .logger import LOGS_DIR, save_run_log, save_planner_md
from .tools import run_retrieval_pipeline

logger = logging.getLogger(__name__)

ERROR_MSG_AR = "عذراً، حدث خطأ أثناء البحث في الأنظمة. يرجى المحاولة مرة أخرى."

# ── Experiment models ────────────────────────────────────────────────────────
# Every query runs on BOTH models for comparison.
# Default (returned to caller): gemini-3-flash
# Experiment: minimax-m2.7-highspeed via OpenRouter
# Last-resort fallback: minimax-m2.7-fp8 via OpenRouter

EXPERIMENT_MODEL = "or-minimax-m2.7"       # MiniMax M2.7 via OpenRouter
FALLBACK_MODEL = "or-gemini-3.1-pro-tools" # OpenRouter Gemini 3.1 as last resort


def _format_result(result: ExecutorResult) -> str:
    """Format ExecutorResult into a markdown string for the planner."""
    lines: list[str] = []

    quality_ar = {
        "strong": "قوية",
        "moderate": "متوسطة",
        "weak": "ضعيفة",
    }
    lines.append("## نتائج البحث في الأنظمة")
    lines.append(f"**الجودة: {quality_ar.get(result.quality, result.quality)}**\n")

    lines.append(result.summary_md)

    if result.citations:
        lines.append("\n---")
        lines.append("**المصادر:**")
        for c in result.citations:
            parts = [c.ref]
            if c.title:
                parts.append(c.title)
            if c.regulation_title:
                parts.append(f"({c.regulation_title})")
            if c.article_num:
                parts.append(f"المادة {c.article_num}")
            lines.append(f"- {' | '.join(parts)}")

    return "\n".join(lines)


async def _run_single(
    query: str,
    deps: RegulationSearchDeps,
    model_override=None,
    model_name: str = "or-gemini-2.5-flash",
    prefetched_results: str | None = None,
) -> tuple[str, ExecutorResult | None, dict | None, bytes | None, float, str | None]:
    """Run the agent once. Returns (formatted, output, usage, messages_json, duration, error)."""
    start = _time.time()
    # Each run needs its own events list to avoid cross-contamination
    from dataclasses import replace
    run_deps = replace(deps, _events=[], _retrieval_logs=[])

    kwargs = dict(deps=run_deps, usage_limits=EXECUTOR_LIMITS)
    if model_override is not None:
        kwargs["model"] = model_override

    # Build user message: query + pre-fetched results (if available)
    if prefetched_results:
        user_message = (
            f"{query}\n\n"
            f"---\n"
            f"## نتائج البحث المسترجعة مسبقاً\n\n"
            f"{prefetched_results}"
        )
    else:
        user_message = query

    try:
        async with regulation_executor.iter(user_message, **kwargs) as run:
            node = run.next_node
            while not isinstance(node, End):
                node = await run.next(node)

        output: ExecutorResult = run.result.output
        usage_obj = run.usage()
        duration = _time.time() - start

        formatted = _format_result(output)
        usage = {
            "requests": usage_obj.requests,
            "input_tokens": usage_obj.input_tokens,
            "output_tokens": usage_obj.output_tokens,
            "total_tokens": usage_obj.total_tokens,
            "tool_calls": usage_obj.tool_calls,
        }

        logger.info(
            "regulation_executor [%s] -- tokens=%s, quality=%s, %.1fs",
            model_name, usage_obj.total_tokens, output.quality, duration,
        )

        return formatted, output, usage, run.all_messages_json(), duration, None

    except Exception as e:
        duration = _time.time() - start
        logger.warning("regulation_executor [%s] failed (%.1fs): %s", model_name, duration, e)
        return "", None, None, None, duration, f"{type(e).__name__}: {e}"


async def run_regulation_search(
    query: str,
    deps: RegulationSearchDeps,
) -> str:
    """Run regulation search in two phases:

    Phase 1: Mechanical retrieval (0 LLM calls) — runs the pipeline ONCE.
    Phase 2: Dual-model synthesis — both gemini and minimax receive the
             same pre-fetched results, saving 1 LLM call each.
    """
    from agents.model_registry import create_model

    log_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    run_dir = LOGS_DIR / log_id
    deps._events = []
    deps._retrieval_logs = []

    total_start = _time.time()

    # ── Phase 1: Mechanical retrieval (shared across both models) ───────────
    retrieval_start = _time.time()
    try:
        prefetched_results = await run_retrieval_pipeline(query, deps)
    except Exception as e:
        logger.error("Mechanical retrieval failed: %s", e, exc_info=True)
        save_run_log(
            log_id=f"{log_id}_retrieval_failed",
            query=query,
            duration_s=_time.time() - retrieval_start,
            error=f"Retrieval failed: {type(e).__name__}: {e}",
            model_used="none",
            retrieval_logs=list(deps._retrieval_logs),
            run_dir=run_dir,
        )
        return ERROR_MSG_AR

    retrieval_duration = _time.time() - retrieval_start
    retrieval_logs_snapshot = list(deps._retrieval_logs)

    logger.info(
        "Mechanical retrieval complete in %.1fs, result length=%d",
        retrieval_duration, len(prefetched_results),
    )

    # Capture retrieval SSE events, then reset for synthesis phase
    retrieval_events = list(deps._events)
    deps._events = []

    # ── Phase 2: Dual-model synthesis (both get same pre-fetched results) ───
    minimax_model = create_model(EXPERIMENT_MODEL)

    gemini_task = _run_single(
        query, deps, model_override=None, model_name="or-gemini-2.5-flash",
        prefetched_results=prefetched_results,
    )
    minimax_task = _run_single(
        query, deps, model_override=minimax_model, model_name=EXPERIMENT_MODEL,
        prefetched_results=prefetched_results,
    )

    results = await asyncio.gather(gemini_task, minimax_task, return_exceptions=True)

    # Unpack results
    gemini = results[0] if not isinstance(results[0], Exception) else ("", None, None, None, 0, str(results[0]))
    minimax = results[1] if not isinstance(results[1], Exception) else ("", None, None, None, 0, str(results[1]))

    g_formatted, g_output, g_usage, g_messages, g_duration, g_error = gemini
    m_formatted, m_output, m_usage, m_messages, m_duration, m_error = minimax

    # Restore retrieval SSE events so the caller can read them
    deps._events = retrieval_events

    # Log BOTH results into the shared run folder
    if g_output or g_error:
        save_run_log(
            log_id=log_id,
            query=query,
            duration_s=g_duration,
            usage=g_usage,
            agent_output=g_output,
            model_messages_json=g_messages,
            formatted_result=g_formatted or None,
            error=g_error,
            model_used="or-gemini-2.5-flash",
            retrieval_logs=retrieval_logs_snapshot,
            run_dir=run_dir,
        )

    if m_output or m_error:
        save_run_log(
            log_id=log_id,
            query=query,
            duration_s=m_duration,
            usage=m_usage,
            agent_output=m_output,
            model_messages_json=m_messages,
            formatted_result=m_formatted or None,
            error=m_error,
            model_used=EXPERIMENT_MODEL,
            retrieval_logs=retrieval_logs_snapshot,
            run_dir=run_dir,
        )

    # Determine chosen model and build models summary for planner.md
    chosen_model = "none"
    fallback_summary = None

    if g_output and g_formatted:
        chosen_model = "or-gemini-2.5-flash"
    elif m_output and m_formatted:
        chosen_model = EXPERIMENT_MODEL
    # Both failed — try fp8 as last resort
    else:
        logger.warning("Both gemini and minimax failed. Trying fp8 fallback...")
        try:
            fp8_model = create_model(FALLBACK_MODEL)
            f_formatted, f_output, f_usage, f_messages, f_duration, f_error = await _run_single(
                query, deps, model_override=fp8_model, model_name=FALLBACK_MODEL,
                prefetched_results=prefetched_results,
            )
            save_run_log(
                log_id=log_id,
                query=query,
                duration_s=f_duration,
                usage=f_usage,
                agent_output=f_output,
                model_messages_json=f_messages,
                formatted_result=f_formatted or None,
                error=f_error,
                model_used=FALLBACK_MODEL,
                retrieval_logs=retrieval_logs_snapshot,
                run_dir=run_dir,
            )
            if f_output and f_formatted:
                chosen_model = FALLBACK_MODEL
                fallback_summary = {
                    "model": FALLBACK_MODEL,
                    "duration_s": round(f_duration, 2),
                    "quality": f_output.quality if f_output else "?",
                    "total_tokens": f_usage.get("total_tokens") if f_usage else "?",
                    "error": f_error,
                }
        except Exception as e:
            logger.error("FP8 fallback also failed: %s", e)

    # Build models summary
    models_summary = []
    models_summary.append({
        "model": "or-gemini-2.5-flash",
        "duration_s": round(g_duration, 2),
        "quality": g_output.quality if g_output else "?",
        "total_tokens": g_usage.get("total_tokens") if g_usage else "?",
        "error": g_error,
    })
    models_summary.append({
        "model": EXPERIMENT_MODEL,
        "duration_s": round(m_duration, 2),
        "quality": m_output.quality if m_output else "?",
        "total_tokens": m_usage.get("total_tokens") if m_usage else "?",
        "error": m_error,
    })
    if fallback_summary:
        models_summary.append(fallback_summary)

    total_duration = _time.time() - total_start

    # Write planner.md (orchestration trace)
    save_planner_md(
        run_dir=run_dir,
        log_id=log_id,
        query=query,
        retrieval_duration_s=retrieval_duration,
        retrieval_logs=retrieval_logs_snapshot,
        models_summary=models_summary,
        total_duration_s=total_duration,
        chosen_model=chosen_model,
        error=None if chosen_model != "none" else "All models failed",
    )

    # Return the chosen result
    if chosen_model == "or-gemini-2.5-flash" and g_formatted:
        return g_formatted
    if chosen_model == EXPERIMENT_MODEL and m_formatted:
        return m_formatted
    if chosen_model == FALLBACK_MODEL:
        # fallback_summary exists if we got here
        return f_formatted  # noqa: F821 — set in the fallback block above

    # Everything failed
    save_run_log(
        log_id=log_id,
        query=query,
        duration_s=g_duration + m_duration,
        error=f"All models failed. Gemini: {g_error}. MiniMax: {m_error}",
        model_used="all_failed",
        retrieval_logs=retrieval_logs_snapshot,
        run_dir=run_dir,
    )
    return ERROR_MSG_AR
