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

from agents.utils.tracking import track_stage
from shared.observability import get_logfire

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
_logfire = get_logfire()


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
        rationale=f"planner_error_fallback: {reason}",
    )


_DEGRADED_SYNTHESIS_DIGEST_CHARS = 500


def _response_from_artifact(agg_output: "AggregatorOutput") -> PlannerResponse:
    """Phase-3 fallback when the artifact exists but the responder LLM failed.

    Uses the synthesis body's head as the chat content. The outer orchestrator
    re-runs the artifact_summarizer on the published item and overrides
    ``SpecialistResult.chat_summary`` with a proper coverage summary, so this
    fallback only surfaces when both the responder AND the summarizer fail.

    Defaults ``build_artifact=True`` (the artifact already exists — publish it).
    """
    synthesis = (getattr(agg_output, "synthesis_md", "") or "").strip()
    head = synthesis[:_DEGRADED_SYNTHESIS_DIGEST_CHARS]
    return PlannerResponse(
        chat_summary_md=head or "اكتمل البحث؛ التفاصيل والمراجع في بطاقة البحث.",
        suggestion_md="",
    )


def _minimal_response(reason: str) -> PlannerResponse:
    """Phase-2 fallback when retrieval failed entirely — honest, no fabrication.

    Phase E: sets ``build_artifact=False`` because there is no aggregator output
    to publish — the orchestrator's publish branch must be skipped.
    """
    logger.warning("planner: minimal degraded response (%s)", reason)
    return PlannerResponse(
        chat_summary_md=(
            "تعذّر إكمال البحث القانوني في هذه المحاولة. يُرجى إعادة المحاولة، "
            "وإذا تكرر الأمر جرّب إعادة صياغة السؤال."
        ),
        suggestion_md="",
        build_artifact=False,
    )


# ---------------------------------------------------------------------------
# Phase helpers
# ---------------------------------------------------------------------------


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
    describe_query: str,
    deps: PlannerDeps,
    decision: PlannerDecision | None = None,
    *,
    run_retrieval: Callable | None = None,
) -> PlannerTurnResult:
    """Public entry point — wraps :func:`_run_planner_turn` in a Logfire span.

    The ``deep_search.planner`` span makes the planner a visible stage in the
    trace tree (peer to ``deep_search.aggregator``); without it the planner's
    phase-1/phase-3 LLM calls are indistinguishable httpx ``POST`` spans. The
    span never changes behaviour — ``_run_planner_turn`` still never raises.

    ``describe_query`` is the router-emitted query description (Wave 1/Phase B
    redesign — renamed from positional ``briefing`` in Phase C).
    """
    with track_stage(
        "deep_search.planner",
        conversation_id=getattr(deps, "conversation_id", "") or None,
        agent_family="deep_search",
        query_id=getattr(deps, "query_id", None),
        resumed=decision is not None,
    ) as span:
        result = await _run_planner_turn(
            describe_query, deps, decision, run_retrieval=run_retrieval,
        )
        span.set(kind=result.kind, degraded=result.degraded)
        if result.decision is not None:
            span.set(mode=result.decision.mode, support=result.decision.support)
        return result


def _count_workspace_reads(events: list[dict]) -> int:
    """Count ``read_workspace_item`` tool_call events emitted onto ``deps._events``.

    Used by the logger payload (§3.8) to record how many prior artifacts the
    decider opened during phase 1 — split from ``ask_user_invoked`` so a
    pathological «planner keeps reading» pattern is distinguishable from a
    «planner asked one clarifying question» pattern.
    """
    return sum(
        1 for e in events
        if e.get("type") == "tool_call" and e.get("tool") == "read_workspace_item"
    )


def _ask_user_was_invoked(events: list[dict]) -> bool:
    """True iff at least one ``ask_user`` tool_call event was emitted.

    The agent infrastructure does not emit a ``tool_call`` event for
    ``ask_user`` itself (it raises ``CallDeferred`` and pauses), so we infer the
    invocation from the planner_paused lifecycle event when present.
    """
    return any(e.get("event") == EVENT_PAUSED for e in events)


