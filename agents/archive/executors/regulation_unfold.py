"""Unfolding logic for regulation search results.

Expands retrieved articles, sections, and regulations into full content
with parent context and references. Called by search_pipeline.py.

Copied from agents/deep_search_v2/regulation_unfold.py -- the unfold
functions work with Supabase client directly and are domain-agnostic.
"""
from __future__ import annotations

import logging
from typing import Any

from supabase import Client as SupabaseClient

logger = logging.getLogger(__name__)


# -- Per-result truncation limits ---------------------------------------------

MAX_CONTENT_CHARS = 3_000
MAX_CONTEXT_CHARS = 500
MAX_SIBLINGS_CHARS = 1_500
MAX_REGULATION_META_CHARS = 300


# -- Article unfolding --------------------------------------------------------


def unfold_article(supabase: SupabaseClient, row: dict[str, Any]) -> dict[str, Any]:
    """Unfold an article result into full content with references.

    Args:
        supabase: Supabase client for DB lookups.
        row: Article row from search_articles RPC (includes joined fields).

    Returns:
        Dict with unfolded article content, context, and resolved references.
    """
    result: dict[str, Any] = {
        "source_type": "article",
        "id": row.get("id"),
        "chunk_ref": row.get("chunk_ref", ""),
        "title": row.get("title", ""),
        "article_num": row.get("article_num"),
        "identifier_number": row.get("identifier_number", ""),
        "content": _truncate(row.get("content", ""), MAX_CONTENT_CHARS),
        "regulation_title": row.get("regulation_title", ""),
        "regulation_ref": row.get("regulation_ref", ""),
        "section_title": row.get("section_title", ""),
    }

    # Fetch article_context (not returned by the RPC, need separate query)
    article_id = row.get("id")
    if article_id:
        try:
            detail = (
                supabase.table("articles")
                .select("article_context, references, section_id, regulation_id")
                .eq("id", article_id)
                .maybe_single()
                .execute()
            )
            if detail and detail.data:
                result["article_context"] = _truncate(
                    detail.data.get("article_context", "") or "", MAX_CONTEXT_CHARS
                )
                result["section_id"] = detail.data.get("section_id")
                result["regulation_id"] = detail.data.get("regulation_id")

                # Resolve references JSONB
                refs_json = detail.data.get("references") or []
                result["resolved_references"] = _resolve_article_references(
                    supabase, refs_json
                )
        except Exception as e:
            logger.warning("Failed to fetch article detail %s: %s", article_id, e)
            result["article_context"] = ""
            result["resolved_references"] = []

    return result


