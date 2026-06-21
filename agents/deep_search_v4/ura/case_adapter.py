"""Adapt case_search graph output -> shared RerankerQueryResult / CaseURAResult.

Boundary converter between the case_search loop's internal dataclasses and
the URA 2.0 shared types. Inputs are ``CaseSearchResult.reranker_results``
(a list of case_search's own ``RerankerQueryResult`` whose ``.results`` are
``RerankedCaseResult`` instances); outputs are shared
``RerankerQueryResult``s whose ``.results`` are typed ``CaseURAResult``s.

Results without a ``db_id`` are dropped (no stable URA ref_id).
"""
from __future__ import annotations

from agents.deep_search_v4.case_search.models import (
    CaseSearchResult,
    RerankerQueryResult as CaseRQR,
)
from agents.deep_search_v4.shared.models import (
    RerankerQueryResult as SharedRQR,
)
from agents.deep_search_v4.ura.schema import CaseURAResult


def _case_to_ura(r) -> CaseURAResult | None:
    db_id = (getattr(r, "db_id", "") or "").strip()
    if not db_id:
        return None
    # v3.0: the reranker output carries case content + metadata; ura/enrich.py
    # fills the reference-view fields it lacks (details_url, entity_name).
    return CaseURAResult(
        ref_id=f"case:{db_id}",
        source_type=r.source_type or "case",
        relevance=r.relevance,
        reasoning=r.reasoning or "",
        appears_in_sub_queries=[],
        rrf_max=float(r.score or 0.0),
        case_number=r.case_number,
        case_content=r.content or "",
        referenced_regulations=list(r.referenced_regulations or []),
        judgment_number=r.judgment_number,
        court=r.court,
        city=r.city,
        title=r.title or "",
        court_level=r.court_level,
        date_hijri=r.date_hijri,
        legal_domains=list(r.legal_domains or []),
        appeal_result=r.appeal_result,
    )


def case_to_rqr(
    case_rqrs_or_result: list[CaseRQR] | CaseSearchResult,
) -> list[SharedRQR]:
    """Convert case_search reranker output into shared ``RerankerQueryResult``s.

    Accepts either a list of case_search ``RerankerQueryResult`` directly or
    a full ``CaseSearchResult`` (convenience -- the common caller passes
    ``case_result.reranker_results``).
    """
    if isinstance(case_rqrs_or_result, CaseSearchResult):
        case_rqrs = case_rqrs_or_result.reranker_results or []
    else:
        case_rqrs = case_rqrs_or_result or []

    out: list[SharedRQR] = []
    for sq in case_rqrs:
        typed: list[CaseURAResult] = []
        kept_forensic: list[dict] = []
        for r in sq.results or []:
            ura = _case_to_ura(r)
            if ura is None:
                # Skip the same rows _case_to_ura skips (no db_id) so the
                # forensic list stays 1:1 with the typed results.
                continue
            typed.append(ura)
            # Forensic: prefer the bare cases.id UUID; fall back to db_id
            # (case_ref) only when db_uuid was not populated.
            ref_id = (getattr(r, "db_uuid", "") or "").strip() or (
                getattr(r, "db_id", "") or ""
            ).strip()
            kept_forensic.append({
                "source_table": "cases",
                "ref_id": ref_id,
                "title": r.title or "",
                "relevance": r.relevance,
                "source_type": r.source_type or "case",
                "reasoning": r.reasoning or "",
            })
        dropped_forensic = [
            {
                "source_table": d.get("source_table", "cases"),
                "ref_id": (d.get("ref_id", "") or d.get("db_uuid", "") or "").strip(),
                "title": d.get("title", "") or "",
                "drop_reason": d.get("drop_reason", "llm"),
                "reasoning": d.get("reasoning", "") or "",
                "source_type": d.get("source_type", "case"),
            }
            for d in (getattr(sq, "dropped_results", None) or [])
            if (d.get("ref_id", "") or d.get("db_uuid", "") or "").strip()
        ]
        out.append(
            SharedRQR(
                query=sq.query,
                rationale=sq.rationale,
                sufficient=sq.sufficient,
                domain="cases",
                results=typed,
                dropped_count=sq.dropped_count,
                summary_note=sq.summary_note,
                unfold_rounds=sq.unfold_rounds,
                total_unfolds=sq.total_unfolds,
                kept_forensic=kept_forensic,
                dropped_forensic=dropped_forensic,
            )
        )
    return out


__all__ = ["case_to_rqr"]
