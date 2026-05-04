"""Reranker-side unfolder for compliance_search.

Produces the markdown blocks the LLM reranker (`ServiceReranker`) consumes
when classifying each candidate government service as keep/drop.

Why a dedicated module:
    Compliance results carry two narrative fields per service:
      * `service_context`   — compact, prompt-engineered description
                              (~600 chars) intended for retrieval/reranking.
      * `service_markdown`  — full original service description
                              (potentially several KB).

    Stage 1 (this module) deliberately uses `service_context`. The reranker
    only needs enough text to grade relevance against the focus instruction,
    and the compact form keeps the prompt cheap when there are 30+ services
    in the pool. Stage 2 (`unfold_ura.py`) is the one that hands the full
    `service_markdown` to the URA / aggregator.

Mirrors the split in case_search:
    unfold_reranker.py — compact, what the reranker sees
    unfold_ura.py      — full, what the aggregator sees
"""
from __future__ import annotations


# Compact context cap for the reranker view. The DB-side `service_context`
# is already engineered to be short, but defensively clip in case some rows
# come in long. Tuning this up does NOT widen the aggregator view (that's
# governed by `unfold_ura.MAX_URA_CONTENT_CHARS`).
MAX_RERANKER_CONTEXT_CHARS = 600


def _format_service_block(row: dict, position: int) -> str:
    """Format a single service row as a markdown block for the reranker.

    Uses ``service_context`` (compact, ~600 chars) — NOT ``service_markdown``.
    The reranker classifies keep/drop; it does not need the full source text.
    """
    lines: list[str] = []

    service_name_ar = row.get("service_name_ar") or ""
    service_ref = row.get("service_ref") or ""
    lines.append(f"### [{position}] خدمة: {service_name_ar} [ref:{service_ref}]")

    provider_name = row.get("provider_name") or ""
    if provider_name:
        lines.append(f"**الجهة:** {provider_name}")

    platform_name = row.get("platform_name") or ""
    if platform_name:
        lines.append(f"**المنصة:** {platform_name}")

    target_audience = row.get("target_audience") or []
    if target_audience:
        audience_str = ", ".join(target_audience[:3])
        lines.append(f"**الجمهور:** {audience_str}")

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
    max_high: int = 0,
    max_medium: int = 0,
) -> str:
    """Build the user message for the ServiceReranker agent.

    The reranker view always uses the compact ``service_context`` block
    produced by :func:`_format_service_block`.

    Args:
        focus_instruction: The compliance focus instruction (Arabic).
        all_results_flat: Flat list of all service result dicts.
        round_count: Which retrieval round (1=initial, 2+=retry).
        n_queries: Number of expander queries executed in this round.
        max_high: If nonzero, inject a cap instruction into the prompt.
        max_medium: If nonzero, inject a cap instruction into the prompt.
    """
    formatted_blocks = "\n\n".join(
        _format_service_block(row, i + 1) for i, row in enumerate(all_results_flat)
    )

    cap_note = ""
    if max_high > 0 and max_medium > 0:
        cap_note = (
            f"\n**تعليمات الحد الأقصى:** احتفظ بحد أقصى {max_high} خدمة عالية الصلة "
            f"و{max_medium} خدمة متوسطة الصلة في مجموع النتائج.\n"
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
