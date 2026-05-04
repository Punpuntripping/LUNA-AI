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
    return CaseURAResult(
        ref_id=f"case:{db_id}",
        source_type=r.source_type or "case",
        title=r.title or "",
        content=r.content or "",
        relevance=r.relevance,
        reasoning=r.reasoning or "",
        appears_in_sub_queries=[],
        rrf_max=float(r.score or 0.0),
        court=r.court,
        city=r.city,
        court_level=r.court_level,
        case_number=r.case_number,
        judgment_number=r.judgment_number,
        date_hijri=r.date_hijri,
        legal_domains=list(r.legal_domains or []),
        referenced_regulations=list(r.referenced_regulations or []),
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
        for r in sq.results or []:
            ura = _case_to_ura(r)
            if ura is None:
                continue
            typed.append(ura)
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
            )
        )
    return out


__all__ = ["case_to_rqr"]
