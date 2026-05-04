"""Runner for the v4 Planner agent.

Single async function :func:`run_planner` — wraps the agent run with timing,
event emission (both into ``deps._events`` and the optional ``emit_sse``
callback), and a degraded fallback so the orchestrator never crashes when the
planner LLM call fails. Per V4_PLANNER_DESIGN.md §10 Q5 the fallback is
``mode="all"`` with default caps and ``aggregator_prompt_key="prompt_1"``.
"""
from __future__ import annotations

import logging
import time

from agents.deep_search_v4.shared.sector_vocab.regulations import canonicalize_sectors

from .agent import create_planner_agent, PLANNER_DEFAULT_MODEL
from .models import PlannerDeps, PlannerOutput
from .prompts import build_planner_user_message

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fallback plan — invoked when the planner LLM call fails. Per
# V4_PLANNER_DESIGN.md §10 Q5: invoke all three executors with default focus
# and no sector filter. Matches the existing FullLoopDeps defaults so behavior
# degrades to the pre-planner baseline.
# ---------------------------------------------------------------------------


def _emit(deps: PlannerDeps, event: dict) -> None:
    """Mirror the aggregator's emit pattern — append + optional SSE callback."""
    deps._events.append(event)
    if deps.emit_sse is not None:
        try:
            deps.emit_sse(event)
        except Exception:  # pragma: no cover - defensive
            logger.warning("planner: emit_sse callback raised", exc_info=True)


def _fallback_plan(reason: str) -> PlannerOutput:
    """Safe full-triangulation plan with default focus when the LLM fails."""
    return PlannerOutput(
        invoke=["reg", "compliance", "cases"],
        focus={"reg": "default", "compliance": "default", "cases": "default"},
        sectors=None,
        rationale=f"planner_error_fallback: {reason}",
    )


async def run_planner(query: str, deps: PlannerDeps) -> PlannerOutput:
    """Execute one planner LLM call and return a validated :class:`PlannerOutput`.

    Emits ``planner_start`` / ``planner_done`` (or ``planner_error``) events on
    ``deps._events`` and the optional ``deps.emit_sse`` callback. Never raises
    — degrades to a safe fallback plan instead.
    """
    model_name = deps.model_override or PLANNER_DEFAULT_MODEL
    _emit(deps, {"event": "planner_start", "model": model_name})

    user_message = build_planner_user_message(query)
    t0 = time.perf_counter()

    try:
        agent = create_planner_agent(model_name=deps.model_override)
        result = await agent.run(user_message)
        output: PlannerOutput = result.output
    except Exception as exc:
        duration = time.perf_counter() - t0
        logger.warning(
            "planner: LLM call failed after %.2fs (%s); using degraded fallback",
            duration,
            exc,
            exc_info=True,
        )
        fallback = _fallback_plan(reason=type(exc).__name__)
        _emit(
            deps,
            {
                "event": "planner_error",
                "model": model_name,
                "duration_s": round(duration, 3),
                "error": str(exc),
                "fallback_invoke": list(fallback.invoke),
            },
        )
        return fallback

    # Canonicalize sectors against the VALID_SECTORS vocabulary — catches
    # near-misses ("السياحة" → "السياحة والترفيه") and drops invalid names.
    if output.sectors:
        canonical = canonicalize_sectors(output.sectors)
        if canonical != output.sectors:
            logger.info(
                "planner: canonicalized sectors %s -> %s",
                output.sectors, canonical,
            )
        output.sectors = canonical or None

    duration = time.perf_counter() - t0
    _emit(
        deps,
        {
            "event": "planner_done",
            "invoke": list(output.invoke),
            "focus": dict(output.focus),
            "model": model_name,
            "duration_s": round(duration, 3),
        },
    )
    logger.info(
        "planner: invoke=%s focus=%s sectors=%s duration=%.2fs",
        sorted(output.invoke),
        {k: output.focus[k] for k in sorted(output.focus)},
        output.sectors,
        duration,
    )
    return output


__all__ = ["run_planner"]
