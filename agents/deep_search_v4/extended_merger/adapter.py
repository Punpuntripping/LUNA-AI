"""Bridge between the extended search (``agents/deep_search_v4/extended``) and this merger.

- :func:`input_from_extended` — the searches' reports + the plan + the planner's view →
  :class:`MergerInput`. Masking (وضع السرية): the plan and the view are DECODED (real)
  text, the reports, the query and the conversation are already ENCODED pipeline text;
  everything taken from the plan / view is encoded here with the turn codec (passthrough
  outside a masked turn) — the same parts the extended search's own briefs encode. The
  documents are the encoded marked text every search read (``whole_file_items``), capped
  exactly as the searches' were.
- :func:`merged_answer_from` — :class:`MergerOutput` → the extended search's
  ``MergedAnswer`` (what the chat summary reads), carrying the whole output in
  ``MergedAnswer.merger`` for the publisher of the merged item (subtype
  ``extended_synthesis``).
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from .models import LeftOutAngle, MergerAngle, MergerDocument, MergerInput, MergerOutput, MergerReport

if TYPE_CHECKING:
    from agents.deep_search_v4.extended.models import (
        AngleCard,
        ExtendedPlan,
        ExtendedSearchReport,
        MergedAnswer,
        PlannerView,
    )

#: Report gaps that are pipeline codes, never user-facing text.
_NO_REFERENCES_CODE = "no_references_after_reranker"
_NO_TEXT_NOTE = "(لم يُتح نص هذا المستند — اعتمد على الملخص)"
_OVER_TOTAL_NOTE = "(لم يُرفق النص لتجاوز حد السياق — اعتمد على الملخص)"


def _encode(text: str) -> str:
    from agents.deep_search_v4.extended.runner import _encode as encode

    return encode(text or "")


def _report_gaps(agg: Any) -> list[str]:
    from agents.deep_search_v4.aggregator.runner import _MODEL_FAILURE_GAP_AR

    out: list[str] = []
    for g in getattr(agg, "gaps", None) or []:
        g = str(g or "").strip()
        if g and g not in out and g not in (_NO_REFERENCES_CODE, _MODEL_FAILURE_GAP_AR):
            out.append(g)
    return out


def _angle(card: "AngleCard") -> MergerAngle:
    from agents.deep_search_v4.extended.brief import _section

    return MergerAngle(
        id=card.id,
        kind=card.kind,
        strength=card.strength,
        title=_encode(card.title),
        argument=_encode(card.argument),
        legal_question=_encode(card.legal_question),
        weakness=_encode(card.weakness),
        for_side=card.for_side or "unknown",
        for_party=_encode(card.for_party),
        sections=[_encode(_section(s)) for s in card.sections],
        ocr_suspect=bool(card.ocr_suspect),
    )


def _documents(view: "PlannerView") -> list[MergerDocument]:
    """Every document as the searches read it (encoded marked text + summary), capped
    per document and in total like the searches (``runner.WHOLE_FILE_*_CAP``)."""
    from agents.deep_search_v4.extended.runner import (
        WHOLE_FILE_PER_DOC_CAP,
        WHOLE_FILE_TOTAL_CAP,
        whole_file_items,
    )

    items = {s.item_id: s for s in whole_file_items(view)}
    used = 0
    out: list[MergerDocument] = []
    for doc in view.documents:
        snap = items.get(str(doc.item_id))
        text = (snap.content_md if snap is not None else "") or ""
        if not text.strip():
            body = _NO_TEXT_NOTE
        else:
            cut = text[:WHOLE_FILE_PER_DOC_CAP]
            if used + len(cut) > WHOLE_FILE_TOTAL_CAP:
                body = _OVER_TOTAL_NOTE
            else:
                used += len(cut)
                body = cut
                if len(text) > len(cut):
                    body += f"\n[انتهى المعروض: {len(cut)} حرف من أصل {len(text)} — بقية الوثيقة لم تُعرض]"
        out.append(MergerDocument(
            ref=doc.id,
            title=(snap.title if snap is not None else "") or _encode(doc.title),
            doc_type=doc.doc_type,
            summary=(snap.summary if snap is not None else "") or "",
            text=body,
            item_id=str(doc.item_id),
        ))
    return out


def input_from_extended(
    reports: Sequence["ExtendedSearchReport"],
    *,
    query: str,
    recent_messages: Any,
    plan: "ExtendedPlan",
    view: "PlannerView",
) -> MergerInput:
    """Every report of the plan, in plan order — a failed one with ``output=None`` so
    its angles reach the scoreboard as not researched."""
    from agents.deep_search_v4.extended.view import recent_pairs

    merger_reports: list[MergerReport] = []
    for r in sorted(reports, key=lambda x: x.index):
        agg = r.agg_output if (r.ok and r.agg_output is not None) else None
        if agg is not None:
            agg = agg.model_copy(update={"gaps": _report_gaps(agg)})
        merger_reports.append(MergerReport(
            title=_encode(r.request.title),
            mode=r.request.mode,
            focus=_encode(r.request.focus),
            why=_encode(r.request.why),
            angle_ids=list(r.request.angles),
            output=agg,
            item_id=r.item_id,
            ura=r.ura,
        ))
    client = view.map_client or {}
    party = str(client.get("party") or "") if str(client.get("side") or "") == plan.client_side else ""
    return MergerInput(
        query=query,
        reports=merger_reports,
        angles={a.id: _angle(a) for a in view.angles},
        documents=_documents(view),
        recent_messages=[{"role": role, "content": content}
                         for role, content in recent_pairs(recent_messages, query=query)],
        understanding=_encode(plan.understanding),
        client_side=plan.client_side or "unknown",
        client_party=_encode(party),
        left_out=[LeftOutAngle(angle_id=lo.angle, why=_encode(lo.why)) for lo in plan.left_out],
    )


def merged_answer_from(out: MergerOutput, reports: Sequence["ExtendedSearchReport"]) -> "MergedAnswer":
    from agents.deep_search_v4.extended.models import MergedAnswer, Resolution

    usable = sorted((r for r in reports if r.ok and r.agg_output is not None), key=lambda r: r.index)
    return MergedAnswer(
        synthesis_md=out.synthesis_md,
        references=[r.model_dump(mode="json") for r in out.references],
        gaps=list(out.gaps),
        confidence=out.confidence,
        resolved=[Resolution(topic=r.topic, reports=list(r.reports), decision=r.decision) for r in out.resolved],
        report_titles=[r.request.title for r in usable],
        merger=out.model_dump(mode="json"),
    )


__all__ = ["input_from_extended", "merged_answer_from"]
