"""Aggregator-side unfolder for government services (unified-topic corpus).

Under the unified ``search_topics`` layer, service rows are retrieved by
``reg_search`` and routed to a :class:`ComplianceURAResult` by the type-aware
``reg_adapter`` (services keep the ``compliance`` domain — D9). Unlike the old
compliance loop, the row now carries the FULL structured payload (intro,
steps, requirements, required documents), so this module builds the **rich
aggregator view** (D10 / §1d) instead of the flat ``service_context``
pass-through.

Three-view split (D10):
    reranker   -> ``service_context`` (compact; built in reg_search unfold)
    aggregator -> RICH view (this module: intro + steps + requirements + docs)
    user       -> ``service_context`` only (source viewer, unchanged)

The rich builder is driven from :meth:`ComplianceURAResult.for_aggregator`; it
falls back to ``service_context`` whenever the structured fields are absent
(backward-compat: old stored refs and ``references_service``-reconstructed
shells carry only ``service_context``).

Also hosts :func:`build_ura_metadata`, ported verbatim from the (Wave-4-deleted)
compliance unfolder so the adapter can assemble the typed metadata fields.
"""
from __future__ import annotations

from typing import Any, Iterable

# Compact user/reference view cap. ``service_context`` is already clamped to
# ~2,000 chars by the RPC; this is a defensive net for the dict/legacy path.
MAX_SERVICE_CONTEXT_CHARS = 2_000


def _truncate(text: str | None, limit: int = MAX_SERVICE_CONTEXT_CHARS) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _clean_list(value: Any) -> list[str]:
    """Coerce a ``services.*`` ARRAY column into a clean ``list[str]``.

    Drops ``None`` / blank entries; str()-coerces the rest. Non-list input
    (``None`` for an unpopulated column) yields ``[]``.
    """
    if not value or not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for entry in value:
        if entry is None:
            continue
        s = str(entry).strip()
        if s:
            out.append(s)
    return out


def build_service_context(row_or_text: Any) -> str:
    """Return the compact ``service_context`` (user / reference view, D10).

    Accepts either a ``services`` row dict or a raw string. Defensively capped
    at :data:`MAX_SERVICE_CONTEXT_CHARS`.
    """
    if isinstance(row_or_text, dict):
        text = row_or_text.get("service_context") or ""
    else:
        text = row_or_text or ""
    return _truncate(text)


def build_ura_metadata(row: dict) -> dict:
    """Extract the non-content URA fields from a service row.

    Ported from the retired compliance loop's ``build_ura_metadata``. Returned
    keys mirror the optional fields on
    :class:`agents.deep_search_v4.ura.schema.ComplianceURAResult`;
    ``service_url`` coalesces ``service_url`` then ``url``.
    """
    return {
        "service_ref": row.get("service_ref", "") or "",
        "provider_name": row.get("provider_name", "") or "",
        "service_url": row.get("service_url") or row.get("url", "") or "",
        "sectors": list(row.get("sectors") or []),
        "is_most_used": bool(row.get("is_most_used", False)),
        "is_proactive": bool(row.get("is_proactive", False)),
    }


def build_service_aggregator_content(
    *,
    service_name: str = "",
    intro_title: str = "",
    provider_name: str = "",
    intro_description: str = "",
    steps: Iterable[str] | None = None,
    requirements: Iterable[str] | None = None,
    required_documents: Iterable[str] | None = None,
    service_url: str = "",
    url: str = "",
    fallback_context: str = "",
) -> str:
    """Build the RICH service aggregator view (D10 / §1d).

    Shape::

        خدمة: {intro_title ∥ service_name}
        **الجهة:** {provider_name}
        {intro_description}
        **الخطوات:**
        1. ...
        **المتطلبات:**
        - ...
        **المستندات المطلوبة:**
        - ...
        **الرابط:** {service_url ∥ url}

    Falls back to ``fallback_context`` (the compact ``service_context``) when
    none of the structured fields are present — the backward-compat path for
    old stored refs and ``references_service``-reconstructed shells.
    """
    steps_l = _clean_list(list(steps) if steps is not None else None)
    reqs_l = _clean_list(list(requirements) if requirements is not None else None)
    docs_l = _clean_list(
        list(required_documents) if required_documents is not None else None
    )
    intro = (intro_description or "").strip()

    if not (intro or steps_l or reqs_l or docs_l):
        return (fallback_context or "").strip()

    header = (intro_title or service_name or "").strip()
    lines: list[str] = []
    if header:
        lines.append(f"خدمة: {header}")
    provider = (provider_name or "").strip()
    if provider:
        lines.append(f"**الجهة:** {provider}")
    if intro:
        lines.append(intro)
    if steps_l:
        lines.append("**الخطوات:**")
        lines.extend(f"{i}. {s}" for i, s in enumerate(steps_l, start=1))
    if reqs_l:
        lines.append("**المتطلبات:**")
        lines.extend(f"- {r}" for r in reqs_l)
    if docs_l:
        lines.append("**المستندات المطلوبة:**")
        lines.extend(f"- {d}" for d in docs_l)
    link = (service_url or url or "").strip()
    if link:
        lines.append(f"**الرابط:** {link}")

    return "\n".join(lines).strip()


__all__ = [
    "MAX_SERVICE_CONTEXT_CHARS",
    "build_service_context",
    "build_ura_metadata",
    "build_service_aggregator_content",
]