async def _run_planner_turn(
    describe_query: str,
    deps: PlannerDeps,
    decision: PlannerDecision | None = None,
    *,
    run_retrieval: Callable | None = None,
) -> PlannerTurnResult:
    """Run the planner-driven loop for one turn.

    Args:
        describe_query: the user's query (raw text). Renamed from ``briefing``
            in Phase C of the redesign — mechanical rename only.
        deps: phase 1-3 runtime deps — built fresh by ``build_planner_deps``.
            Phase C: deps now carry the comprehension surface (case_brief,
            recent_messages, prior_searches, attached_items) AND user_id /
            conversation_id; the decider's dynamic instructions read these.
        decision: when supplied (resume path) phase 1 is skipped; when ``None``
            (fresh dispatch) phase 1 runs and may pause via ``ask_user``.
        run_retrieval: optional injected retrieval coroutine — for tests. When
            ``None`` the real ``run_retrieval`` is lazy-imported.

    Returns:
        :class:`PlannerTurnResult` — ``kind="completed"`` or ``kind="paused"``.
        Never raises.
    """
    query = describe_query or ""

    # ── PHASE 1 — decide ───────────────────────────────────────────────────
    if decision is None:
        from pydantic_ai import DeferredToolRequests  # lazy — pydantic_ai dep

        t0 = time.perf_counter()
        try:
            decider = create_planner_decider(model_override=deps.model_override)
            # Phase C: decider's deps_type flipped from None → PlannerDeps.
            # Pass deps so the dynamic instructions render comprehension blocks
            # AND read_workspace_item has supabase/user_id/conversation_id.
            result = await decider.run(
                build_decider_user_message(query),
                deps=deps,
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
            _decided_payload = {
                "event": EVENT_DECIDED,
                "mode": decision.mode,
                "support": decision.support,
                "planner_brief_chars": len(getattr(decision, "planner_brief", "") or ""),
                "query_restatement_chars": len(getattr(decision, "query_restatement", "") or ""),
                "context_labels": list(getattr(decision, "context_labels", []) or []),
                "workspace_reads_count": _count_workspace_reads(deps._events),
                "ask_user_invoked": False,  # paused-branch returned above
                "duration_s": round(time.perf_counter() - t0, 3),
            }
            emit(deps, _decided_payload)
            # §3.8 observability — surface the EVENT_DECIDED payload as
            # attributes on the active `deep_search.planner` span so
            # dashboards can filter/alert on them. Telemetry must never
            # break the run, hence the broad except.
            try:
                _span = _logfire.current_span()
                if _span is not None:
                    _span.set_attributes({
                        "planner.mode": _decided_payload["mode"],
                        "planner.support": _decided_payload["support"],
                        "planner.planner_brief_chars": _decided_payload["planner_brief_chars"],
                        "planner.context_labels": _decided_payload["context_labels"],
                        "planner.workspace_reads_count": _decided_payload["workspace_reads_count"],
                        "planner.ask_user_invoked": _decided_payload["ask_user_invoked"],
                    })
            except Exception:
                pass
            logger.info(
                "planner: decided mode=%s support=%s brief_chars=%d labels=%s reads=%d",
                decision.mode, decision.support,
                len(getattr(decision, "planner_brief", "") or ""),
                list(getattr(decision, "context_labels", []) or []),
                _count_workspace_reads(deps._events),
            )
    else:
        # Resume path — decision already resolved by the orchestrator. The
        # decider's read_workspace_item tool-call events (if any) lived on the
        # PRIOR turn's deps; this turn's deps._events is fresh, so the resume
        # read-back records 0 reads (correct — no new reads happened here).
        _decided_payload = {
            "event": EVENT_DECIDED,
            "mode": decision.mode,
            "support": decision.support,
            "planner_brief_chars": len(getattr(decision, "planner_brief", "") or ""),
            "query_restatement_chars": len(getattr(decision, "query_restatement", "") or ""),
            "context_labels": list(getattr(decision, "context_labels", []) or []),
            "workspace_reads_count": _count_workspace_reads(deps._events),
            "ask_user_invoked": _ask_user_was_invoked(deps._events),
            "resumed": True,
        }
        emit(deps, _decided_payload)
        # §3.8 — same span-attribute surface on the resume path. Same
        # try/except guard.
        try:
            _span = _logfire.current_span()
            if _span is not None:
                _span.set_attributes({
                    "planner.mode": _decided_payload["mode"],
                    "planner.support": _decided_payload["support"],
                    "planner.planner_brief_chars": _decided_payload["planner_brief_chars"],
                    "planner.context_labels": _decided_payload["context_labels"],
                    "planner.workspace_reads_count": _decided_payload["workspace_reads_count"],
                    "planner.ask_user_invoked": _decided_payload["ask_user_invoked"],
                })
        except Exception:
            pass

    deps._decision = decision

    # ── PHASE 2 — retrieve ─────────────────────────────────────────────────
    config = build_retrieval_config(decision)
    # The planner's faithful, zero-bias restatement (when produced) is the
    # canonical retrieval query — it resolves colloquial / rambling phrasing
    # into a clean statement of the user's real question and legal posture,
    # WITHOUT injecting any law or entity the user didn't name. This is what
    # flows to the sector_picker, the executors, and the aggregator. Falls back
    # to the raw query when the planner left it empty (query already clean).
    retrieval_query = (
        getattr(decision, "query_restatement", "") or ""
    ).strip() or query
    agg_output: "AggregatorOutput | None" = None
    degraded = False
    t1 = time.perf_counter()
    try:
        retrieval_fn = await _resolve_run_retrieval(run_retrieval)
        agg_output = await retrieval_fn(retrieval_query, config, deps)
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
            # Phase E (§3.8): publish-gate fields replace the dropped
            # `suggested_action` enum. `build_artifact` is the orchestrator's
            # branch input; `referenced_item_id` is the SSE payload for the
            # «referenced_existing_item» event when present.
            "build_artifact": response.build_artifact,
            "referenced_item_id": response.referenced_item_id,
            "duration_s": round(time.perf_counter() - t2, 3),
        })
        # §3.8 observability — surface the EVENT_RESPONDED payload as
        # attributes on the active `deep_search.planner` span. Note
        # `referenced_item_id` is post-coercion (validator on
        # PlannerResponse normalises 'None'/'null'/'' → actual None).
        try:
            _span = _logfire.current_span()
            if _span is not None:
                _span.set_attributes({
                    "planner.build_artifact": response.build_artifact,
                    "planner.referenced_item_id": response.referenced_item_id,
                    "planner.chat_summary_chars": len(response.chat_summary_md or ""),
                    "planner.suggestion_chars": len(response.suggestion_md or ""),
                })
        except Exception:
            pass
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
