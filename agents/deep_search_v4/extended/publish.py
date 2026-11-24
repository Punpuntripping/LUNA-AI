"""«بحث موسّع» — publish the reports and the merged answer; write the chat summary (§4.7, D14).

- Every usable report → its own ``agent_search`` workspace item through today's
  publisher (``publish_search_result``): title = the request's title, its own
  references, forensic rows (``retrieval_artifacts`` + ``reranker_runs``) from its own
  URA — exactly what an ordinary deep search stores.
- The merged answer → one more ``agent_search`` item, written here (no URA of its own,
  so no forensic rows): its text ends with the code-written list of the reports it was
  built from («التقارير التي بُني عليها هذا الجواب: …», D14); ``metadata.children`` =
  the report item ids (the hook for the workspace-item relationships plan) and
  ``metadata.extended_search = {run_id, titles, resolved}``; its references go to
  ``workspace_item_references`` like the publisher's.
- One report usable (or ``merged`` is ``None``) → the reports only, no merged item.

A failed publish never stops the others; any failure adds today's Arabic status line.
وضع السرية: the merged text, its gaps and its ``resolved`` are model text in the encoded
space — decoded with ``decode_for_persist`` before storing (store-real invariant), as
the publisher does for a report.

:func:`write_chat_summary` feeds the merged answer to the responder today's path would
use (unified or legacy, ``RESPONDER_UNIFIED_FAMILIES``) and returns the same
``PlannerTurnResult`` the orchestrator maps onto a ``SpecialistResult``.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from agents.utils.tracking import track_stage

from .models import ExtendedPlan, ExtendedSearchReport, MergedAnswer

logger = logging.getLogger(__name__)

#: D14 — the heading of the code-written list at the end of the merged answer.
REPORTS_HEADING_AR = "التقارير التي بُني عليها هذا الجواب:"
#: Same wording as ``_run_deep_search``'s publish-failure line.
PUBLISH_FAILED_AR = (
    "تعذّر حفظ نتيجة البحث كبطاقة في مساحة العمل — "
    "الإجابة معروضة في المحادثة لكنها لن تظهر ضمن المستندات."
)
#: Title of the merged item when the wiring passes no ``task_label``.
MERGED_TITLE_PREFIX_AR = "بحث موسّع"
MERGED_PROMPT_KEY = "extended_merger"
_TITLE_MAX = 80


@dataclass
class PublishedExtended:
    report_item_ids: list[str]
    merged_item_id: str | None
    sse_events: list[dict] = field(default_factory=list)


# --------------------------------------------------------------------------- helpers


def reports_footer(titles: Sequence[str]) -> str:
    """D14: «التقارير التي بُني عليها هذا الجواب:» + a numbered list (Latin digits)."""
    titles = [t.strip() for t in titles if (t or "").strip()]
    if not titles:
        return ""
    return "\n".join([f"**{REPORTS_HEADING_AR}**", *(f"{i}. {t}" for i, t in enumerate(titles, 1))])


def _usable(reports: Sequence[ExtendedSearchReport]) -> list[ExtendedSearchReport]:
    return sorted((r for r in reports if r.ok and r.agg_output is not None), key=lambda r: r.index)


def merged_agg_output(
    merged: MergedAnswer,
    *,
    disclaimer_ar: str = "",
    model_used: str = "",
) -> Any:
    """The merged answer as an ``AggregatorOutput`` (what the responders and the
    reference writer read). A reference that no longer validates is dropped."""
    from agents.deep_search_v4.aggregator.models import AggregatorOutput, Reference, ValidationReport

    refs: list[Reference] = []
    for raw in merged.references:
        try:
            refs.append(Reference.model_validate(raw))
        except Exception:  # noqa: BLE001
            logger.warning("extended_search: merged reference %s dropped (invalid)", raw.get("n"), exc_info=True)
    return AggregatorOutput(
        synthesis_md=merged.synthesis_md,
        references=refs,
        confidence=merged.confidence,
        gaps=list(merged.gaps),
        disclaimer_ar=disclaimer_ar,
        prompt_key=MERGED_PROMPT_KEY,
        model_used=model_used,
        validation=ValidationReport(passed=True, cited_numbers=[r.n for r in refs]),
        artifact=None,
    )


def _decode(text: str) -> str:
    from backend.app.services.masking_service import decode_for_persist

    return decode_for_persist(text or "") if text else (text or "")


def _merged_title(task_label: str | None, query: str) -> str:
    if (task_label or "").strip():
        return task_label.strip()[:_TITLE_MAX]
    q = " ".join(_decode(query).split())
    title = f"{MERGED_TITLE_PREFIX_AR}: {q}" if q else MERGED_TITLE_PREFIX_AR
    return title[:_TITLE_MAX]


def _ura_results(reports: Sequence[ExtendedSearchReport]) -> list[Any]:
    out: list[Any] = []
    for r in reports:
        ura = r.ura
        if ura is None:
            continue
        out += list(getattr(ura, "high_results", None) or []) + list(getattr(ura, "medium_results", None) or [])
    return out


def _publish_merged(
    merged: MergedAnswer,
    usable: Sequence[ExtendedSearchReport],
    child_ids: list[str],
    *,
    supabase: Any,
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    message_id: str | None,
    query: str,
    plan: ExtendedPlan,
    detail_level: str,
    task_label: str | None,
    run_id: str | None,
) -> tuple[str, list[dict]]:
    """Insert the merged item + its reference rows. Raises when the item insert fails."""
    from backend.app.services.references_service import persist_item_references
    from backend.app.services.workspace_service import create_workspace_item

    first = usable[0].agg_output
    agg = merged_agg_output(
        merged,
        disclaimer_ar=getattr(first, "disclaimer_ar", "") or "",
        model_used=MERGED_PROMPT_KEY,
    )
    titles = list(merged.report_titles) or [r.request.title for r in usable]
    parts = [(merged.synthesis_md or "").rstrip(), reports_footer(titles)]
    if agg.disclaimer_ar.strip():
        parts.append("---\n\n" + agg.disclaimer_ar.strip())
    content_md = _decode("\n\n".join(p for p in parts if p))
    title = _merged_title(task_label, query)
    gaps = [_decode(g) for g in merged.gaps]
    resolved = [
        {"topic": _decode(r.topic), "reports": list(r.reports), "decision": _decode(r.decision)}
        for r in merged.resolved
    ]
    metadata: dict[str, Any] = {
        "subtype": "legal_synthesis",
        "confidence": merged.confidence,
        "detail_level": detail_level,
        "ura_log_id": "",
        "gaps": gaps,
        "prompt_key": MERGED_PROMPT_KEY,
        "ref_count": len(agg.references),
        "cited_count": len(agg.references),
        "children": list(child_ids),
        "extended_search": {
            "run_id": run_id,
            "titles": titles,
            "resolved": resolved,
            "client_side": plan.client_side,
        },
    }
    with track_stage(
        "publish.workspace_item",
        conversation_id=conversation_id,
        case_id=case_id,
        agent_family="publish",
        kind="agent_search",
        message_id=message_id,
        extended="merged",
        children=len(child_ids),
        title_chars=len(title),
        content_md_chars=len(content_md),
        confidence=merged.confidence,
    ) as span:
        row = create_workspace_item(
            supabase,
            user_id,
            kind="agent_search",
            created_by="agent",
            title=title,
            conversation_id=conversation_id,
            case_id=case_id,
            message_id=message_id,
            agent_family="deep_search",
            content_md=content_md,
            metadata=metadata,
            describe_query=query,
        )
        item_id = row.get("item_id") or row.get("artifact_id") or ""
        if not item_id:
            raise RuntimeError("extended_search: merged publish returned no item_id")
        try:
            span.set(item_id=item_id, outcome="ok")
        except Exception:  # noqa: BLE001
            pass
        try:
            persist_item_references(
                supabase,
                wi_id=item_id,
                references=list(agg.references),
                ura_results=_ura_results(usable) or None,
                cited_numbers=[r.n for r in agg.references],
                ref_to_sub_queries={},
            )
        except Exception as exc:  # noqa: BLE001 - the item is stored; refs are best-effort
            logger.warning("extended_search: merged references persist failed: %s", exc, exc_info=True)
    event = {
        "type": "workspace_item_created",
        "item_id": item_id,
        "kind": "agent_search",
        "title": row.get("title", title),
        "subtype": "legal_synthesis",
        "created_by": "agent",
        # The turn's primary item: message_service puts it FIRST in messages.artifact_ids
        # (the chat message's citation target), while the event itself comes LAST so the
        # clients' auto-open (the newest created item) opens the merged answer.
        "primary": True,
    }
    return item_id, [event]


# --------------------------------------------------------------------------- publish


async def publish_extended(
    reports: Sequence[ExtendedSearchReport],
    merged: MergedAnswer | None,
    *,
    supabase: Any,
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    message_id: str | None,
    query: str,
    plan: ExtendedPlan,
    detail_level: str,
    task_label: str | None = None,
    run_id: str | None = None,
) -> PublishedExtended:
    """Publish every usable report, then (≥ 2 of them and ``merged`` given) the merged
    answer with ``children``. Sets ``report.item_id``. ``sse_events`` lists the reports'
    ``workspace_item_created`` first and the merged item's LAST, marked ``primary`` (see
    below). Never raises — a failure is logged and turns into today's Arabic status line
    at the end of ``sse_events``.

    ``query`` — the message the searches ran on (stored as ``describe_query``, as
    today's path does); ``task_label`` — the router's label, the merged item's title
    (fallback «بحث موسّع: …»); ``run_id`` — the plan's run id (metadata only).
    """
    from agents.agent_search import SearchPublishDeps, SearchPublishInput, publish_search_result

    usable = _usable(reports)
    report_events: list[dict] = []
    merged_events: list[dict] = []
    report_ids: list[str] = []
    failed = False
    t0 = time.perf_counter()

    for report in usable:
        try:
            out = await publish_search_result(
                SearchPublishInput(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    case_id=case_id,
                    message_id=message_id,
                    agg_output=report.agg_output,
                    original_query=query,
                    detail_level=detail_level,
                    ura=report.ura,
                    reg_rqrs=list(report.reg_rqrs or []),
                    comp_rqrs=[],
                    case_rqrs=list(report.case_rqrs or []),
                    per_executor_stats=dict(report.per_executor_stats or {}),
                    task_label=report.request.title,
                    describe_query=query,
                ),
                SearchPublishDeps(supabase=supabase, logger=logger),
            )
        except Exception as exc:  # noqa: BLE001 - one failed publish never stops the others
            failed = True
            logger.warning("extended_search: report %d publish failed: %s", report.index, exc, exc_info=True)
            continue
        report.item_id = out.item_id
        report_ids.append(out.item_id)
        report_events.extend(out.sse_events)

    merged_id: str | None = None
    if merged is not None and len(usable) >= 2:
        try:
            merged_id, merged_events = _publish_merged(
                merged, usable, report_ids,
                supabase=supabase, user_id=user_id, conversation_id=conversation_id,
                case_id=case_id, message_id=message_id, query=query, plan=plan,
                detail_level=detail_level, task_label=task_label, run_id=run_id,
            )
        except Exception as exc:  # noqa: BLE001
            failed = True
            logger.warning("extended_search: merged publish failed: %s", exc, exc_info=True)

    # Reports first, the merged answer LAST: web and mobile auto-open the newest created
    # item, which must be the merged answer. Its event carries ``primary: True``, so
    # message_service still puts it FIRST in messages.artifact_ids — the chat message's
    # first agent_search item is its citation target, the answer the summary came from.
    events: list[dict] = [*report_events, *merged_events]
    if failed:
        events.append({"type": "status", "text": PUBLISH_FAILED_AR})
    logger.info(
        "extended_search: published %d/%d reports, merged=%s (%.1fs)",
        len(report_ids), len(usable), merged_id or "-", time.perf_counter() - t0,
    )
    return PublishedExtended(report_item_ids=report_ids, merged_item_id=merged_id, sse_events=events)


# --------------------------------------------------------------------------- chat summary


async def write_chat_summary(
    merged: MergedAnswer,
    *,
    query: str,
    deps: Any,
    mode: str = "full",
) -> Any:
    """The chat bubble for an extended search — the responder today's path would run
    (unified or legacy), fed with the merged answer instead of one aggregator output.

    ``deps`` — the turn's ``PlannerDeps`` (its ``emit_sse``, ``router_context``,
    ``recent_messages``…); its ``_agg_output`` / ``_decision`` are set to the merged
    answer here (the legacy responder reads them). ``query`` — the message the searches
    ran on. Emits the "retrieval done" event first (the bar moves to «writing» only
    now). Returns a ``PlannerTurnResult`` (``kind="completed"``): ``response`` has
    ``chat_summary_md`` / ``next_steps``; ``follow_up_question`` on the unified path.
    Never raises: a responder failure falls back to the answer's head, as today.
    """
    from agents.deep_search_v4.planner.logger import EVENT_ERROR, EVENT_RESPONDED, EVENT_RETRIEVAL_DONE, emit
    from agents.deep_search_v4.planner.models import PlannerDecision
    from agents.deep_search_v4.planner.runner import (
        PlannerTurnResult,
        _emit_planner_ledger,
        _phase3_unified,
        _response_from_artifact,
        _with_transition_line,
    )
    from agents.responder.wiring import responder_unified

    agg = merged_agg_output(merged)
    decision = PlannerDecision(
        mode=mode if mode in ("reg_compliance_led", "case_led", "full") else "full",
        support=False,
        rationale="extended_search merged answer",
    )
    deps._agg_output = agg
    deps._decision = decision
    emit(deps, {
        "event": EVENT_RETRIEVAL_DONE,
        "mode": decision.mode,
        "confidence": merged.confidence,
        "extended": True,
    })
    t2 = time.perf_counter()

    if responder_unified("deep_search"):
        return await _phase3_unified(query, deps, agg, decision, decider_result=None, t2=t2)

    from agents.deep_search_v4.planner.agent import PLANNER_RESPONDER_LIMITS, create_planner_responder
    from agents.deep_search_v4.planner.prompts import build_responder_user_message
    from agents.models import salvage_next_steps

    degraded = False
    try:
        responder = create_planner_responder(model_override=getattr(deps, "model_override", None))
        result = await responder.run(
            build_responder_user_message(query), deps=deps, usage_limits=PLANNER_RESPONDER_LIMITS,
        )
        response = result.output
        salvaged = salvage_next_steps(response.next_steps, allow_open=False)
        if salvaged != response.next_steps:
            response.next_steps = salvaged
            response._next_steps_salvaged = True
        _emit_planner_ledger(result)
        emit(deps, {
            "event": EVENT_RESPONDED,
            "build_artifact": response.build_artifact,
            "referenced_item_id": response.referenced_item_id,
            "duration_s": round(time.perf_counter() - t2, 3),
        })
    except Exception as exc:  # noqa: BLE001 - the answer exists; reuse its head (§9)
        degraded = True
        logger.error("extended_search: responder raised (%s); response from the answer", exc, exc_info=True)
        emit(deps, {"event": EVENT_ERROR, "phase": "respond", "error": str(exc)})
        response = _response_from_artifact(agg)
    return PlannerTurnResult(
        kind="completed",
        response=_with_transition_line(response, agg),
        decision=decision,
        agg_output=agg,
        degraded=degraded,
        retrieval_query=query,
    )


__all__ = [
    "MERGED_TITLE_PREFIX_AR",
    "PUBLISH_FAILED_AR",
    "PublishedExtended",
    "REPORTS_HEADING_AR",
    "merged_agg_output",
    "publish_extended",
    "reports_footer",
    "write_chat_summary",
]
