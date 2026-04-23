"""Merge reg_search + compliance_search outputs into a Unified Retrieval Artifact.

Two-stage merge:
    reg_reranker_results ── merge_partial_ura() ──> PartialURA
    PartialURA + ComplianceURASlice ── merge_to_ura() ──> UnifiedRetrievalArtifact

Identity rules:
    - reg results:        ref_id = f"reg:{db_id}"
    - compliance results: ref_id = f"compliance:{sha1(url)[:16]}" (set by ura_runner)

Dedup rules:
    - Within reg: same ref_id across sub-queries -> merge appears_in_sub_queries,
      relevance = max("high" > "medium"), rrf_max = max, reasoning joined.
    - Cross-domain: no dedup (reg and compliance live in different namespaces).
"""
from __future__ import annotations

from datetime import datetime, timezone

from agents.deep_search_v3.ura.schema import (
    PartialURA,
    URAResult,
    UnifiedRetrievalArtifact,
)
from agents.deep_search_v3.compliance_search.models import ComplianceURASlice
from agents.deep_search_v3.reg_search.models import (
    RerankedResult,
    RerankerQueryResult,
)


_RELEVANCE_RANK = {"high": 2, "medium": 1}
_DOMAIN_RANK = {"regulations": 2, "compliance": 1}


def _relevance_max(a: str, b: str) -> str:
    return a if _RELEVANCE_RANK.get(a, 0) >= _RELEVANCE_RANK.get(b, 0) else b


def _reg_ref_id(result: RerankedResult) -> str:
    db_id = (result.db_id or "").strip()
    if not db_id:
        return ""
    return f"reg:{db_id}"


def _reg_metadata(result: RerankedResult) -> dict:
    meta: dict = {
        "regulation_title": result.regulation_title,
        "article_num": result.article_num,
        "section_title": result.section_title,
    }
    if result.article_context:
        meta["article_context"] = result.article_context
    if result.references_content:
        meta["references_content"] = result.references_content
    if result.section_summary:
        meta["section_summary"] = result.section_summary
    return meta


