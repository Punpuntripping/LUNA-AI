"""Aggregator-side unfolder for compliance_search.

What the URA / aggregator sees for each kept government service.

Compliance rows are flat — every service carries a single prompt-engineered
narrative field, ``service_context`` (compact, RPC-clamped to ~2,000 chars).
Both the reranker view (``unfold_reranker.py``) and this aggregator view use
``service_context``; there is no separate full-markdown stage. This module
therefore just selects ``service_context`` as the URA ``.content`` and packs
the remaining typed metadata fields.

Counterpart to ``unfold_reranker.py``.
"""
from __future__ import annotations


# URA content cap. ``service_context`` is already clamped to 2,000 chars by
# the hybrid_search_services RPC; this is a defensive no-op safety net for
# the dict/legacy path where a row might bypass the RPC clamp.
MAX_URA_CONTENT_CHARS = 2_000


def _truncate(text: str, limit: int = MAX_URA_CONTENT_CHARS) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def build_ura_content(row: dict) -> str:
    """Return the URA ``.content`` body for a kept compliance row.

    Uses the compact ``service_context`` — the same field the reranker sees.
    Truncated only at :data:`MAX_URA_CONTENT_CHARS` as a defensive cap.
    """
    return _truncate(row.get("service_context") or "")


def build_ura_metadata(row: dict) -> dict:
    """Extract the non-content URA fields from a compliance row.

    Returned keys mirror the optional fields on
    :class:`agents.deep_search_v4.ura.schema.ComplianceURAResult`. The
    adapter assembles these alongside :func:`build_ura_content` and the
    ref_id it mints itself.
    """
    return {
        "service_ref": row.get("service_ref", "") or "",
        "provider_name": row.get("provider_name", "") or "",
        "service_url": row.get("service_url") or row.get("url", "") or "",
        "sectors": list(row.get("sectors") or []),
        "is_most_used": bool(row.get("is_most_used", False)),
        "is_proactive": bool(row.get("is_proactive", False)),
    }


__all__ = [
    "MAX_URA_CONTENT_CHARS",
    "build_ura_content",
    "build_ura_metadata",
]
