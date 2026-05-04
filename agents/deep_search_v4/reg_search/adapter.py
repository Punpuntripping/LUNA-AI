"""Adapt reg_search graph output -> shared RerankerQueryResult / RegURAResult.

Boundary converter between the reg_search loop's internal dataclasses and
the URA 2.0 shared types the orchestrator / merger consume. Inputs are the
reg_search ``RerankerQueryResult`` dataclasses (whose ``results`` are
``RerankedResult`` instances); outputs are shared ``RerankerQueryResult``
dataclasses (whose ``results`` are typed ``RegURAResult`` instances).

Results without a ``db_id`` are dropped (no stable URA ref_id can be
constructed for them).
"""
from __future__ import annotations

from agents.deep_search_v4.reg_search.models import (
    RerankerQueryResult as RegRQR,
)
from agents.deep_search_v4.shared.models import (
    RerankerQueryResult as SharedRQR,
)
from agents.deep_search_v4.ura.schema import RegURAResult


def reg_to_rqr(reg_rqrs: list[RegRQR]) -> list[SharedRQR]:
    """Convert reg_search reranker output into shared ``RerankerQueryResult``s.

    Each inner ``RerankedResult`` becomes a ``RegURAResult``. Items with an
    empty ``db_id`` are skipped silently -- they cannot be deduped/cited
    downstream without a stable reference id.
    """
    out: list[SharedRQR] = []
    for sq in reg_rqrs or []:
        typed_results: list[RegURAResult] = []
        for r in sq.results or []:
            db_id = (getattr(r, "db_id", "") or "").strip()
            if not db_id:
                continue
            typed_results.append(
                RegURAResult(
                    ref_id=f"reg:{db_id}",
                    source_type=r.source_type,
                    title=r.title,
                    content=r.content,
                    relevance=r.relevance,
                    reasoning=r.reasoning or "",
                    appears_in_sub_queries=[],
                    rrf_max=float(getattr(r, "rrf", 0.0) or 0.0),
                    regulation_title=r.regulation_title or "",
                    article_num=r.article_num,
                    section_title=r.section_title or None,
                    article_context=r.article_context or "",
                    section_summary=r.section_summary or "",
                    references_content=r.references_content or "",
                )
            )
        out.append(
            SharedRQR(
                query=sq.query,
                rationale=sq.rationale,
                sufficient=sq.sufficient,
                domain="regulations",
                results=typed_results,
                dropped_count=sq.dropped_count,
                summary_note=sq.summary_note,
                unfold_rounds=sq.unfold_rounds,
                total_unfolds=sq.total_unfolds,
            )
        )
    return out


__all__ = ["reg_to_rqr"]
