"""Adapt compliance_search graph output -> shared RerankerQueryResult /
ComplianceURAResult.

Compliance's reranker runs **once** over the de-duplicated service pool, so
the final ``ComplianceSearchResult.kept_results`` does not itself record
which sub-query surfaced each service. To preserve per-sub-query attribution
(Option A, decided in the Loop V2 plan Q1), the orchestrator must capture
the per-query ``service_ref`` lists inside ``SearchNode`` and hand them to
this adapter as ``per_query_service_refs``.

Until Wave D wires that plumbing, ``per_query_service_refs`` is optional;
when absent this adapter falls back to emitting a single lumped
``RerankerQueryResult`` that covers every kept service (Option B fallback).
A ``# TODO(Wave D)`` marker flags the intended wiring.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from agents.deep_search_v4.compliance_search.models import (
    ComplianceSearchResult,
)
from agents.deep_search_v4.ura.compliance_unfold import (
    build_ura_content,
    build_ura_metadata,
)
from agents.deep_search_v4.shared.models import (
    RerankerQueryResult as SharedRQR,
)
from agents.deep_search_v4.ura.schema import ComplianceURAResult


def _compliance_ref_id(service_ref: str, service_url: str = "") -> str:
    """Stable ref_id: ``compliance:<16-char-sha1>`` over service_ref (fallback: url)."""
    seed = service_ref or service_url
    if not seed:
        return ""
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    return f"compliance:{digest}"


def _service_to_ura(row: "RerankedServiceResult | dict") -> ComplianceURAResult | None:
    """Map one kept service (typed or dict) to a ``ComplianceURAResult``.

    Accepts both ``RerankedServiceResult`` (post-Phase 3 typed path) and a
    raw ``dict`` (legacy/test path) for backward compatibility.

    Stage 2 unfolding lives in :mod:`agents.deep_search_v4.ura.compliance_unfold`;
    this function is a thin
    assembler that mints the ref_id (an adapter concern) and stitches the
    URA-side content + metadata together.

    Returns ``None`` when no stable ``ref_id`` can be constructed.
    """
    from agents.deep_search_v4.compliance_search.models import RerankedServiceResult

    if isinstance(row, RerankedServiceResult):
        service_ref = row.service_ref
        service_url = row.service_url
        raw_row: dict = {
            "service_ref": row.service_ref,
            "service_name_ar": row.title,
            "service_context": row.content,
            "provider_name": row.provider_name,
            "service_url": row.service_url,
            "sectors": row.sectors,
            "is_proactive": row.is_proactive,
            "score": row.score,
        }
        relevance = row.relevance
        reasoning = row.reasoning
    else:
        service_ref = row.get("service_ref", "") or ""
        service_url = row.get("service_url") or row.get("url", "") or ""
        raw_row = row
        relevance = row.get("_relevance", "medium")
        reasoning = row.get("_reasoning", "") or ""

    ref_id = _compliance_ref_id(service_ref, service_url)
    if not ref_id:
        return None

    # build_ura_metadata returns service_ref / provider_name / service_url /
    # sectors / is_most_used / is_proactive -- all valid ComplianceURAResult
    # fields. v3.0 renames: service_name_ar -> service_name, content ->
    # service_context. `url` (the service_url fallback) is only present on the
    # dict path; "" on the typed path -- RerankedServiceResult has no `url`.
    metadata = build_ura_metadata(raw_row)

    return ComplianceURAResult(
        ref_id=ref_id,
        source_type="gov_service",
        relevance=relevance,
        reasoning=reasoning,
        appears_in_sub_queries=[],
        rrf_max=float(raw_row.get("score", 0.0) or 0.0),
        service_name=raw_row.get("service_name_ar", "") or "",
        service_context=build_ura_content(raw_row),
        url=raw_row.get("url", "") or "",
        **metadata,
    )


def compliance_to_rqr(
    result: ComplianceSearchResult,
    per_query_service_refs: Mapping[str, Sequence[str]] | None = None,
    *,
    per_query_dropped: Mapping[str, Sequence[dict]] | None = None,
    original_focus_instruction: str = "",
) -> list[SharedRQR]:
    """Convert a ``ComplianceSearchResult`` into shared ``RerankerQueryResult``s.

    Args:
        result: The compliance loop's final output.
        per_query_service_refs: Optional mapping ``query -> [service_ref, ...]``
            recording which mini-query surfaced each service. Populated by the
            orchestrator's compliance phase wrapper (Wave D); until then, the
            adapter falls back to a single lumped ``RerankerQueryResult``.
        per_query_dropped: Optional parallel mapping ``query -> [dropped_dict,
            ...]`` recording which services this sub-query's reranker dropped
            (LLM-drop or cap-truncation). Populated alongside
            ``per_query_service_refs`` by the RerankerNode; feeds the per-query
            ``dropped_forensic`` list. Each dict carries ``service_id`` / ``title``
            / ``reasoning`` / ``drop_reason``.
        original_focus_instruction: Fallback query label for the lumped
            RQR when ``per_query_service_refs`` is absent and
            ``queries_used`` is also empty.

    Returns:
        One ``SharedRQR`` per compliance sub-query (Option A) or one lumped
        ``SharedRQR`` covering every kept service (Option B fallback).
    """
    # Map ref_id -> ComplianceURAResult for all kept services.
    ura_by_ref: dict[str, ComplianceURAResult] = {}
    # Also keep a parallel map: service_ref -> ref_id, so the caller's
    # per-query-service_refs mapping can be translated without re-hashing.
    ref_id_by_service_ref: dict[str, str] = {}
    # Forensic side-maps keyed by ref_id: the real services.id UUID (the
    # forensic ref_id — distinct from the citation ref_id hash) and the Arabic
    # service name. The kept ComplianceURAResult carries neither, so we stash
    # them here off the original RerankedServiceResult/dict row.
    service_id_by_ref_id: dict[str, str] = {}
    title_by_ref_id: dict[str, str] = {}
    from agents.deep_search_v4.compliance_search.models import RerankedServiceResult

    for row in result.kept_results or []:
        ura = _service_to_ura(row)
        if ura is None:
            continue
        if ura.ref_id in ura_by_ref:
            continue
        ura_by_ref[ura.ref_id] = ura
        if isinstance(row, RerankedServiceResult):
            service_ref = row.service_ref or ""
            service_id_by_ref_id[ura.ref_id] = row.service_id or ""
            title_by_ref_id[ura.ref_id] = row.title or ""
        else:
            service_ref = row.get("service_ref", "") or ""
            service_id_by_ref_id[ura.ref_id] = (
                row.get("service_id", "") or row.get("id", "") or ""
            )
            title_by_ref_id[ura.ref_id] = row.get("service_name_ar", "") or ""
        if service_ref:
            ref_id_by_service_ref[service_ref] = ura.ref_id

    # Per-query attribution path: SearchNode populates
    # ``state.per_query_service_refs`` before dedup; the orchestrator's
    # compliance phase forwards it here as ``per_query_service_refs``.
    # One SharedRQR per sub-query with precise service attribution.
    if per_query_service_refs:
        out: list[SharedRQR] = []
        for query, refs in per_query_service_refs.items():
            typed: list[ComplianceURAResult] = []
            kept_forensic: list[dict] = []
            seen_ref_ids: set[str] = set()
            seen_input_refs: set[str] = set()
            for sref in refs or []:
                if sref in seen_input_refs:
                    continue
                seen_input_refs.add(sref)
                ref_id = ref_id_by_service_ref.get(sref)
                if not ref_id or ref_id in seen_ref_ids:
                    continue
                ura = ura_by_ref.get(ref_id)
                if ura is None:
                    continue
                typed.append(ura)
                seen_ref_ids.add(ref_id)
                # Forensic: bare services.id UUID + the Arabic service name
                # (the ComplianceURAResult carries neither). 1:1 with kept.
                kept_forensic.append({
                    "source_table": "services",
                    "ref_id": service_id_by_ref_id.get(ref_id, ""),
                    "title": title_by_ref_id.get(ref_id, ""),
                    "relevance": ura.relevance,
                    "source_type": ura.source_type or "gov_service",
                    "reasoning": ura.reasoning or "",
                })
            # dropped_forensic — LLM-drop + cap-truncation rows the RerankerNode
            # recorded for this sub-query (keyed by the same query string).
            dropped_forensic = [
                {
                    "source_table": "services",
                    "ref_id": d.get("service_id", ""),
                    "title": d.get("title", ""),
                    "drop_reason": d.get("drop_reason", "llm"),
                    "reasoning": d.get("reasoning", "") or "",
                    "source_type": "gov_service",
                }
                for d in ((per_query_dropped or {}).get(query, []) or [])
                if d.get("service_id", "")
            ]
            # H6 — dropped = unique services that entered this sub-query
            # but the reranker did not keep. Mirrors reg_search's
            # per-sub-query accounting so the URA's `rqr_table.md` stops
            # showing a column of zeros for compliance.
            dropped_count = max(0, len(seen_input_refs) - len(typed))
            out.append(
                SharedRQR(
                    query=query,
                    rationale="",
                    sufficient=True,
                    domain="compliance",
                    results=typed,
                    dropped_count=dropped_count,
                    summary_note="",
                    kept_forensic=kept_forensic,
                    dropped_forensic=dropped_forensic,
                )
            )
        return out

    # -------------------------------------------------------------------
    # Fallback (Option B): lump all kept services into one SharedRQR.
    # Chosen when the orchestrator has not yet provided per-query
    # attribution. Aggregator still sees every service, but it cannot
    # split the synthesis by compliance sub-task.
    # -------------------------------------------------------------------
    label = ""
    if result.queries_used:
        label = result.queries_used[0]
    if not label:
        label = original_focus_instruction or "compliance"

    lumped_kept_forensic = [
        {
            "source_table": "services",
            "ref_id": service_id_by_ref_id.get(ref_id, ""),
            "title": title_by_ref_id.get(ref_id, ""),
            "relevance": ura.relevance,
            "source_type": ura.source_type or "gov_service",
            "reasoning": ura.reasoning or "",
        }
        for ref_id, ura in ura_by_ref.items()
    ]

    return [
        SharedRQR(
            query=label,
            rationale="",
            sufficient=True,
            domain="compliance",
            results=list(ura_by_ref.values()),
            dropped_count=0,
            summary_note=(
                "Fallback: per-query attribution unavailable; all kept "
                "services grouped into a single RQR."
            ),
            kept_forensic=lumped_kept_forensic,
            dropped_forensic=[],
        )
    ]


__all__ = ["compliance_to_rqr"]