def _resolve_article_references(
    supabase: SupabaseClient, refs_json: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Resolve references from an article's JSONB references field.

    Two shapes:
    - Shape A (has article_id): {article_id, regulation_id, article_number, regulation_title}
      -> fetch the referenced article's content
    - Shape B (regulation-only): {regulation_id, regulation_title}
      -> use regulation_title directly
    """
    resolved: list[dict[str, Any]] = []
    for ref in refs_json:
        if not isinstance(ref, dict):
            continue
        try:
            article_id = ref.get("article_id")
            if article_id:
                # Shape A: fetch referenced article content
                ref_detail = (
                    supabase.table("articles")
                    .select("title, content, article_num, identifier_number")
                    .eq("id", article_id)
                    .maybe_single()
                    .execute()
                )
                if ref_detail and ref_detail.data:
                    resolved.append({
                        "type": "article_ref",
                        "article_id": article_id,
                        "regulation_id": ref.get("regulation_id"),
                        "regulation_title": ref.get("regulation_title", ""),
                        "article_number": ref.get("article_number"),
                        "title": ref_detail.data.get("title", ""),
                        "content": _truncate(
                            ref_detail.data.get("content", ""), MAX_CONTEXT_CHARS
                        ),
                    })
                else:
                    # Article not found -- include what we have
                    resolved.append({
                        "type": "article_ref",
                        "regulation_title": ref.get("regulation_title", ""),
                        "article_number": ref.get("article_number"),
                        "note": "لم يُعثر على المادة المشار إليها",
                    })
            else:
                # Shape B: regulation-only reference
                resolved.append({
                    "type": "regulation_ref",
                    "regulation_id": ref.get("regulation_id"),
                    "regulation_title": ref.get("regulation_title", ""),
                })
        except Exception as e:
            logger.warning("Failed to resolve reference %s: %s", ref, e)
            continue
    return resolved


# -- Section unfolding --------------------------------------------------------


def unfold_section(supabase: SupabaseClient, row: dict[str, Any]) -> dict[str, Any]:
    """Unfold a section result with child articles.

    Args:
        supabase: Supabase client for DB lookups.
        row: Section row from search_sections RPC (includes joined fields).

    Returns:
        Dict with section summary, context, and child articles.
    """
    section_id = row.get("id")

    result: dict[str, Any] = {
        "source_type": "section",
        "id": section_id,
        "chunk_ref": row.get("chunk_ref", ""),
        "title": row.get("title", ""),
        "section_summary": _truncate(
            row.get("section_summary", "") or "", MAX_CONTEXT_CHARS
        ),
        "section_keyword": row.get("section_keyword", ""),
        "regulation_title": row.get("regulation_title", ""),
        "regulation_ref": row.get("regulation_ref", ""),
    }

    # Fetch section_context (not returned by RPC)
    if section_id:
        try:
            detail = (
                supabase.table("sections")
                .select("section_context, regulation_id")
                .eq("id", section_id)
                .maybe_single()
                .execute()
            )
            if detail and detail.data:
                result["section_context"] = _truncate(
                    detail.data.get("section_context", "") or "", MAX_CONTEXT_CHARS
                )
                result["regulation_id"] = detail.data.get("regulation_id")
        except Exception as e:
            logger.warning("Failed to fetch section detail %s: %s", section_id, e)

    # Fetch ALL child articles for this section
    if section_id:
        try:
            children = (
                supabase.table("articles")
                .select(
                    "id, title, content, article_num, identifier_number, "
                    "article_context, references, regulation_id"
                )
                .eq("section_id", section_id)
                .order("article_num")
                .execute()
            )
            if children and children.data:
                child_articles: list[dict[str, Any]] = []
                for child in children.data:
                    child_art = {
                        "id": child.get("id"),
                        "title": child.get("title", ""),
                        "article_num": child.get("article_num"),
                        "identifier_number": child.get("identifier_number", ""),
                        "content": _truncate(
                            child.get("content", ""), MAX_CONTENT_CHARS
                        ),
                        "article_context": _truncate(
                            child.get("article_context", "") or "",
                            MAX_CONTEXT_CHARS,
                        ),
                        "regulation_id": child.get("regulation_id"),
                    }
                    # Resolve references for each child article
                    refs_json = child.get("references") or []
                    child_art["resolved_references"] = (
                        _resolve_article_references(supabase, refs_json)
                    )
                    child_articles.append(child_art)
                result["child_articles"] = child_articles
        except Exception as e:
            logger.warning(
                "Failed to fetch child articles for section %s: %s",
                section_id, e,
            )

    return result


# -- Regulation unfolding -----------------------------------------------------


def unfold_regulation(
    supabase: SupabaseClient, row: dict[str, Any]
) -> dict[str, Any]:
    """Unfold a regulation result with child sections (stop at section level).

    Args:
        supabase: Supabase client for DB lookups.
        row: Regulation row from search_regulations RPC.

    Returns:
        Dict with regulation summary, entity info, external refs, and child sections.
    """
    regulation_id = row.get("id")

    result: dict[str, Any] = {
        "source_type": "regulation",
        "id": regulation_id,
        "regulation_ref": row.get("regulation_ref", ""),
        "title": row.get("title", ""),
        "type": row.get("type", ""),
        "main_category": row.get("main_category", ""),
        "sub_category": row.get("sub_category", ""),
        "regulation_summary": _truncate(
            row.get("regulation_summary", "") or "", MAX_CONTENT_CHARS
        ),
        "authority_level": row.get("authority_level", ""),
        "authority_score": row.get("authority_score"),
    }

    # Fetch entity_name if entity_id present
    if regulation_id:
        try:
            reg_detail = (
                supabase.table("regulations")
                .select("entity_id, external_references, source_url, pdf_link")
                .eq("id", regulation_id)
                .maybe_single()
                .execute()
            )
            if reg_detail and reg_detail.data:
                entity_id = reg_detail.data.get("entity_id")
                result["source_url"] = reg_detail.data.get("source_url", "")
                result["pdf_link"] = reg_detail.data.get("pdf_link")

                # Resolve external_references
                ext_refs = reg_detail.data.get("external_references") or []
                result["external_references"] = _resolve_external_references(ext_refs)

                # Fetch entity name
                if entity_id:
                    try:
                        entity = (
                            supabase.table("entities")
                            .select("entity_name")
                            .eq("id", entity_id)
                            .maybe_single()
                            .execute()
                        )
                        if entity and entity.data:
                            result["entity_name"] = entity.data.get(
                                "entity_name", ""
                            )
                    except Exception as e:
                        logger.warning(
                            "Failed to fetch entity for regulation %s: %s",
                            regulation_id, e,
                        )
        except Exception as e:
            logger.warning(
                "Failed to fetch regulation detail %s: %s", regulation_id, e
            )

    # Fetch child sections (stop at section level -- do NOT recurse into articles)
    if regulation_id:
        try:
            sections = (
                supabase.table("sections")
                .select("id, title, section_summary, section_context, chunk_index")
                .eq("regulation_id", regulation_id)
                .order("chunk_index")
                .execute()
            )
            if sections and sections.data:
                child_sections: list[dict[str, Any]] = []
                for sec in sections.data:
                    child_sections.append({
                        "id": sec.get("id"),
                        "title": sec.get("title", ""),
                        "section_summary": _truncate(
                            sec.get("section_summary", "") or "",
                            MAX_CONTEXT_CHARS,
                        ),
                        "section_context": _truncate(
                            sec.get("section_context", "") or "",
                            MAX_CONTEXT_CHARS,
                        ),
                    })
                result["child_sections"] = child_sections
        except Exception as e:
            logger.warning(
                "Failed to fetch child sections for regulation %s: %s",
                regulation_id, e,
            )

    return result


def _resolve_external_references(
    ext_refs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve external_references JSONB from a regulation.

    Shape: {relation, regulation_id, regulation_name}
    No DB lookup needed -- we just pass the data through.
    """
    resolved: list[dict[str, Any]] = []
    for ref in ext_refs:
        if not isinstance(ref, dict):
            continue
        resolved.append({
            "relation": ref.get("relation", ""),
            "regulation_id": ref.get("regulation_id", ""),
            "regulation_name": ref.get("regulation_name", ""),
        })
    return resolved


# -- Reference collection -----------------------------------------------------


def collect_references(all_results: list[dict[str, Any]]) -> str:
    """Collect deduplicated references block from all unfolded results.

    Deduplicates by regulation title. Returns a formatted <references>
    block string for inclusion in the tool output.
    """
    seen_regulations: set[str] = set()
    ref_lines: list[str] = []

    for result in all_results:
        _collect_regulation_ref(result, seen_regulations, ref_lines)

        # Also collect from child articles and their references
        for child in result.get("child_articles", []):
            _collect_regulation_ref(child, seen_regulations, ref_lines)
            for ref in child.get("resolved_references", []):
                reg_title = ref.get("regulation_title", "")
                if reg_title and reg_title not in seen_regulations:
                    seen_regulations.add(reg_title)
                    ref_lines.append(f"- {reg_title}")

        # External references from regulations
        for ext in result.get("external_references", []):
            reg_name = ext.get("regulation_name", "")
            if reg_name and reg_name not in seen_regulations:
                seen_regulations.add(reg_name)
                ref_lines.append(f"- {reg_name}")

        # Resolved references from articles
        for ref in result.get("resolved_references", []):
            reg_title = ref.get("regulation_title", "")
            if reg_title and reg_title not in seen_regulations:
                seen_regulations.add(reg_title)
                ref_lines.append(f"- {reg_title}")

    if not ref_lines:
        return ""

    return "<references>\n" + "\n".join(ref_lines) + "\n</references>"


def _collect_regulation_ref(
    result: dict[str, Any],
    seen: set[str],
    lines: list[str],
) -> None:
    """Add the primary regulation reference for a result if not already seen."""
    reg_title = result.get("regulation_title") or result.get("title", "")
    source_type = result.get("source_type", "")

    # For regulation-type results, also include source_url and pdf_link
    if source_type == "regulation":
        reg_title = result.get("title", "")

    if not reg_title or reg_title in seen:
        return

    seen.add(reg_title)
    parts = [reg_title]

    entity_name = result.get("entity_name", "")
    if entity_name:
        parts.append(entity_name)

    source_url = result.get("source_url", "")
    if source_url:
        parts.append(source_url)

    pdf_link = result.get("pdf_link")
    if isinstance(pdf_link, dict):
        pdf_url = pdf_link.get("url", "")
        if pdf_url:
            parts.append(pdf_url)
    elif isinstance(pdf_link, str) and pdf_link:
        parts.append(pdf_link)

    lines.append(f"- {', '.join(parts)}")


# -- Formatting helpers -------------------------------------------------------


def _format_scores(result: dict[str, Any]) -> str:
    """Build a relevance score line from _score (hybrid RRF) and _reranker_score."""
    parts: list[str] = []
    hybrid_score = result.get("_score")
    if hybrid_score is not None:
        parts.append(f"RRF: {round(float(hybrid_score), 4)}")
    rerank = result.get("_reranker_score")
    if rerank is not None:
        parts.append(f"Jina: {round(float(rerank), 4)}")
    if parts:
        return f"**درجة الصلة:** {' | '.join(parts)}"
    return ""


def format_unfolded_result(result: dict[str, Any], position: int) -> str:
    """Format a single unfolded result into a readable text block.

    Args:
        result: Unfolded result dict from unfold_article/section/regulation.
        position: 1-based position in the ranked list.

    Returns:
        Formatted string block for the LLM to analyze.
    """
    source_type = result.get("source_type", "unknown")

    if source_type == "article":
        return _format_article(result, position)
    elif source_type == "section":
        return _format_section(result, position)
    elif source_type == "regulation":
        return _format_regulation(result, position)
    return f"### [{position}] نتيجة غير معروفة النوع\n"


def _format_article(result: dict[str, Any], position: int) -> str:
    lines: list[str] = []
    title = result.get("title", "بدون عنوان")
    identifier = result.get("identifier_number", "")
    reg_title = result.get("regulation_title", "")
    section_title = result.get("section_title", "")

    header = f"### [{position}] مادة: {title}"
    if identifier:
        header += f" ({identifier})"
    lines.append(header)

    if reg_title:
        lines.append(f"**النظام:** {reg_title}")
    if section_title:
        lines.append(f"**الباب/الفصل:** {section_title}")
    score_line = _format_scores(result)
    if score_line:
        lines.append(score_line)
    lines.append("")

    content = result.get("content", "")
    if content:
        lines.append(f"> {content}")
        lines.append("")

    context = result.get("article_context", "")
    if context:
        lines.append(f"**السياق:** {context}")
        lines.append("")

    # Resolved references
    refs = result.get("resolved_references", [])
    if refs:
        lines.append("**إشارات مرجعية:**")
        for ref in refs:
            if ref.get("type") == "article_ref":
                ref_title = ref.get("regulation_title", "")
                art_num = ref.get("article_number", "")
                ref_content = ref.get("content", "")
                line = f"  - المادة {art_num} من {ref_title}"
                if ref_content:
                    line += f": {ref_content[:200]}"
                lines.append(line)
            elif ref.get("type") == "regulation_ref":
                lines.append(f"  - {ref.get('regulation_title', '')}")
        lines.append("")

    return "\n".join(lines)


def _format_section(result: dict[str, Any], position: int) -> str:
    lines: list[str] = []
    title = result.get("title", "بدون عنوان")
    reg_title = result.get("regulation_title", "")

    lines.append(f"### [{position}] باب/فصل: {title}")
    if reg_title:
        lines.append(f"**النظام:** {reg_title}")
    score_line = _format_scores(result)
    if score_line:
        lines.append(score_line)
    lines.append("")

    summary = result.get("section_summary", "")
    if summary:
        lines.append(f"**ملخص:** {summary}")
        lines.append("")

    context = result.get("section_context", "")
    if context:
        lines.append(f"**السياق:** {context}")
        lines.append("")

    # Child articles
    children = result.get("child_articles", [])
    if children:
        lines.append(f"**المواد ({len(children)}):**")
        for child in children:
            child_title = child.get("title", "")
            child_num = child.get("article_num", "")
            child_content = child.get("content", "")
            lines.append(f"#### المادة {child_num}: {child_title}")
            if child_content:
                lines.append(f"> {child_content}")
            child_context = child.get("article_context", "")
            if child_context:
                lines.append(f"**السياق:** {child_context}")

            # Child article references
            child_refs = child.get("resolved_references", [])
            if child_refs:
                for ref in child_refs:
                    if ref.get("type") == "article_ref":
                        lines.append(
                            f"  - إشارة: المادة {ref.get('article_number', '')} "
                            f"من {ref.get('regulation_title', '')}"
                        )
                    elif ref.get("type") == "regulation_ref":
                        lines.append(
                            f"  - إشارة: {ref.get('regulation_title', '')}"
                        )
            lines.append("")

    return "\n".join(lines)


def _format_regulation(result: dict[str, Any], position: int) -> str:
    lines: list[str] = []
    title = result.get("title", "بدون عنوان")
    reg_type = result.get("type", "")
    entity = result.get("entity_name", "")

    lines.append(f"### [{position}] نظام: {title}")
    if reg_type:
        lines.append(f"**النوع:** {reg_type}")
    if entity:
        lines.append(f"**الجهة:** {entity}")

    authority = result.get("authority_level", "")
    if authority:
        lines.append(f"**المستوى:** {authority}")
    score_line = _format_scores(result)
    if score_line:
        lines.append(score_line)
    lines.append("")

    summary = result.get("regulation_summary", "")
    if summary:
        lines.append(f"**ملخص:** {summary}")
        lines.append("")

    # External references
    ext_refs = result.get("external_references", [])
    if ext_refs:
        lines.append("**أنظمة ذات صلة:**")
        for ext in ext_refs:
            lines.append(
                f"  - {ext.get('regulation_name', '')} ({ext.get('relation', '')})"
            )
        lines.append("")

    # Child sections (section-level only, no articles)
    children = result.get("child_sections", [])
    if children:
        lines.append(f"**الأبواب والفصول ({len(children)}):**")
        for sec in children:
            sec_title = sec.get("title", "")
            sec_summary = sec.get("section_summary", "")
            line = f"  - {sec_title}"
            if sec_summary:
                line += f": {sec_summary[:200]}"
            lines.append(line)
        lines.append("")

    return "\n".join(lines)


# -- Utility ------------------------------------------------------------------


def _truncate(text: str, max_chars: int) -> str:
    """Truncate text to max_chars, appending '...' if truncated."""
    if not text or len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."
