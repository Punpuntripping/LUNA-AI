"""Aggregator-side unfolder for compliance_search.

What the URA / aggregator sees — the FULL original `service_markdown` for
each kept government service, so synthesis can quote and reason from the
real source text rather than the compact reranker context.

Why a dedicated module:
    Compliance rows carry two narrative fields:
      * `service_context`   — compact (~600 chars), prompt-engineered for
                              retrieval/reranking.
      * `service_markdown`  — full original service description from the
                              source platform (potentially several KB).

    Stage 1 (`unfold_reranker.py`) hands the LLM reranker the compact
    `service_context` because keep/drop classification doesn't need the
    full text and a 30-row pool would otherwise blow the prompt budget.

    Stage 2 (this module) is the opposite trade-off: there are far fewer
    rows after the reranker has filtered, and the aggregator must produce a
    grounded synthesis — so it gets the full `service_markdown`, only
    clipped at `MAX_URA_CONTENT_CHARS` as a safety guard.

    `service_markdown` falls back to `service_context` only when the row
    has no markdown at all (defensive — should be rare in practice).

Counterpart to `unfold_reranker.py`.
"""
from __future__ import annotations


# URA content cap. Compliance markdown is normally a few KB but can spike;
# 8_000 chars keeps a single service from monopolising the aggregator
# context window while still giving room for procedure steps, eligibility
# rules, channel matrices, and fee schedules. Tune alongside the Wave B
# merger budget if needed.
MAX_URA_CONTENT_CHARS = 8_000


def _truncate(text: str, limit: int = MAX_URA_CONTENT_CHARS) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _stringify(value) -> str:
    """Coerce arrays/None into a single comma-joined string for the URA."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(x) for x in value if x)
    return str(value)


def build_ura_content(row: dict) -> str:
    """Return the URA `.content` body for a kept compliance row.

    Stage 2 contract: hand the aggregator the FULL ``service_markdown``,
    truncated only at :data:`MAX_URA_CONTENT_CHARS` as a safety cap. Falls
    back to the compact ``service_context`` if the row has no markdown
    (defensive — shouldn't happen for healthy rows).
    """
    service_markdown = row.get("service_markdown") or ""
    if service_markdown:
        return _truncate(service_markdown)

    fallback = row.get("service_context") or ""
    return _truncate(fallback)


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
        "platform_name": row.get("platform_name", "") or "",
        "service_url": row.get("service_url") or row.get("url", "") or "",
        "service_markdown": row.get("service_markdown", "") or "",
        "target_audience": _stringify(row.get("target_audience")),
        "service_channels": _stringify(row.get("service_channels")),
        "is_most_used": bool(row.get("is_most_used", False)),
    }


__all__ = [
    "MAX_URA_CONTENT_CHARS",
    "build_ura_content",
    "build_ura_metadata",
]
