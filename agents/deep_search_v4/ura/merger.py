"""Single-pass merger: 3 executor phase outputs -> Unified Retrieval Artifact 2.0.

Wave B of the Loop V2 refactor replaced the legacy two-stage pipeline
(``reg_reranker_results -> PartialURA -> merge_to_ura``) with a single
pass that consumes the three shared ``RerankerQueryResult`` streams
produced by the reg / compliance / case executors and emits a fully
tiered ``UnifiedRetrievalArtifact``.

Identity rules
--------------
Each domain emits its own namespaced ``ref_id``:
    - regulations -> ``"reg:<db_id>"``
    - compliance  -> ``"compliance:<sha1(url)[:16]>"``
    - cases       -> ``"case:<db_id>"``
Because the prefixes are disjoint, cross-domain dedup is a no-op; the
merger still uses a single ``grouped`` dict keyed by ``ref_id`` for
simplicity.

Sub-query indexing
------------------
Sub-queries carry a **global** index across the three phases in
absorption order: regulations first, then compliance, then cases. So if
reg absorbs 3 sub-queries, the first compliance sub-query lands at
global index 3, and the first case sub-query lands after compliance.

Dedup semantics (within a domain)
--------------------------------
When the same ``ref_id`` appears in multiple sub-queries of the same
domain the merger:
    - unions ``appears_in_sub_queries`` (sorted ascending),
    - lifts ``relevance`` to ``max("high" > "medium")``,
    - joins ``reasoning`` with ``"؛ "`` preserving first-seen order and
      deduping empty / repeated strings,
    - keeps the first non-empty ``content``,
    - keeps the maximum ``rrf_max``.

Tier split & ordering
--------------------
After dedup the merger partitions results by ``relevance`` into
``high_results`` and ``medium_results``. Each tier is sorted by
``(-DOMAIN_RANK[domain], -rrf_max)`` so higher-ranked domains (default:
regulations > cases > compliance) surface first.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Union

from agents.deep_search_v4.shared.models import RerankerQueryResult
from agents.deep_search_v4.ura.schema import (
    CaseURAResult,
    ComplianceURAResult,
    Domain,
    RegURAResult,
    UnifiedRetrievalArtifact,
)

logger = logging.getLogger(__name__)

# Module-level rank tables. Tweak DOMAIN_RANK in Wave D if the default
# ordering needs to change (e.g. surface cases before regulations for
# precedent-heavy queries).
DOMAIN_RANK: dict[str, int] = {
    "regulations": 3,
    "cases": 2,
    "compliance": 1,
}

_RELEVANCE_RANK: dict[str, int] = {"high": 2, "medium": 1}

# Per-sub-query keep caps (post Wave-D monitor findings).
# q27 had one reg sub-query supply 31 of 76 URA refs because the reranker
# kept 35 of 38 candidates without an upper bound. The aggregator cited
# only ~24% of URA refs inline; the rest were dead weight.
# Safety-net caps applied by the merger AFTER the per-domain rerankers.
# The primary keep caps live in each domain's reranker (reg: 8/4, case: 6/4,
# compliance: 6/4) and are applied before the results reach here. These
# merger caps are a last-resort backstop (12 high >> 8 default, so they
# normally never fire). Order of selection: rrf_max desc, then array order.
MAX_HIGH_PER_SUBQUERY = 12
MAX_MEDIUM_PER_SUBQUERY = 4

_DomainResult = Union[RegURAResult, ComplianceURAResult, CaseURAResult]


def _max_relevance(a: str, b: str) -> str:
    """Return the stronger of two relevance labels (``"high" > "medium"``)."""
    return a if _RELEVANCE_RANK.get(a, 0) >= _RELEVANCE_RANK.get(b, 0) else b


def _join_reasoning(parts: list[str]) -> str:
    """Join non-empty, first-seen-unique reasoning fragments with ``"؛ "``."""
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        s = (part or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return "؛ ".join(out)


def _order_key(result: _DomainResult) -> tuple[int, float]:
    """Sort key for a tier: higher DOMAIN_RANK first, then higher rrf_max."""
    return (-DOMAIN_RANK.get(result.domain, 0), -float(result.rrf_max or 0.0))


def build_ura_from_phases(
    reg_rqrs: list[RerankerQueryResult],
    compliance_rqrs: list[RerankerQueryResult],
    case_rqrs: list[RerankerQueryResult],
    *,
    original_query: str,
    query_id: int = 0,
    log_id: str = "",
    sector_filter: list[str] | None = None,
) -> UnifiedRetrievalArtifact:
    """Build a URA 2.0 artifact from the three executor phase outputs.

    Any phase may be empty (that domain was skipped or failed). Each
    sub-query gets a global index, each result is deduped by ``ref_id``
    within its domain, and the final two tier lists (``high_results`` /
    ``medium_results``) are sorted by ``DOMAIN_RANK`` then ``rrf_max``.

    Parameters
    ----------
    reg_rqrs, compliance_rqrs, case_rqrs:
        Shared ``RerankerQueryResult`` lists from each domain's adapter.
        May be empty.
    original_query:
        The user's original query string, copied into URA.
    query_id, log_id:
        Trace identifiers (optional).
    sector_filter:
        Optional sector filter list carried through to URA.

    Returns
    -------
    UnifiedRetrievalArtifact
        A fully populated URA 2.0 artifact. ``produced_by`` flags are
        set based solely on whether each phase produced any sub-queries.
    """
    sub_queries_meta: list[dict] = []
    grouped: dict[str, _DomainResult] = {}
    # Per-ref_id merge counter (number of *additional* sightings beyond
    # the first); only counted when the merge actually contributed a new
    # sub-query index, so cross-sub-query dedup is reflected accurately.
    merge_counts: dict[str, int] = {}
    empties_filtered = 0

    def _is_empty(result: _DomainResult) -> bool:
        """A result is 'useless' when its ``content`` body is blank.

        Why content-only (tightened post Wave-D monitor findings): the
        aggregator synthesizes from ``content``; a row with a title but
        no body cannot ground any citation. Three q27 medium-tier reg
        entries (refs #1, #3, #60 of monitor dump) carried titles like
        "المعايير الأساسية للسلامة" but empty bodies and produced
        un-citeable references. Filter at the merger so they never
        reach the aggregator.

        Section-level URA results often carry their substance in
        ``section_summary`` rather than ``content``; for those we accept
        section_summary as a body proxy.
        """
        content = (getattr(result, "content", "") or "").strip()
        if content:
            return False
        section_summary = (getattr(result, "section_summary", "") or "").strip()
        if section_summary:
            return False
        return True

    def _cap(results: list) -> list:
        """Keep top ``MAX_HIGH_PER_SUBQUERY`` high + ``MAX_MEDIUM_PER_SUBQUERY``
        medium per sub-query, sorted by ``rrf_max`` desc (ties: input order).
        """
        if not results:
            return []
        highs: list = [r for r in results if getattr(r, "relevance", "") == "high"]
        meds: list = [r for r in results if getattr(r, "relevance", "") != "high"]
        highs.sort(key=lambda r: -float(getattr(r, "rrf_max", 0.0) or 0.0))
        meds.sort(key=lambda r: -float(getattr(r, "rrf_max", 0.0) or 0.0))
        return highs[:MAX_HIGH_PER_SUBQUERY] + meds[:MAX_MEDIUM_PER_SUBQUERY]

    capped_total = 0

    def _absorb(domain: Domain, rqrs: list[RerankerQueryResult]) -> None:
        nonlocal empties_filtered, capped_total
        for sq in rqrs or []:
            sq_index = len(sub_queries_meta)
            raw_results = list(sq.results or [])
            capped_results = _cap(raw_results)
            capped_total += max(0, len(raw_results) - len(capped_results))
            meta: dict = {
                "index": sq_index,
                "query": sq.query,
                "rationale": sq.rationale,
                "domain": domain,
                "sufficient": bool(sq.sufficient),
                "kept_count": len(capped_results),
                "raw_kept_count": len(raw_results),
                "dropped_count": int(sq.dropped_count or 0),
            }
            if sq.summary_note:
                meta["summary_note"] = sq.summary_note
            sub_queries_meta.append(meta)

            for result in capped_results:
                ref_id = getattr(result, "ref_id", "") or ""
                if not ref_id:
                    continue

                if _is_empty(result):
                    empties_filtered += 1
                    logger.debug(
                        "ura.merger: filtered empty-content result ref_id=%s "
                        "domain=%s sub_query=%d",
                        ref_id,
                        domain,
                        sq_index,
                    )
                    continue

                if ref_id not in grouped:
                    # Don't mutate the caller's instance: make a shallow
                    # copy so we own the appears_in_sub_queries list and
                    # can freely bump relevance / rrf_max during merge.
                    merged = result.model_copy(
                        update={"appears_in_sub_queries": [sq_index]}
                    )
                    grouped[ref_id] = merged
                    continue

                # Dedup hit: same ref_id already grouped (either earlier in
                # this sub-query or in a prior one). Count the merge for
                # observability; merge appears_in_sub_queries / relevance /
                # reasoning / content / rrf_max as before.
                merge_counts[ref_id] = merge_counts.get(ref_id, 0) + 1
                existing = grouped[ref_id]
                if sq_index not in existing.appears_in_sub_queries:
                    existing.appears_in_sub_queries.append(sq_index)
                    existing.appears_in_sub_queries.sort()

                existing.relevance = _max_relevance(
                    existing.relevance, result.relevance
                )
                existing.reasoning = _join_reasoning(
                    [existing.reasoning, result.reasoning or ""]
                )
                if not existing.content and result.content:
                    existing.content = result.content
                incoming_rrf = float(getattr(result, "rrf_max", 0.0) or 0.0)
                if incoming_rrf > float(existing.rrf_max or 0.0):
                    existing.rrf_max = incoming_rrf

    _absorb("regulations", reg_rqrs)
    _absorb("compliance", compliance_rqrs)
    _absorb("cases", case_rqrs)

    if merge_counts:
        for ref_id, count in merge_counts.items():
            logger.debug(
                "ura.merger: deduped ref_id=%s merge_count=%d",
                ref_id,
                count,
            )
    total_dedup_merges = sum(merge_counts.values())

    logger.info(
        "ura.merger: kept=%d unique  empties_filtered=%d  dedup_merges=%d  "
        "subquery_overflow_capped=%d",
        len(grouped),
        empties_filtered,
        total_dedup_merges,
        capped_total,
    )

    high: list[_DomainResult] = []
    medium: list[_DomainResult] = []
    for result in grouped.values():
        if result.relevance == "high":
            high.append(result)
        else:
            medium.append(result)

    high.sort(key=_order_key)
    medium.sort(key=_order_key)

    return UnifiedRetrievalArtifact(
        schema_version="2.0",
        query_id=query_id,
        log_id=log_id,
        original_query=original_query,
        produced_at=datetime.now(timezone.utc).isoformat(),
        produced_by={
            "reg_search": bool(reg_rqrs),
            "compliance_search": bool(compliance_rqrs),
            "case_search": bool(case_rqrs),
        },
        sub_queries=sub_queries_meta,
        high_results=high,
        medium_results=medium,
        sector_filter=list(sector_filter or []),
    )


__all__ = ["build_ura_from_phases", "DOMAIN_RANK"]
