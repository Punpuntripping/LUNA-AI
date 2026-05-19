"""Reranker-side unfolder for compliance_search.

Produces the markdown blocks the LLM reranker (`ServiceReranker`) consumes
when classifying each candidate government service as keep/drop.

Why a dedicated module:
    Compliance results carry a single prompt-engineered narrative field per
    service, `service_context` (compact, RPC-clamped to ~2,000 chars). This
    module formats it — plus the service name, provider, sectors and RRF
    score — into the markdown block the reranker grades.

    `ura/compliance_unfold.py` is the aggregator-side counterpart; it selects
    the same `service_context` as the URA `.content`.
"""
from __future__ import annotations


# Compact context cap for the reranker view. The DB-side `service_context`
# is already engineered to be short, but defensively clip in case some rows
# come in long. Tuning this up does NOT widen the aggregator view (that's
# governed by `ura.compliance_unfold.MAX_URA_CONTENT_CHARS`).
MAX_RERANKER_CONTEXT_CHARS = 600


def _format_service_block(row: dict, position: int) -> str:
    """Format a single service row as a markdown block for the reranker.

    Uses ``service_context`` — the compact (~600 chars) narrative field —
    so a 30-row pool stays within the reranker's token budget.
    """
    lines: list[str] = []

    service_name_ar = row.get("service_name_ar") or ""
    service_ref = row.get("service_ref") or ""
    lines.append(f"### [{position}] خدمة: {service_name_ar} [ref:{service_ref}]")

    provider_name = row.get("provider_name") or ""
    if provider_name:
        lines.append(f"**الجهة:** {provider_name}")

    sectors = row.get("sectors") or []
    if sectors:
        lines.append(f"**القطاع:** {', '.join(str(s) for s in sectors[:3])}")

    score = row.get("score") or row.get("rrf_score") or 0.0
    lines.append(f"**RRF:** {score:.4f}")

    lines.append("")

    service_context = row.get("service_context") or ""
    if len(service_context) > MAX_RERANKER_CONTEXT_CHARS:
        service_context = service_context[:MAX_RERANKER_CONTEXT_CHARS] + "..."
    lines.append(service_context)

    lines.append("")

    service_url = row.get("service_url") or row.get("url") or ""
    lines.append(f"**الرابط:** {service_url if service_url else '—'}")

    lines.append("---")

    return "\n".join(lines)


def build_reranker_user_message(
    focus_instruction: str,
    all_results_flat: list[dict],
    round_count: int,
    n_queries: int,
    *,
    max_keep: int = 0,
) -> str:
    """Build the user message for the ServiceReranker agent.

    The reranker view always uses the compact ``service_context`` block
    produced by :func:`_format_service_block`.

    Args:
        focus_instruction: The compliance focus instruction (Arabic).
        all_results_flat: Flat list of all service result dicts.
        round_count: Which retrieval round (1=initial, 2+=retry).
        n_queries: Number of expander queries executed in this round.
        max_keep: If nonzero, inject a flat cap instruction into the prompt.
    """
    formatted_blocks = "\n\n".join(
        _format_service_block(row, i + 1) for i, row in enumerate(all_results_flat)
    )

    cap_note = ""
    if max_keep > 0:
        cap_note = (
            f"\n**تعليمات الحد الأقصى:** احتفظ بحد أقصى {max_keep} خدمة "
            f"في مجموع النتائج.\n"
        )

    if round_count == 1:
        return (
            f"## تعليمات التركيز\n"
            f"{focus_instruction}\n"
            f"{cap_note}"
            f"\n"
            f"---\n"
            f"\n"
            f"## نتائج الخدمات الحكومية — {len(all_results_flat)} خدمة من {n_queries} استعلام\n"
            f"\n"
            f"{formatted_blocks}"
        )

    return (
        f"## تعليمات التركيز\n"
        f"{focus_instruction}\n"
        f"{cap_note}"
        f"\n"
        f"**الجولة {round_count}:** نتائج إضافية بعد إعادة البحث في المحاور الضعيفة. صنّف جميع النتائج المعروضة.\n"
        f"\n"
        f"---\n"
        f"\n"
        f"## نتائج الخدمات الحكومية — {len(all_results_flat)} خدمة (مجمّعة من {round_count} جولات)\n"
        f"\n"
        f"{formatted_blocks}"
    )


__all__ = [
    "MAX_RERANKER_CONTEXT_CHARS",
    "build_reranker_user_message",
]
