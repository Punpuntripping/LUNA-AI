"""Adapt reg_search graph output -> shared RerankerQueryResult / typed URA.

Boundary converter between the reg_search loop's internal dataclasses and the
URA shared types the orchestrator / merger consume. Inputs are the reg_search
``RerankerQueryResult`` dataclasses (whose ``results`` are ``RerankedResult``
instances); outputs are shared ``RerankerQueryResult`` dataclasses whose
``results`` are TYPED URA results.

Type-aware routing (unified ``search_topics`` corpus)
-----------------------------------------------------
Since Wave 1, one reg_search sub-query can surface FOUR source types. This
adapter routes each ``RerankedResult`` by ``source_row["source_type"]`` (the
true topic type the RPC returned), falling back to ``RerankedResult.source_type``
for rows assembled before the source_row plumbing landed:

    regulation / appendix -> ``RegURAResult``   (ref_id ``reg:<chunks_v2.id>``)
                             appendix rows carry ``corpus="appendix"`` so the
                             (ملحق) tag survives into every view (D13).
    circular              -> ``CircularURAResult`` (ref_id ``circular:<circulars.id>``)
    service               -> ``ComplianceURAResult`` (services keep the
                             ``compliance`` domain — D9; ref_id
                             ``compliance:<sha1(service_ref)>`` so a service
                             surfaced by more than one reg_compliance sub-query
                             dedups to one URA ref).

The per-sub-query ``SharedRQR`` keeps ``domain="regulations"`` (the executor
loop's identity); the merger tiers by each *result's* own ``.domain``, so a
mixed sub-query is handled correctly. Results without a stable ``db_id`` are
dropped (no URA ref_id can be constructed).
"""
from __future__ import annotations

import hashlib

from agents.deep_search_v4.reg_compliance_search.models import (
    RerankerQueryResult as RegRQR,
)
from agents.deep_search_v4.reg_compliance_search.unfold_reranker import _circular_entity_name
from agents.deep_search_v4.shared.models import (
    RerankerQueryResult as SharedRQR,
)
from agents.deep_search_v4.ura.schema import (
    CircularURAResult,
    ComplianceURAResult,
    RegURAResult,
    cap_circular_content,
)
from agents.deep_search_v4.ura.services_unfold import (
    build_service_context,
    build_ura_metadata,
)


# Source-type -> forensic source_table. The forensic side-channel persists the
# real content-table UUID; a circular/service row must not be recorded as a
# "chunks" row.
_SOURCE_TABLE = {
    "regulation": "chunks",
    "appendix": "chunks",
    "chunk": "chunks",
    "circular": "circulars",
    "service": "services",
}


def _true_source_type(r, row: dict) -> str:
    """Resolve the true topic type for one kept ``RerankedResult``.

    Prefers the RPC's ``source_row["source_type"]``; falls back to the
    ``RerankedResult.source_type`` for legacy rows with an empty source_row.
    Everything that is not clearly a circular/service/appendix is treated as a
    regulation chunk (the byte-identical legacy reg path).
    """
    t = (row.get("source_type") or "").strip().lower()
    if t in ("regulation", "appendix", "circular", "service"):
        return t
    rr = (getattr(r, "source_type", "") or "").strip().lower()
    if rr in ("circular", "service"):
        return rr
    return "regulation"


def _service_ref_id(service_ref: str, service_url: str = "") -> str:
    """Stable ``compliance:<16-char-sha1>`` over service_ref (fallback: url).

    The ``compliance:<sha1(service_ref)>`` scheme is retained for continuity:
    services persisted before Wave 4 (by the now-retired compliance executor)
    keep the same citation ref_id, and a service surfaced by more than one
    reg_compliance sub-query dedups to one URA ref.
    """
    seed = service_ref or service_url
    if not seed:
        return ""
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    return f"compliance:{digest}"


def _build_reg(r, row: dict, db_id: str, true_type: str) -> tuple[RegURAResult, dict]:
    """Regulation / appendix chunk -> ``RegURAResult`` shell + forensic dict.

    Byte-identical to the legacy path except the ``corpus`` marker: appendix
    chunks carry ``corpus="appendix"`` so the (ملحق) tag survives (D13). The
    heavy fields (reg_title, chunk_content, cross_refs, …) are still filled by
    ``ura/enrich.py`` post-merge.
    """
    corpus = (row.get("corpus") or "").strip().lower()
    if not corpus and true_type == "appendix":
        corpus = "appendix"

    typed = RegURAResult(
        ref_id=f"reg:{db_id}",
        source_type=r.source_type,
        relevance=r.relevance,
        reasoning=r.reasoning or "",
        appears_in_sub_queries=[],
        rrf_max=float(getattr(r, "rrf", 0.0) or 0.0),
        corpus=corpus,
    )
    forensic = {
        "source_table": "chunks",
        "ref_id": db_id,
        "title": (getattr(r, "title", "") or "").strip(),
        "relevance": r.relevance,
        "source_type": r.source_type,
        "reasoning": r.reasoning or "",
    }
    return typed, forensic