def _join_reasoning(parts: list[str]) -> str:
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        s = (p or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return "؛ ".join(out)


def merge_partial_ura(
    reg_reranker_results: list[RerankerQueryResult],
    original_query: str,
    query_id: int = 0,
    log_id: str = "",
    sector_filter: list[str] | None = None,
) -> PartialURA:
    """Convert reg reranker output into a PartialURA.

    Results keyed by reg ref_id are deduplicated across sub-queries:
    - appears_in_sub_queries merges the sub-query indices where the ref appeared.
    - relevance is lifted to the strongest seen ("high" > "medium").
    - rrf_max tracks the highest RRF score seen (0.0 if unknown -- reranker
      output does not carry RRF per result today, so this stays 0.0 unless
      the caller patches it in).
    - reasoning strings are joined (deduped).

    cases=None by design -- the slot exists in the URA plan but case_search
    is out of scope for this pipeline.
    """
    sub_queries_meta: list[dict] = []
    grouped: dict[str, URAResult] = {}
    first_seen: list[str] = []

    for sq_idx, query_result in enumerate(reg_reranker_results or []):
        sub_queries_meta.append({
            "index": sq_idx,
            "query": query_result.query,
            "rationale": query_result.rationale,
            "sufficient": query_result.sufficient,
            "kept_count": len(query_result.results or []),
            "dropped_count": query_result.dropped_count,
        })

        for result in query_result.results or []:
            ref_id = _reg_ref_id(result)
            if not ref_id:
                continue

            if ref_id not in grouped:
                ura_result = URAResult(
                    ref_id=ref_id,
                    domain="regulations",
                    source_type=result.source_type,
                    title=result.title,
                    content=result.content,
                    metadata=_reg_metadata(result),
                    relevance=result.relevance,
                    reasoning=result.reasoning,
                    appears_in_sub_queries=[sq_idx],
                    rrf_max=0.0,
                    triggered_by_ref_ids=[],
                    cross_references=[],
                )
                grouped[ref_id] = ura_result
                first_seen.append(ref_id)
            else:
                existing = grouped[ref_id]
                if sq_idx not in existing.appears_in_sub_queries:
                    existing.appears_in_sub_queries.append(sq_idx)
                existing.relevance = _relevance_max(existing.relevance, result.relevance)
                existing.reasoning = _join_reasoning([existing.reasoning, result.reasoning])
                if not existing.content and result.content:
                    existing.content = result.content

    ordered: list[URAResult] = [grouped[rid] for rid in first_seen]

    return PartialURA(
        schema_version="1.0",
        query_id=query_id,
        log_id=log_id,
        original_query=original_query,
        produced_at=datetime.now(timezone.utc).isoformat(),
        sub_queries=sub_queries_meta,
        results=ordered,
        sector_filter=list(sector_filter or []),
    )


def _compliance_result_from_dict(d: dict) -> URAResult:
    return URAResult(
        ref_id=d.get("ref_id", ""),
        domain="compliance",
        source_type=d.get("source_type", "gov_service"),
        title=d.get("title", ""),
        content=d.get("content", ""),
        metadata=dict(d.get("metadata", {}) or {}),
        relevance=d.get("relevance", "medium"),
        reasoning=d.get("reasoning", ""),
        appears_in_sub_queries=list(d.get("appears_in_sub_queries", []) or []),
        rrf_max=float(d.get("rrf_max", 0.0) or 0.0),
        triggered_by_ref_ids=list(d.get("triggered_by_ref_ids", []) or []),
        cross_references=list(d.get("cross_references", []) or []),
    )


def _order_key(result: URAResult) -> tuple:
    return (
        -_RELEVANCE_RANK.get(result.relevance, 0),
        -_DOMAIN_RANK.get(result.domain, 0),
        -result.rrf_max,
    )


def merge_to_ura(
    partial: PartialURA,
    compliance: ComplianceURASlice | None,
) -> UnifiedRetrievalArtifact:
    """Merge compliance results into a partial URA.

    - Reg results pass through untouched.
    - Compliance results are appended (different ref_id namespace -> no dedup).
    - Cross-references are computed: every compliance result with
      triggered_by_ref_ids gets a cross_reference entry on the *compliance*
      side pointing to each trigger reg ref, kind="implements".
    - Final ordering: relevance DESC, domain DESC (regulations before
      compliance), rrf_max DESC.
    """
    compliance_results: list[URAResult] = []
    compliance_queries: list[str] = []
    if compliance is not None:
        for raw in compliance.results or []:
            compliance_results.append(_compliance_result_from_dict(raw))
        compliance_queries = list(compliance.queries_used or [])

    reg_ref_ids = {r.ref_id for r in partial.results}

    for cr in compliance_results:
        existing = list(cr.cross_references)
        existing_targets = {(x.get("cites_ref_id"), x.get("kind")) for x in existing}
        for trigger_ref in cr.triggered_by_ref_ids:
            if trigger_ref in reg_ref_ids:
                key = (trigger_ref, "implements")
                if key not in existing_targets:
                    existing.append({"cites_ref_id": trigger_ref, "kind": "implements"})
                    existing_targets.add(key)
        cr.cross_references = existing

    combined: list[URAResult] = list(partial.results) + compliance_results
    combined.sort(key=_order_key)

    sub_queries = list(partial.sub_queries)
    for i, q in enumerate(compliance_queries):
        sub_queries.append({
            "index": len(partial.sub_queries) + i,
            "query": q,
            "domain": "compliance",
        })

    return UnifiedRetrievalArtifact(
        schema_version="1.0",
        query_id=partial.query_id,
        log_id=partial.log_id,
        original_query=partial.original_query,
        produced_at=datetime.now(timezone.utc).isoformat(),
        produced_by={
            "reg_search": True,
            "compliance_search": compliance is not None,
            "case_search": False,
        },
        sub_queries=sub_queries,
        results=combined,
        dropped=[],
        sector_filter=list(partial.sector_filter),
    )


__all__ = [
    "merge_partial_ura",
    "merge_to_ura",
]
