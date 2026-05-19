"""Two-phase runner for the planner-driven loop.

:func:`handle_planner_turn` is the single convergence point for phases 2–3 —
both fresh dispatch (``decision=None`` → phase 1 runs here) and the resume path
(``decision`` supplied → phase 1 skipped) enter it. See PLANNER_REDESIGN_PLAN.md
§2, §9, §14.

Flow:

    PHASE 1 — decide    planner_decider.run → PlannerDecision | DeferredToolRequests
    PHASE 2 — retrieve  run_retrieval(query, config, deps) → AggregatorOutput
    PHASE 3 — respond   planner_responder.run → PlannerResponse

The runner **never raises** — every phase has a degraded fallback (§9). A
phase-1 ``DeferredToolRequests`` is a normal pause, not a failure: it is
returned to the orchestrator as ``kind="paused"`` and must not be caught into a
fallback.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Literal

from agents.deep_search_v4.shared.sector_vocab.regulations import canonicalize_sectors

from .agent import (
    PLANNER_DECIDER_LIMITS,
    PLANNER_RESPONDER_LIMITS,
    create_planner_decider,
    create_planner_responder,
)
from .apply import build_retrieval_config
from .deps import PlannerDeps
from .logger import (
    EVENT_DECIDED,
    EVENT_ERROR,
    EVENT_PAUSED,
    EVENT_RESPONDED,
    EVENT_RETRIEVAL_DONE,
    emit,
)
from .models import PlannerDecision, PlannerResponse
from .prompts import build_decider_user_message, build_responder_user_message

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pydantic_ai import DeferredToolRequests

    from agents.deep_search_v4.aggregator.models import AggregatorOutput

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result contract
# ---------------------------------------------------------------------------


@dataclass
class PlannerTurnResult:
    """What :func:`handle_planner_turn` returns.

    - ``kind="completed"`` — phases 2–3 ran. ``response`` / ``decision`` /
      ``agg_output`` are set; the orchestrator publishes the artifact and maps
      ``response`` onto a ``SpecialistResult``.
    - ``kind="paused"`` — phase 1 called ``ask_user``. ``planner_result`` (the
      raw ``AgentRunResult``) and ``deferred`` carry the pause state for the
      orchestrator's ``_record_deferred``.

    ``aborted`` is never produced here — a post-resume off-script reply is
    detected by the orchestrator when it resumes ``planner_decider`` itself,
    before ``handle_planner_turn`` is called.
    """

    kind: Literal["completed", "paused"]
    # completed
    response: PlannerResponse | None = None
    decision: PlannerDecision | None = None
    agg_output: "AggregatorOutput | None" = None
    degraded: bool = False
    # paused
    planner_result: Any = None
    deferred: "DeferredToolRequests | None" = None


# ---------------------------------------------------------------------------
# Degraded fallbacks (§9)
# ---------------------------------------------------------------------------

_DEFAULT_DECISION_RATIONALE = "planner_error_fallback: phase-1 raised; defaulted to reg_led."


def _default_decision(reason: str) -> PlannerDecision:
    """Safe default plan when phase 1 raises (§9): reg_led, no support."""
    return PlannerDecision(
        mode="reg_led",
        support=False,
        sectors=None,
        rationale=f"planner_error_fallback: {reason}",
    )


def _response_from_artifact(agg_output: "AggregatorOutput") -> PlannerResponse:
    """Phase-3 fallback when the artifact exists but the responder LLM failed."""
    return PlannerResponse(
        chat_summary_md=(getattr(agg_output, "chat_summary", "") or "").strip()
        or "اكتمل البحث؛ التفاصيل والمراجع في بطاقة البحث.",
        suggestion_md="",
        suggested_action="none",
    )


def _minimal_response(reason: str) -> PlannerResponse:
    """Phase-2 fallback when retrieval failed entirely — honest, no fabrication."""
    logger.warning("planner: minimal degraded response (%s)", reason)
    return PlannerResponse(
        chat_summary_md=(
            "تعذّر إكمال البحث القانوني في هذه المحاولة. يُرجى إعادة المحاولة، "
            "وإذا تكرر الأمر جرّب إعادة صياغة السؤال."
        ),
        suggestion_md="",
        suggested_action="none",
    )


# ---------------------------------------------------------------------------
# Phase helpers
# ---------------------------------------------------------------------------


def _canonicalize_decision_sectors(decision: PlannerDecision) -> None:
    """Canonicalize ``decision.sectors`` against VALID_SECTORS in place."""
    if not decision.sectors:
        return
    canonical = canonicalize_sectors(decision.sectors)
    if canonical != decision.sectors:
        logger.info(
            "planner: canonicalized sectors %s -> %s", decision.sectors, canonical,
        )
    decision.sectors = canonical or None


async def _resolve_run_retrieval(injected: Callable | None) -> Callable:
    """Return the ``run_retrieval`` coroutine — injected (tests) or lazy-imported.

    The lazy import breaks the planner → deep_search_v4.orchestrator import
    cycle (PLANNER_REDESIGN_PLAN.md §6 Blocking 1).
    """
    if injected is not None:
        return injected
    from agents.deep_search_v4.orchestrator import run_retrieval  # lazy

    return run_retrieval


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def handle_planner_turn(
    briefing: str,
    deps: PlannerDeps,
    decision: PlannerDecision | None = None,
    *,
    run_retrieval: Callable | None = None,
) -> PlannerTurnResult:
    """Run the planner-driven loop for one turn.

    Args:
        briefing: the user's query (raw text).
        deps: phase 2–3 runtime deps — built fresh by ``build_planner_deps``.
        decision: when supplied (resume path) phase 1 is skipped; when ``None``
            (fresh dispatch) phase 1 runs and may pause via ``ask_user``.
        run_retrieval: optional injected retrieval coroutine — for tests. When
            ``None`` the real ``run_retrieval`` is lazy-imported.

    Returns:
        :class:`PlannerTurnResult` — ``kind="completed"`` or ``kind="paused"``.
        Never raises.
    """
    query = briefing or ""

    # ── PHASE 1 — decide ───────────────────────────────────────────────────
    if decision is None:
        from pydantic_ai import DeferredToolRequests  # lazy — pydantic_ai dep

        t0 = time.perf_counter()
        try:
            decider = create_planner_decider(model_override=deps.model_override)
            result = await decider.run(
                build_decider_user_message(query),
                usage_limits=PLANNER_DECIDER_LIMITS,
            )
            output = result.output
        except Exception as exc:  # phase 1 raised → safe default (§9)
            logger.warning(
                "planner: phase-1 decider raised (%s); using default decision",
                exc, exc_info=True,
            )
            emit(deps, {"event": EVENT_ERROR, "phase": "decide", "error": str(exc)})
            decision = _default_decision(type(exc).__name__)
        else:
            if isinstance(output, DeferredToolRequests):
                # Normal pause — NOT a failure. Hand back to the orchestrator.
                emit(deps, {"event": EVENT_PAUSED})
                logger.info("planner: phase-1 paused via ask_user")
                return PlannerTurnResult(
                    kind="paused", planner_result=result, deferred=output,
                )
            # PlannerDecision in hand.
            decision = output
            _canonicalize_decision_sectors(decision)
            emit(deps, {
                "event": EVENT_DECIDED,
                "mode": decision.mode,
                "support": decision.support,
                "sectors": list(decision.sectors or []),
                "duration_s": round(time.perf_counter() - t0, 3),
            })
            logger.info(
                "planner: decided mode=%s support=%s sectors=%s",
                decision.mode, decision.support, decision.sectors,
            )
    else:
        # Resume path — decision already resolved by the orchestrator.
        _canonicalize_decision_sectors(decision)
        emit(deps, {
            "event": EVENT_DECIDED,
            "mode": decision.mode,
            "support": decision.support,
            "sectors": list(decision.sectors or []),
            "resumed": True,
        })

    deps._decision = decision

    # ── PHASE 2 — retrieve ─────────────────────────────────────────────────
    config = build_retrieval_config(decision)
    agg_output: "AggregatorOutput | None" = None
    degraded = False
    t1 = time.perf_counter()
    try:
        retrieval_fn = await _resolve_run_retrieval(run_retrieval)
        agg_output = await retrieval_fn(query, config, deps)
        deps._agg_output = agg_output
        emit(deps, {
            "event": EVENT_RETRIEVAL_DONE,
            "mode": decision.mode,
            "confidence": getattr(agg_output, "confidence", None),
            "duration_s": round(time.perf_counter() - t1, 3),
        })
    except Exception as exc:  # phase 2 raised → minimal honest response (§9)
        degraded = True
        logger.error(
            "planner: phase-2 retrieval raised (%s); degraded response",
            exc, exc_info=True,
        )
        emit(deps, {"event": EVENT_ERROR, "phase": "retrieve", "error": str(exc)})
        return PlannerTurnResult(
            kind="completed",
            response=_minimal_response(type(exc).__name__),
            decision=decision,
            agg_output=None,
            degraded=True,
        )

    # ── PHASE 3 — respond ──────────────────────────────────────────────────
    t2 = time.perf_counter()
    try:
        responder = create_planner_responder(model_override=deps.model_override)
        result = await responder.run(
            build_responder_user_message(query),
            deps=deps,
            usage_limits=PLANNER_RESPONDER_LIMITS,
        )
        response: PlannerResponse = result.output
        emit(deps, {
            "event": EVENT_RESPONDED,
            "suggested_action": response.suggested_action,
            "duration_s": round(time.perf_counter() - t2, 3),
        })
    except Exception as exc:  # phase 3 raised → reuse the artifact (§9)
        degraded = True
        logger.error(
            "planner: phase-3 responder raised (%s); response from artifact",
            exc, exc_info=True,
        )
        emit(deps, {"event": EVENT_ERROR, "phase": "respond", "error": str(exc)})
        response = (
            _response_from_artifact(agg_output)
            if agg_output is not None
            else _minimal_response(type(exc).__name__)
        )

    return PlannerTurnResult(
        kind="completed",
        response=response,
        decision=decision,
        agg_output=agg_output,
        degraded=degraded,
    )


__all__ = ["PlannerTurnResult", "handle_planner_turn"]