def _build_circular(r, row: dict, db_id: str) -> tuple[CircularURAResult, dict]:
    """Circular row -> ``CircularURAResult`` + forensic dict (content carried).

    Full content is read from ``source_row["content"]`` (``r.content`` is a
    reranker snippet) and capped to the aggregator view at 4k (D11). Entity name
    is resolved from the embedded ``entities`` object via the Wave-1 helper.
    """
    title = (row.get("title") or "").strip()
    typed = CircularURAResult(
        ref_id=f"circular:{db_id}",
        source_type="circular",
        relevance=r.relevance,
        reasoning=r.reasoning or "",
        appears_in_sub_queries=[],
        rrf_max=float(getattr(r, "rrf", 0.0) or 0.0),
        circ_ref=(row.get("circ_ref") or "").strip(),
        title=title,
        entity_name=_circular_entity_name(row),
        content=cap_circular_content(row.get("content")),
        source_url=(row.get("source") or "").strip(),
        sectors=list(row.get("sectors") or []),
    )
    forensic = {
        "source_table": "circulars",
        "ref_id": db_id,
        "title": title,
        "relevance": r.relevance,
        "source_type": "circular",
        "reasoning": r.reasoning or "",
    }
    return typed, forensic


def _build_service(r, row: dict, db_id: str) -> tuple[ComplianceURAResult | None, dict | None]:
    """Service row -> ``ComplianceURAResult`` + forensic dict (D9/D10).

    Keeps the ``compliance`` domain. ``service_context`` holds the compact
    user/reference view; the structured fields drive the RICH aggregator view
    built lazily in ``ComplianceURAResult.for_aggregator``. Returns ``(None,
    None)`` when no stable ref_id can be minted (no service_ref AND no url).
    """
    service_ref = (row.get("service_ref") or "").strip()
    service_url = row.get("service_url") or row.get("url") or ""
    ref_id = _service_ref_id(service_ref, service_url)
    if not ref_id:
        return None, None

    name = (row.get("service_name_ar") or row.get("intro_title") or "").strip()
    metadata = build_ura_metadata(row)
    typed = ComplianceURAResult(
        ref_id=ref_id,
        source_type="gov_service",
        relevance=r.relevance,
        reasoning=r.reasoning or "",
        appears_in_sub_queries=[],
        rrf_max=float(getattr(r, "rrf", 0.0) or 0.0),
        service_name=name,
        service_context=build_service_context(row),
        url=row.get("url", "") or "",
        intro_title=(row.get("intro_title") or "").strip(),
        intro_description=(row.get("intro_description") or "").strip(),
        steps=list(row.get("steps") or []),
        requirements=list(row.get("requirements") or []),
        required_documents=list(row.get("required_documents") or []),
        **metadata,
    )
    forensic = {
        "source_table": "services",
        "ref_id": db_id,
        "title": name,
        "relevance": r.relevance,
        "source_type": "gov_service",
        "reasoning": r.reasoning or "",
    }
    return typed, forensic


def reg_compliance_to_rqr(reg_rqrs: list[RegRQR]) -> list[SharedRQR]:
    """Convert reg_search reranker output into shared ``RerankerQueryResult``s.

    Each inner ``RerankedResult`` is routed by its true source type to the
    matching typed URA result (see the module docstring). Items with an empty
    ``db_id`` — or services with no stable ref seed — are skipped silently: they
    cannot be deduped/cited downstream without a stable reference id.
    """
    out: list[SharedRQR] = []
    for sq in reg_rqrs or []:
        typed_results: list = []
        kept_forensic: list[dict] = []
        for r in sq.results or []:
            db_id = (getattr(r, "db_id", "") or "").strip()
            if not db_id:
                continue
            row = getattr(r, "source_row", None) or {}
            true_type = _true_source_type(r, row)

            if true_type == "circular":
                typed, forensic = _build_circular(r, row, db_id)
            elif true_type == "service":
                typed, forensic = _build_service(r, row, db_id)
            else:  # regulation / appendix / legacy chunk
                typed, forensic = _build_reg(r, row, db_id, true_type)

            if typed is None:
                continue
            typed_results.append(typed)
            kept_forensic.append(forensic)

        dropped_forensic = [
            {
                "source_table": _SOURCE_TABLE.get(
                    (d.get("source_type", "") or "").strip().lower(), "chunks"
                ),
                "ref_id": (d.get("db_id", "") or "").strip(),
                "title": (d.get("title", "") or "").strip(),
                "drop_reason": d.get("drop_reason", "llm"),
                "reasoning": d.get("reasoning", "") or "",
                "source_type": d.get("source_type", "chunk"),
            }
            for d in (getattr(sq, "dropped_results", None) or [])
            if (d.get("db_id", "") or "").strip()
        ]
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
                kept_forensic=kept_forensic,
                dropped_forensic=dropped_forensic,
            )
        )
    return out


__all__ = ["reg_compliance_to_rqr"]
