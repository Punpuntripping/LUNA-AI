"""«بحث شامل» — merge the searches' reports into ONE answer (plan §4.6, §4.9 c; D4, D14, D19).

The merger itself is ``agents/deep_search_v4/extended_merger`` (prompt ``merger-v1``):
it reads every report with the planner's request and the angle cards behind it, the
whole file and every document's summary, and one shared reference list WITH excerpts;
it returns the merged answer + the angle scoreboard (نقاط القوة والضعف) + the settled
contradictions, published as the ``extended_synthesis`` workspace item. This module is
the extended search's entry point to it:

- :func:`merge_reports` — builds the merger's input from the reports, the plan and the
  planner's view (``extended_merger.adapter``: plan / view text encoded with the turn
  codec), persists the masking fakes minted for it BEFORE the model runs, runs the
  merger (slot ``extended_merger`` = the aggregator's policy; one ledger row
  ``deep_search.extended_merger``) and returns a ``MergedAnswer`` carrying the
  merger's whole output. Raises on failure: the wiring then publishes the reports
  alone and writes the chat from the first one (:func:`single_report_answer`).
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from .models import ExtendedPlan, ExtendedSearchReport, MergedAnswer, PlannerView

logger = logging.getLogger(__name__)

MODEL_SLOT = "extended_merger"
LEDGER_STAGE = "deep_search.extended_merger"

_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


def _ref_dict(ref: Any) -> dict[str, Any]:
    if isinstance(ref, dict):
        return dict(ref)
    try:
        return ref.model_dump(mode="json")
    except Exception:  # noqa: BLE001
        return dict(getattr(ref, "__dict__", {}) or {})


def _dedupe(items: Sequence[str]) -> list[str]:
    out: list[str] = []
    for s in items:
        t = str(s or "").strip()
        if t and t not in out:
            out.append(t)
    return out


def _report_gaps(report: ExtendedSearchReport) -> list[str]:
    from agents.deep_search_v4.aggregator.runner import _MODEL_FAILURE_GAP_AR

    return [
        g for g in _dedupe(getattr(report.agg_output, "gaps", None) or [])
        if g != "no_references_after_reranker" and g != _MODEL_FAILURE_GAP_AR
    ]


def single_report_answer(report: ExtendedSearchReport) -> MergedAnswer:
    """One report as the answer — when only one search succeeded (no merger) or the
    merger failed. Its own citations and references, unchanged."""
    agg = report.agg_output
    return MergedAnswer(
        synthesis_md=(getattr(agg, "synthesis_md", "") or "").strip(),
        references=[_ref_dict(r) for r in getattr(agg, "references", None) or []],
        gaps=_report_gaps(report),
        confidence=str(getattr(agg, "confidence", "medium") or "medium")
        if str(getattr(agg, "confidence", "")) in _CONFIDENCE_RANK else "medium",
        resolved=[],
        report_titles=[report.request.title],
    )


def _persist_fakes(supabase: Any, user_id: str | None) -> None:
    if supabase is None or not user_id:
        return
    try:
        from backend.app.services.masking_service import active_codec, persist_new_mappings

        persist_new_mappings(supabase, user_id, active_codec())
    except Exception:  # noqa: BLE001
        logger.warning("extended_merger: persisting new masking fakes failed", exc_info=True)


async def merge_reports(
    reports: Sequence[ExtendedSearchReport],
    *,
    query: str,
    recent_messages: Any,
    plan: ExtendedPlan,
    view: PlannerView,
    supabase: Any = None,
    user_id: str | None = None,
    conversation_id: str | None = None,
    conversation_memory: str | None = None,
    model: Any = None,
) -> MergedAnswer:
    """Merge the reports (every one of the plan — a failed one reaches the scoreboard as
    not researched) into one answer.

    ``query`` — the message the searches ran on (encoded, as they got it);
    ``recent_messages`` — the turn's encoded ``ChatMessageSnapshot`` list (or
    ``{"role", "content"}`` dicts). ``conversation_memory`` — the rendered, encoded
    memory block the searches got (``None`` when the conversation has none).
    ``supabase`` / ``user_id`` (optional): persist the
    masking fakes minted for the merger's input before the model runs. ``model`` — a
    test model. Raises ``ValueError`` with fewer than 2 usable reports (the wiring
    publishes the single report instead) and re-raises a model failure.
    """
    from agents.deep_search_v4.extended_merger.adapter import input_from_extended, merged_answer_from
    from agents.deep_search_v4.extended_merger.runner import run_merger

    usable = [r for r in reports if r.ok and r.agg_output is not None]
    if len(usable) < 2:
        raise ValueError(f"extended_merger: needs at least 2 usable reports, got {len(usable)}")

    inp = input_from_extended(
        reports, query=query, recent_messages=recent_messages, plan=plan, view=view,
        conversation_memory=conversation_memory,
    )
    _persist_fakes(supabase, user_id)
    out, _usage = await run_merger(inp, model=model, conversation_id=conversation_id)
    logger.info(
        "extended_merger: %d/%d reports → %d chars, %d references, confidence=%s, %d resolved, scoreboard %d",
        len(usable), len(reports), len(out.synthesis_md), len(out.references), out.confidence,
        len(out.resolved), len(out.angle_status),
    )
    return merged_answer_from(out, reports)


__all__ = ["LEDGER_STAGE", "MODEL_SLOT", "merge_reports", "single_report_answer"]
