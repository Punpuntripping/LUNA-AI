"""Report builder for deep_search_v2.

Pure Python -- no LLM. Wraps the aggregator's synthesis into a complete
markdown report artifact with title, header, and citations section.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def build_report(
    synthesis_md: str,
    citations: list[dict],
    question: str,
    title: str | None = None,
) -> str:
    """Build a complete markdown report from aggregator synthesis.

    Args:
        synthesis_md: The aggregator's synthesis_md output (Arabic legal analysis).
        citations: List of citation dicts from the aggregator.
        question: The original user question (used to generate title if needed).
        title: Optional explicit title. Generated from question if not provided.

    Returns:
        Complete markdown report string ready for artifact storage.
    """
    # Generate title from question if not provided
    if not title:
        # Use first 80 chars of question, cleaned up
        title = question.strip()
        if len(title) > 80:
            title = title[:77] + "..."
        title = f"تقرير بحث: {title}"

    # Header
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = [
        f"# {title}",
        "",
        f"*تاريخ التقرير: {date_str}*",
        "",
        "---",
        "",
    ]

    # Main body (synthesis from aggregator)
    if synthesis_md:
        lines.append(synthesis_md.strip())
    else:
        lines.append("*لم يتم إنتاج تحليل.*")

    lines.append("")

    # Citations / References section
    if citations:
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## المراجع والاستشهادات")
        lines.append("")

        for i, cite in enumerate(citations, 1):
            ref = cite.get("ref", "")
            title_text = cite.get("title", "")
            source_type = cite.get("source_type", "")
            regulation_title = cite.get("regulation_title", "")
            article_num = cite.get("article_num", "")
            court = cite.get("court", "")
            relevance = cite.get("relevance", "")

            # Build citation line
            parts: list[str] = []
            if ref:
                parts.append(f"**{ref}**")
            if title_text:
                parts.append(title_text)
            if regulation_title:
                parts.append(f"({regulation_title})")
            if article_num:
                parts.append(f"المادة {article_num}")
            if court:
                parts.append(f"| {court}")

            citation_line = " — ".join(parts) if parts else f"مرجع {i}"

            source_labels = {
                "regulation": "نظام",
                "article": "مادة",
                "section": "باب/فصل",
                "case": "حكم قضائي",
                "service": "خدمة حكومية",
            }
            source_label = source_labels.get(source_type, source_type)

            lines.append(f"{i}. [{source_label}] {citation_line}")
            if relevance:
                lines.append(f"   *{relevance}*")
            lines.append("")

    # Footer
    lines.append("")
    lines.append("---")
    lines.append("*تم إنتاج هذا التقرير بواسطة منصة لونا للذكاء الاصطناعي القانوني.*")

    return "\n".join(lines)
