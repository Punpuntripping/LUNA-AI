"""Convert ComplianceSearchResult into a URA-shaped ComplianceURASlice.

Standalone — no partial-URA dependency. Compliance now runs as a peer domain
in the URA pipeline and emits its slice directly from its own kept_results.
"""
from __future__ import annotations

import hashlib
import logging

from .models import ComplianceSearchResult, ComplianceURASlice

logger = logging.getLogger(__name__)


def _compliance_ref_id(service_ref: str) -> str:
    """Stable ref_id from a service_ref string: ``compliance:<16-char-sha1>``."""
    if not service_ref:
        return ""
    digest = hashlib.sha1(service_ref.encode("utf-8")).hexdigest()[:16]
    return f"compliance:{digest}"


def build_compliance_slice(result: ComplianceSearchResult) -> ComplianceURASlice:
    """Convert raw kept_results into URA-shaped dicts for the merger."""
    ura_dicts: list[dict] = []
    seen_ref_ids: set[str] = set()

    for row in result.kept_results:
        service_ref = row.get("service_ref", "")
        if not service_ref:
            continue

        ref_id = _compliance_ref_id(service_ref)
        if not ref_id or ref_id in seen_ref_ids:
            continue
        seen_ref_ids.add(ref_id)

        ura_dicts.append({
            "ref_id": ref_id,
            "domain": "compliance",
            "source_type": "gov_service",
            "title": row.get("service_name_ar", ""),
            "content": row.get("service_context", ""),
            "metadata": {
                "service_ref": service_ref,
                "provider_name": row.get("provider_name", ""),
                "platform_name": row.get("platform_name", ""),
                "service_url": row.get("service_url") or row.get("url", ""),
                "service_markdown": row.get("service_markdown", ""),
                "target_audience": row.get("target_audience") or [],
                "service_channels": row.get("service_channels") or [],
                "is_most_used": row.get("is_most_used", False),
                "relevance_note": row.get("_reasoning", ""),
            },
            "relevance": row.get("_relevance", "medium"),
            "reasoning": row.get("_reasoning", ""),
            "appears_in_sub_queries": [],
            "rrf_max": float(row.get("score", 0.0)),
            "triggered_by_ref_ids": [],
            "cross_references": [],
        })

    logger.info(
        "build_compliance_slice: %d kept rows → %d unique URA dicts",
        len(result.kept_results),
        len(ura_dicts),
    )

    return ComplianceURASlice(
        results=ura_dicts,
        queries_used=list(result.queries_used),
    )


__all__ = ["build_compliance_slice"]
