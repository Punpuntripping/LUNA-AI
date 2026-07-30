"""Single-pass merger: 3 executor phase outputs -> Unified Retrieval Artifact 2.0.

Wave B of the Loop V2 refactor replaced the legacy two-stage pipeline
(``reg_reranker_results -> PartialURA -> merge_to_ura``) with a single
pass that consumes the shared ``RerankerQueryResult`` streams produced by
the reg_compliance and case executors and emits a fully tiered
``UnifiedRetrievalArtifact``. The standalone compliance executor was retired
(Wave 4) — government services now surface inside the reg_compliance stream as
typed ``ComplianceURAResult`` results (routed by the type-aware reg adapter),
so the merger still tiers them by their own ``result.domain`` ("compliance").

Identity rules
--------------
Each domain emits its own namespaced ``ref_id``:
    - regulations -> ``"reg:<db_id>"``
    - compliance  -> ``"compliance:<sha1(service_ref)[:16]>"``  (services)
    - circulars   -> ``"circular:<circulars.id>"``
    - cases       -> ``"case:<db_id>"``
Because the prefixes are disjoint, cross-DOMAIN dedup is a no-op; the merger
still uses a single ``grouped`` dict keyed by ``ref_id`` for simplicity. The
shared ``compliance:`` prefix still lets a service surfaced by more than one
reg_compliance sub-query dedup to one ref.

Sub-query indexing
------------------
Sub-queries carry a **global** index across the phases in absorption order:
regulations first, then cases. So if reg absorbs 3 sub-queries, the first
case sub-query lands at global index 3.

Dedup semantics (within a domain)
--------------------------------
When the same ``ref_id`` appears in multiple sub-queries of the same
domain the merger:
    - unions ``appears_in_sub_queries`` (sorted ascending),
    - lifts ``relevance`` to ``max("high" > "medium")``,
    - joins ``reasoning`` with ``"؛ "`` preserving first-seen order and
      deduping empty / repeated strings,
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
    CircularURAResult,
    ComplianceURAResult,
    Domain,
    RegURAResult,
    UnifiedRetrievalArtifact,
)

logger = logging.getLogger(__name__)

# Module-level rank tables. Tweak DOMAIN_RANK in Wave D if the default
# ordering needs to change (e.g. surface cases before regulations for
# precedent-heavy queries). Absolute values are irrelevant -- only the relative
# order matters (regulations > cases > circulars > compliance).
DOMAIN_RANK: dict[str, int] = {
    "regulations": 4,
    "cases": 3,
    "circulars": 2,
    "compliance": 1,
}

_RELEVANCE_RANK: dict[str, int] = {"high": 2, "medium": 1}

# ── The merger does NOT discard kept results (2026-07-25) ────────────────────
#
# It used to. Historic caps were MAX_HIGH_PER_SUBQUERY = 12 and
# MAX_MEDIUM_PER_SUBQUERY = 4, applied here AFTER each domain's reranker had
# already decided (and paid tokens for) its keeps. That was wrong on two counts:
#
#   1. **It silently deleted work.** The tier split meant a sub-query with 8
#      legitimate `medium` keeps surfaced only 4 — while up to 12 `high` slots
#      sat unused. Cases were hit hardest: the cross-forum rule (a
#      non-specialised court applying the same principle is a KEEP at `medium`)
#      deliberately produces mostly mediums, so the cap deleted exactly the
#      material it exists to surface. Same shape for reg, whose reranker cap is
#      8 total: 8 mediums in, 4 out.
#   2. **It truncated on the wrong signal.** Survival was ordered by `rrf_max`
#      — retrieval cosine similarity — discarding the reranker's own judgement
#      of which results mattered. A semantic decision undone by a lexical score.
#
# The caps' own docstring conceded they were "a last-resort backstop" from an
# era before the domain rerankers had caps of their own. They do now (cases 7,
# reg 8, and each is applied where the decision belongs — at the reranker, with
# the candidate text in view). So the backstop is obsolete, and the merger's job
# is now purely: dedupe by ref_id, lift relevance, union sub-query indices,
# tier-order.
#
# What replaces the cap: a diagnostic. If a sub-query ever arrives with an
# implausible number of keeps, that is a RERANKER regression and must be visible
# in logs — not hidden by silent truncation here.
SUBQUERY_KEEP_WARN_THRESHOLD = 15

_DomainResult = Union[
    RegURAResult, ComplianceURAResult, CircularURAResult, CaseURAResult
]


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
    case_rqrs: list[RerankerQueryResult] | None = None,
    *,
    original_query: str,
    query_id: int = 0,
    log_id: str = "",
    sector_filter: list[str] | None = None,
) -> UnifiedRetrievalArtifact:
    """Build a URA 2.0 artifact from the executor phase outputs.

    Any phase may be empty (that domain was skipped or failed). Each
    sub-query gets a global index, each result is deduped by ``ref_id``
    within its domain, and the final two tier lists (``high_results`` /
    ``medium_results``) are sorted by ``DOMAIN_RANK`` then ``rrf_max``.

    Parameters
    ----------
    reg_rqrs, case_rqrs:
        Shared ``RerankerQueryResult`` lists from each domain's adapter.
        May be empty. Government services arrive inside ``reg_rqrs`` (typed
        ``ComplianceURAResult`` by the type-aware reg adapter — the standalone
        compliance executor was retired in Wave 4); the merger still routes
        each row by its own ``result.domain`` so services tier under
        ``"compliance"``.
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

    # v3.0: no merger-side empty-content filter. Content is domain-specific
    # and unpopulated at merge time -- enrich.py runs later and owns the
    # post-fetch empty-drop. The merger keeps only the ref_id-presence check.

    def _order(results: list) -> list:
        """Order one sub-query's keeps: `high` first, then `medium`.

        **Never truncates** — every kept result reaches the artifact. The
        reranker already decided what to keep, with the candidate text in view;
        re-deciding that here on a retrieval score would overrule a semantic
        judgement with a lexical one. See the module-level note above.

        Ordering still matters: the preprocessor numbers citations in tier
        order, so `high` must precede `medium`. Within a tier, `rrf_max` desc
        (ties: input order).
        """
        if not results:
            return []
        highs: list = [r for r in results if getattr(r, "relevance", "") == "high"]
        meds: list = [r for r in results if getattr(r, "relevance", "") != "high"]
        highs.sort(key=lambda r: -float(getattr(r, "rrf_max", 0.0) or 0.0))
        meds.sort(key=lambda r: -float(getattr(r, "rrf_max", 0.0) or 0.0))
        return highs + meds

    overflow_subqueries = 0

    def _absorb(domain: Domain, rqrs: list[RerankerQueryResult]) -> None:
        nonlocal overflow_subqueries
        for sq in rqrs or []:
            sq_index = len(sub_queries_meta)
            raw_results = list(sq.results or [])
            ordered_results = _order(raw_results)
            if len(raw_results) > SUBQUERY_KEEP_WARN_THRESHOLD:
                # Not truncated — surfaced. An implausible keep count is a
                # reranker-side regression (e.g. doctrinal saturation filling a
                # window with restatements of one holding) and must be
                # diagnosable rather than silently trimmed here.
                overflow_subqueries += 1
                logger.warning(
                    "ura.merger: sub-query %d [%s] kept %d results (> %d) — "
                    "NOT truncated; check the %s reranker's keep cap",
                    sq_index, domain, len(raw_results),
                    SUBQUERY_KEEP_WARN_THRESHOLD, domain,
                )
            meta: dict = {
                "index": sq_index,
                "query": sq.query,
                "rationale": sq.rationale,
                "domain": domain,
                "sufficient": bool(sq.sufficient),
                "kept_count": len(ordered_results),
                "raw_kept_count": len(raw_results),
                "dropped_count": int(sq.dropped_count or 0),
            }
            if sq.summary_note:
                meta["summary_note"] = sq.summary_note
            sub_queries_meta.append(meta)

            for result in ordered_results:
                ref_id = getattr(result, "ref_id", "") or ""
                if not ref_id:
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
                incoming_rrf = float(getattr(result, "rrf_max", 0.0) or 0.0)
                if incoming_rrf > float(existing.rrf_max or 0.0):
                    existing.rrf_max = incoming_rrf

    _absorb("regulations", reg_rqrs)
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
        "ura.merger: kept=%d unique  dedup_merges=%d  "
        "subqueries_over_warn_threshold=%d (none truncated)",
        len(grouped),
        total_dedup_merges,
        overflow_subqueries,
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
        schema_version="3.0",
        query_id=query_id,
        log_id=log_id,
        original_query=original_query,
        produced_at=datetime.now(timezone.utc).isoformat(),
        produced_by={
            # Executor-family keys (data contract — persisted on
            # retrieval_artifacts.produced_by, read by aggregator domain
            # derivation). ``compliance_search`` stays in the dict but is always
            # False now: the standalone compliance executor was retired and its
            # services ride inside the reg_compliance stream.
            "reg_search": bool(reg_rqrs),
            "compliance_search": False,
            "case_search": bool(case_rqrs),
        },
        sub_queries=sub_queries_meta,
        high_results=high,
        medium_results=medium,
        sector_filter=list(sector_filter or []),
    )


__all__ = ["build_ura_from_phases", "DOMAIN_RANK"]
