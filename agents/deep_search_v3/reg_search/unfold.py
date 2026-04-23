"""Unfolding logic for regulation search results (reg_search package).

Expands retrieved articles, sections, and regulations into full content
with parent context and references. Called by search_pipeline.py.

Copied from agents/deep_search_v3/executors/regulation_unfold.py.
This is intentionally a copy (not an import) to avoid circular dependencies
and allow independent evolution.
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
MAX_REFERENCES_CONTENT_CHARS = 2_000
MAX_SIBLING_CONTENT_CHARS = 2_000
MAX_SIBLINGS_TOTAL_CHARS = 8_000


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

    # Fetch article_context and references_content (not returned by the RPC)
    article_id = row.get("id")
    if article_id:
        try:
            detail = (
                supabase.table("articles")
                .select("article_context, references_content, section_id, regulation_id")
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
                result["references_content"] = _truncate(
                    detail.data.get("references_content", "") or "",
                    MAX_REFERENCES_CONTENT_CHARS,
                )
        except Exception as e:
            logger.warning("Failed to fetch article detail %s: %s", article_id, e)
            result["article_context"] = ""
            result["references_content"] = ""

    return result


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
                    "article_context, references_content, regulation_id"
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
                        "references_content": _truncate(
                            child.get("references_content", "") or "",
                            MAX_REFERENCES_CONTENT_CHARS,
                        ),
                    }
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

        # Also collect from child articles
        for child in result.get("child_articles", []):
            _collect_regulation_ref(child, seen_regulations, ref_lines)

        # External references from regulations
        for ext in result.get("external_references", []):
            reg_name = ext.get("regulation_name", "")
            if reg_name and reg_name not in seen_regulations:
                seen_regulations.add(reg_name)
                ref_lines.append(f"- {reg_name}")

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
    result_id = result.get("id", "")

    header = f"### [{position}] مادة: {title}"
    if identifier:
        header += f" ({identifier})"
    if result_id:
        header += f" [id:{result_id}]"
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

    # References content (pre-rendered text)
    refs_content = result.get("references_content", "")
    if refs_content:
        lines.append("**إشارات مرجعية:**")
        lines.append(refs_content)
        lines.append("")

    return "\n".join(lines)


def _format_section(result: dict[str, Any], position: int) -> str:
    lines: list[str] = []
    title = result.get("title", "بدون عنوان")
    reg_title = result.get("regulation_title", "")
    result_id = result.get("id", "")

    header = f"### [{position}] باب/فصل: {title}"
    if result_id:
        header += f" [id:{result_id}]"
    lines.append(header)
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

            # Child article references content
            child_refs = child.get("references_content", "")
            if child_refs:
                lines.append(f"**إشارات مرجعية:**")
                lines.append(child_refs)
            lines.append("")

    return "\n".join(lines)


def _format_regulation(result: dict[str, Any], position: int) -> str:
    lines: list[str] = []
    title = result.get("title", "بدون عنوان")
    reg_type = result.get("type", "")
    entity = result.get("entity_name", "")
    result_id = result.get("id", "")

    header = f"### [{position}] نظام: {title}"
    if result_id:
        header += f" [id:{result_id}]"
    lines.append(header)
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


# -- Article with siblings (reranker tool) -------------------------------------


def unfold_article_with_siblings(
    supabase: SupabaseClient, article_id: str
) -> dict[str, Any]:
    """Unfold an article with precise context + all sibling articles in the same section.

    Used by the reranker tool (article_precise mode):
    1. Fetches the target article (content + context + references)
    2. Gets the article's section_id
    3. Fetches the parent section summary
    4. Fetches ALL sibling articles (same section) with content + references

    Returns:
        Dict with keys: target_article, parent_section, sibling_articles, regulation_title
    """
    result: dict[str, Any] = {
        "target_article": None,
        "parent_section": None,
        "sibling_articles": [],
        "regulation_title": "",
    }

    # Step 1: Fetch the target article
    try:
        article_detail = (
            supabase.table("articles")
            .select(
                "id, title, content, article_num, identifier_number, "
                "article_context, references_content, section_id, regulation_id"
            )
            .eq("id", article_id)
            .maybe_single()
            .execute()
        )
        if not article_detail or not article_detail.data:
            return result

        art = article_detail.data
        result["target_article"] = {
            "id": art["id"],
            "title": art.get("title", ""),
            "article_num": art.get("article_num"),
            "identifier_number": art.get("identifier_number", ""),
            "content": _truncate(art.get("content", ""), MAX_CONTENT_CHARS),
            "article_context": _truncate(
                art.get("article_context", "") or "", MAX_CONTEXT_CHARS
            ),
            "references_content": _truncate(
                art.get("references_content", "") or "", MAX_REFERENCES_CONTENT_CHARS
            ),
        }

        section_id = art.get("section_id")
        regulation_id = art.get("regulation_id")

    except Exception as e:
        logger.warning(
            "unfold_article_with_siblings: failed to fetch article %s: %s",
            article_id, e,
        )
        return result

    if not section_id:
        return result

    # Step 2: Fetch parent section summary + context
    try:
        section_detail = (
            supabase.table("sections")
            .select("id, title, section_summary, section_context")
            .eq("id", section_id)
            .maybe_single()
            .execute()
        )
        if section_detail and section_detail.data:
            sec = section_detail.data
            result["parent_section"] = {
                "id": sec["id"],
                "title": sec.get("title", ""),
                "section_summary": _truncate(
                    sec.get("section_summary", "") or "", MAX_CONTEXT_CHARS
                ),
                "section_context": _truncate(
                    sec.get("section_context", "") or "", MAX_CONTEXT_CHARS
                ),
            }
    except Exception as e:
        logger.warning(
            "unfold_article_with_siblings: failed to fetch section %s: %s",
            section_id, e,
        )

    # Step 3: Fetch regulation title
    if regulation_id:
        try:
            reg = (
                supabase.table("regulations")
                .select("title")
                .eq("id", regulation_id)
                .maybe_single()
                .execute()
            )
            if reg and reg.data:
                result["regulation_title"] = reg.data.get("title", "")
        except Exception:
            pass

    # Step 4: Fetch ALL sibling articles in the same section (excluding target)
    try:
        siblings = (
            supabase.table("articles")
            .select(
                "id, title, content, article_num, identifier_number, "
                "references_content"
            )
            .eq("section_id", section_id)
            .neq("id", article_id)
            .order("article_num")
            .execute()
        )
        if siblings and siblings.data:
            total_chars = 0
            for sib in siblings.data:
                sib_content = _truncate(
                    sib.get("content", ""), MAX_SIBLING_CONTENT_CHARS
                )
                total_chars += len(sib_content)
                if total_chars > MAX_SIBLINGS_TOTAL_CHARS:
                    sib_content = _truncate(sib_content, 200)

                result["sibling_articles"].append({
                    "id": sib["id"],
                    "title": sib.get("title", ""),
                    "article_num": sib.get("article_num"),
                    "content": sib_content,
                    "references_content": _truncate(
                        sib.get("references_content", "") or "", 500
                    ),
                })
    except Exception as e:
        logger.warning(
            "unfold_article_with_siblings: failed to fetch siblings for section %s: %s",
            section_id, e,
        )

    return result


# -- Precise unfold: article ---------------------------------------------------


def unfold_article_precise(
    supabase: SupabaseClient, row: dict[str, Any]
) -> dict[str, Any]:
    """Precise unfold for an article — context + content + references."""
    result: dict[str, Any] = {
        "source_type": "article",
        "id": row.get("id"),
        "chunk_ref": row.get("chunk_ref", ""),
        "title": row.get("title", ""),
        "article_num": row.get("article_num"),
        "content": _truncate(row.get("content", ""), MAX_CONTENT_CHARS),
        "regulation_title": row.get("regulation_title", ""),
        "regulation_ref": row.get("regulation_ref", ""),
        "section_title": row.get("section_title", ""),
    }

    article_id = row.get("id")
    if article_id:
        try:
            detail = (
                supabase.table("articles")
                .select("article_context, references_content")
                .eq("id", article_id)
                .maybe_single()
                .execute()
            )
            if detail and detail.data:
                result["article_context"] = _truncate(
                    detail.data.get("article_context", "") or "", MAX_CONTEXT_CHARS
                )
                result["references_content"] = _truncate(
                    detail.data.get("references_content", "") or "",
                    MAX_REFERENCES_CONTENT_CHARS,
                )
        except Exception as e:
            logger.warning("Failed to fetch article detail %s: %s", article_id, e)

    return result


# -- Precise unfold: section ---------------------------------------------------


def unfold_section_precise(
    supabase: SupabaseClient, row: dict[str, Any]
) -> dict[str, Any]:
    """Precise unfold for a section — summary + context + child article titles."""
    section_id = row.get("id")

    result: dict[str, Any] = {
        "source_type": "section",
        "id": section_id,
        "title": row.get("title", ""),
        "section_summary": _truncate(
            row.get("section_summary", "") or "", MAX_CONTEXT_CHARS
        ),
        "regulation_title": row.get("regulation_title", ""),
        "regulation_ref": row.get("regulation_ref", ""),
    }

    if section_id:
        try:
            detail = (
                supabase.table("sections")
                .select("section_context")
                .eq("id", section_id)
                .maybe_single()
                .execute()
            )
            if detail and detail.data:
                result["section_context"] = _truncate(
                    detail.data.get("section_context", "") or "", MAX_CONTEXT_CHARS
                )
        except Exception as e:
            logger.warning("Failed to fetch section detail %s: %s", section_id, e)

        try:
            children = (
                supabase.table("articles")
                .select("title, article_num")
                .eq("section_id", section_id)
                .order("article_num")
                .execute()
            )
            if children and children.data:
                result["child_article_titles"] = [
                    c.get("title", "") for c in children.data
                ]
        except Exception as e:
            logger.warning(
                "Failed to fetch article titles for section %s: %s", section_id, e
            )

    return result


# -- Precise unfold: regulation ------------------------------------------------


def unfold_regulation_precise(
    supabase: SupabaseClient, row: dict[str, Any]
) -> dict[str, Any]:
    """Precise unfold for a regulation — summary + child section titles."""
    regulation_id = row.get("id")

    result: dict[str, Any] = {
        "source_type": "regulation",
        "id": regulation_id,
        "title": row.get("title", ""),
        "regulation_summary": _truncate(
            row.get("regulation_summary", "") or "", MAX_CONTENT_CHARS
        ),
    }

    if regulation_id:
        try:
            sections = (
                supabase.table("sections")
                .select("title, chunk_index")
                .eq("regulation_id", regulation_id)
                .order("chunk_index")
                .execute()
            )
            if sections and sections.data:
                result["child_section_titles"] = [
                    s.get("title", "") for s in sections.data
                ]
        except Exception as e:
            logger.warning(
                "Failed to fetch section titles for regulation %s: %s",
                regulation_id, e,
            )

    return result


# -- Precise formatting --------------------------------------------------------


def format_unfolded_result_precise(result: dict[str, Any], position: int) -> str:
    """Format a single unfolded result in precise (compact) mode."""
    source_type = result.get("source_type", "unknown")

    if source_type == "article":
        return _format_article_precise(result, position)
    elif source_type == "section":
        return _format_section_precise(result, position)
    elif source_type == "regulation":
        return _format_regulation_precise(result, position)
    return f"### [{position}] نتيجة غير معروفة النوع\n"


def _format_article_precise(result: dict[str, Any], position: int) -> str:
    lines: list[str] = []
    title = result.get("title", "بدون عنوان")
    reg_title = result.get("regulation_title", "")
    section_title = result.get("section_title", "")
    result_id = result.get("id", "")

    header = f"### [{position}] مادة: {title}"
    if result_id:
        header += f" [id:{result_id}]"
    lines.append(header)
    if reg_title:
        lines.append(f"**النظام:** {reg_title}")
    if section_title:
        lines.append(f"**الباب/الفصل:** {section_title}")
    score_line = _format_scores(result)
    if score_line:
        lines.append(score_line)
    lines.append("")

    context = result.get("article_context", "")
    if context:
        lines.append(f"**السياق:** {context}")
        lines.append("")

    content = result.get("content", "")
    if content:
        lines.append(f"> {content}")
        lines.append("")

    refs_content = result.get("references_content", "")
    if refs_content:
        lines.append("<references_content>")
        lines.append(refs_content)
        lines.append("</references_content>")
        lines.append("")

    return "\n".join(lines)


def _format_section_precise(result: dict[str, Any], position: int) -> str:
    lines: list[str] = []
    title = result.get("title", "بدون عنوان")
    reg_title = result.get("regulation_title", "")
    result_id = result.get("id", "")

    header = f"### [{position}] باب/فصل: {title}"
    if result_id:
        header += f" [id:{result_id}]"
    lines.append(header)
    if reg_title:
        lines.append(f"**النظام:** {reg_title}")
    score_line = _format_scores(result)
    if score_line:
        lines.append(score_line)
    lines.append("")

    summary = result.get("section_summary", "")
    if summary:
        lines.append(f"**ملخص:** {summary}")

    context = result.get("section_context", "")
    if context:
        lines.append(f"**السياق:** {context}")
    lines.append("")

    child_titles = result.get("child_article_titles", [])
    if child_titles:
        lines.append(f"**المواد:** {' | '.join(child_titles)}")
        lines.append("")

    return "\n".join(lines)


def _format_regulation_precise(result: dict[str, Any], position: int) -> str:
    lines: list[str] = []
    title = result.get("title", "بدون عنوان")
    result_id = result.get("id", "")

    header = f"### [{position}] نظام: {title}"
    if result_id:
        header += f" [id:{result_id}]"
    lines.append(header)
    score_line = _format_scores(result)
    if score_line:
        lines.append(score_line)
    lines.append("")

    summary = result.get("regulation_summary", "")
    if summary:
        lines.append(f"**ملخص:** {summary}")
        lines.append("")

    child_titles = result.get("child_section_titles", [])
    if child_titles:
        lines.append(f"**الأبواب:** {' | '.join(child_titles)}")
        lines.append("")

    return "\n".join(lines)


# -- Utility ------------------------------------------------------------------


def _truncate(text: str, max_chars: int) -> str:
    """Truncate text to max_chars, appending '...' if truncated."""
    if not text or len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."
