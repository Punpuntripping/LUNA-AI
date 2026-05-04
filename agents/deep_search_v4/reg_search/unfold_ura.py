"""Aggregator-side (URA) unfolder for reg_search.

Stage-2 layer for the regulation pipeline. Produces richer content than the
reranker formatters in `unfold_reranker.py` — the URA / aggregator can absorb
full article text, extended context, and deeper section/regulation structure
that would bloat the reranker prompt.

The key invariant:
    URA caps >> reranker caps

Public surface:
    build_ura_content(supabase, row, *, mode="precise") -> str
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from supabase import Client as SupabaseClient

logger = logging.getLogger(__name__)


# -- URA-level truncation limits (wider than reranker) --------------------------
#
#   Reranker caps (for reference):
#       MAX_CONTENT_CHARS          = 3_000
#       MAX_CONTEXT_CHARS          = 500
#       MAX_REFERENCES_CONTENT_CHARS = 2_000
#
#   URA caps (this file):

MAX_CONTENT_CHARS_URA = 8_000
MAX_CONTEXT_CHARS_URA = 2_000
MAX_REFERENCES_CHARS_URA = 4_000
MAX_CHILD_ARTICLE_CHARS_URA = 5_000   # per child article content
MAX_SECTION_SUMMARY_CHARS_URA = 1_500


# -- Private utility ------------------------------------------------------------


def _truncate(text: str, max_chars: int) -> str:
    """Truncate *text* to *max_chars*, appending '...' if truncated."""
    if not text or len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


# -- Article URA formatter ------------------------------------------------------


def _build_full_article_ura(
    supabase: "SupabaseClient",
    row: dict[str, Any],
    *,
    fetch_context: bool = True,
) -> str:
    """Return a richer markdown block for an article result (URA caps).

    Fetches article_context and references_content from DB using URA-level
    char caps. Both precise and full modes call this; precise skips nothing
    at the article level — the wider caps already give adequate richness.

    Args:
        supabase: Supabase client.
        row: Article row from the reg_search RPC.
        fetch_context: If True (default), fetch article_context and
            references_content from the DB.  Set to False when the caller
            has already pre-fetched those fields into *row*.

    Returns:
        Markdown string.
    """
    lines: list[str] = []

    title = row.get("title", "بدون عنوان")
    identifier = row.get("identifier_number", "")
    reg_title = row.get("regulation_title", "")
    section_title = row.get("section_title", "")
    result_id = row.get("id", "")
    article_num = row.get("article_num", "")

    # Header
    header = f"### مادة: {title}"
    if identifier:
        header += f" ({identifier})"
    elif article_num:
        header += f" (م. {article_num})"
    if result_id:
        header += f" [id:{result_id}]"
    lines.append(header)

    if reg_title:
        lines.append(f"**النظام:** {reg_title}")
    if section_title:
        lines.append(f"**الباب/الفصل:** {section_title}")
    lines.append("")

    # Content (main text)
    content = _truncate(row.get("content", ""), MAX_CONTENT_CHARS_URA)
    if content:
        lines.append(f"> {content}")
        lines.append("")

    # Context + references — fetch from DB if needed
    article_context = row.get("article_context", "")
    references_content = row.get("references_content", "")

    if fetch_context and result_id:
        try:
            detail = (
                supabase.table("articles")
                .select("article_context, references_content")
                .eq("id", result_id)
                .maybe_single()
                .execute()
            )
            if detail and detail.data:
                article_context = detail.data.get("article_context", "") or ""
                references_content = detail.data.get("references_content", "") or ""
        except Exception as exc:
            logger.warning(
                "_build_full_article_ura: failed to fetch detail for %s: %s",
                result_id, exc,
            )

    if article_context:
        lines.append(f"**السياق:** {_truncate(article_context, MAX_CONTEXT_CHARS_URA)}")
        lines.append("")

    if references_content:
        lines.append("**إشارات مرجعية:**")
        lines.append(_truncate(references_content, MAX_REFERENCES_CHARS_URA))
        lines.append("")

    return "\n".join(lines)


# -- Section URA formatter ------------------------------------------------------


def _build_full_section_ura(
    supabase: "SupabaseClient",
    row: dict[str, Any],
    *,
    include_child_articles: bool = True,
) -> str:
    """Return a richer markdown block for a section result (URA caps).

    Fetches section_context from DB.  When *include_child_articles* is True
    (full mode), also fetches ALL child articles with full content.

    Args:
        supabase: Supabase client.
        row: Section row from the reg_search RPC.
        include_child_articles: If True, fetch and include all child articles
            with their content.  False gives a compact section header only
            (precise mode).

    Returns:
        Markdown string.
    """
    lines: list[str] = []

    section_id = row.get("id")
    title = row.get("title", "بدون عنوان")
    reg_title = row.get("regulation_title", "")
    result_id = section_id or ""

    # Header
    header = f"### باب/فصل: {title}"
    if result_id:
        header += f" [id:{result_id}]"
    lines.append(header)
    if reg_title:
        lines.append(f"**النظام:** {reg_title}")
    lines.append("")

    # Section summary from RPC row
    section_summary = _truncate(
        row.get("section_summary", "") or "", MAX_SECTION_SUMMARY_CHARS_URA
    )
    if section_summary:
        lines.append(f"**ملخص:** {section_summary}")
        lines.append("")

    # Fetch section_context from DB
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
                ctx = detail.data.get("section_context", "") or ""
                if ctx:
                    lines.append(
                        f"**السياق:** {_truncate(ctx, MAX_CONTEXT_CHARS_URA)}"
                    )
                    lines.append("")
        except Exception as exc:
            logger.warning(
                "_build_full_section_ura: failed to fetch context for section %s: %s",
                section_id, exc,
            )

    # Child articles (full mode only)
    if include_child_articles and section_id:
        try:
            children = (
                supabase.table("articles")
                .select(
                    "id, title, content, article_num, identifier_number, "
                    "article_context, references_content"
                )
                .eq("section_id", section_id)
                .order("article_num")
                .execute()
            )
            if children and children.data:
                lines.append(f"**المواد ({len(children.data)}):**")
                lines.append("")
                for child in children.data:
                    child_num = child.get("article_num", "")
                    child_title = child.get("title", "")
                    child_id = child.get("id", "")
                    child_identifier = child.get("identifier_number", "")

                    art_header = f"#### المادة {child_num}: {child_title}"
                    if child_identifier:
                        art_header += f" ({child_identifier})"
                    if child_id:
                        art_header += f" [id:{child_id}]"
                    lines.append(art_header)

                    child_content = _truncate(
                        child.get("content", ""), MAX_CHILD_ARTICLE_CHARS_URA
                    )
                    if child_content:
                        lines.append(f"> {child_content}")

                    child_ctx = child.get("article_context", "") or ""
                    if child_ctx:
                        lines.append(
                            f"**السياق:** {_truncate(child_ctx, MAX_CONTEXT_CHARS_URA)}"
                        )

                    child_refs = child.get("references_content", "") or ""
                    if child_refs:
                        lines.append("**إشارات مرجعية:**")
                        lines.append(
                            _truncate(child_refs, MAX_REFERENCES_CHARS_URA)
                        )

                    lines.append("")

        except Exception as exc:
            logger.warning(
                "_build_full_section_ura: failed to fetch child articles for section %s: %s",
                section_id, exc,
            )
    elif not include_child_articles and section_id:
        # Precise mode: just list child article titles
        try:
            children = (
                supabase.table("articles")
                .select("title, article_num")
                .eq("section_id", section_id)
                .order("article_num")
                .execute()
            )
            if children and children.data:
                titles = [
                    f"م.{c.get('article_num', '')}: {c.get('title', '')}"
                    for c in children.data
                    if c.get("title")
                ]
                if titles:
                    lines.append(f"**المواد:** {' | '.join(titles)}")
                    lines.append("")
        except Exception as exc:
            logger.warning(
                "_build_full_section_ura: failed to fetch article titles for section %s: %s",
                section_id, exc,
            )

    return "\n".join(lines)


# -- Regulation URA formatter ---------------------------------------------------


def _build_full_regulation_ura(
    supabase: "SupabaseClient",
    row: dict[str, Any],
    *,
    include_section_summaries: bool = True,
) -> str:
    """Return a richer markdown block for a regulation result (URA caps).

    Fetches entity_name, external_references, and source_url from DB.
    Fetches child sections with their summaries and article counts.

    Args:
        supabase: Supabase client.
        row: Regulation row from the reg_search RPC.
        include_section_summaries: If True (both modes), include section
            summaries and article counts alongside section titles.

    Returns:
        Markdown string.
    """
    lines: list[str] = []

    regulation_id = row.get("id")
    title = row.get("title", "بدون عنوان")
    reg_type = row.get("type", "")
    main_cat = row.get("main_category", "")
    sub_cat = row.get("sub_category", "")
    authority_level = row.get("authority_level", "")

    # Header
    header = f"### نظام: {title}"
    if regulation_id:
        header += f" [id:{regulation_id}]"
    lines.append(header)
    if reg_type:
        lines.append(f"**النوع:** {reg_type}")
    if main_cat:
        lines.append(f"**التصنيف:** {main_cat}" + (f" > {sub_cat}" if sub_cat else ""))
    if authority_level:
        lines.append(f"**المستوى:** {authority_level}")
    lines.append("")

    # Regulation summary
    reg_summary = _truncate(
        row.get("regulation_summary", "") or "", MAX_CONTENT_CHARS_URA
    )
    if reg_summary:
        lines.append(f"**ملخص:** {reg_summary}")
        lines.append("")

    # Fetch entity name, source_url, external_references from DB
    entity_name = ""
    source_url = ""
    pdf_link = None
    external_references: list[dict[str, Any]] = []

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
                source_url = reg_detail.data.get("source_url", "") or ""
                pdf_link = reg_detail.data.get("pdf_link")
                ext_refs_raw = reg_detail.data.get("external_references") or []
                external_references = _resolve_external_references(ext_refs_raw)

                entity_id = reg_detail.data.get("entity_id")
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
                            entity_name = entity.data.get("entity_name", "") or ""
                    except Exception as exc:
                        logger.warning(
                            "_build_full_regulation_ura: failed to fetch entity for %s: %s",
                            regulation_id, exc,
                        )
        except Exception as exc:
            logger.warning(
                "_build_full_regulation_ura: failed to fetch detail for regulation %s: %s",
                regulation_id, exc,
            )

    if entity_name:
        lines.append(f"**الجهة:** {entity_name}")
    if source_url:
        lines.append(f"**المصدر:** {source_url}")
    if pdf_link:
        pdf_url = (
            pdf_link.get("url", "") if isinstance(pdf_link, dict) else str(pdf_link)
        )
        if pdf_url:
            lines.append(f"**PDF:** {pdf_url}")
    if entity_name or source_url or pdf_link:
        lines.append("")

    # External references
    if external_references:
        lines.append("**أنظمة ذات صلة:**")
        for ext in external_references:
            lines.append(
                f"  - {ext.get('regulation_name', '')} ({ext.get('relation', '')})"
            )
        lines.append("")

    # Child sections with summaries and article counts
    if regulation_id:
        try:
            # Single query: sections ordered by chunk_index
            sections = (
                supabase.table("sections")
                .select("id, title, section_summary, chunk_index")
                .eq("regulation_id", regulation_id)
                .order("chunk_index")
                .execute()
            )
            if sections and sections.data:
                # Fetch article counts per section in one query
                section_ids = [s["id"] for s in sections.data if s.get("id")]
                article_counts: dict[str, int] = {}
                if section_ids:
                    try:
                        # Supabase doesn't support GROUP BY via PostgREST directly;
                        # fetch all article section_ids and count client-side.
                        counts_resp = (
                            supabase.table("articles")
                            .select("section_id")
                            .in_("section_id", section_ids)
                            .execute()
                        )
                        if counts_resp and counts_resp.data:
                            for art in counts_resp.data:
                                sid = art.get("section_id")
                                if sid:
                                    article_counts[sid] = (
                                        article_counts.get(sid, 0) + 1
                                    )
                    except Exception as exc:
                        logger.warning(
                            "_build_full_regulation_ura: failed to count articles for regulation %s: %s",
                            regulation_id, exc,
                        )

                lines.append(f"**الأبواب والفصول ({len(sections.data)}):**")
                for sec in sections.data:
                    sec_id = sec.get("id", "")
                    sec_title = sec.get("title", "")
                    sec_summary = _truncate(
                        sec.get("section_summary", "") or "",
                        MAX_SECTION_SUMMARY_CHARS_URA,
                    )
                    count = article_counts.get(sec_id, 0)

                    sec_line = f"  - **{sec_title}**"
                    if count:
                        sec_line += f" ({count} مادة)"
                    lines.append(sec_line)
                    if include_section_summaries and sec_summary:
                        lines.append(f"    {sec_summary}")

                lines.append("")

        except Exception as exc:
            logger.warning(
                "_build_full_regulation_ura: failed to fetch sections for regulation %s: %s",
                regulation_id, exc,
            )

    return "\n".join(lines)


# -- Helper: resolve external references (no DB lookup needed) ------------------


def _resolve_external_references(
    ext_refs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Pass-through resolver for external_references JSONB.

    Shape of each entry: {relation, regulation_id, regulation_name}
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


# -- Public entry point ---------------------------------------------------------


def build_ura_content(
    supabase: "SupabaseClient",
    row: dict[str, Any],
    *,
    mode: str = "precise",
) -> str:
    """Return the URA-side content string for one reg result.

    Uses URA-level char caps (wider than the reranker) so the aggregator
    receives richer payloads than the reranker LLM saw during scoring.

    Args:
        supabase: Supabase client for DB lookups.
        row: Result row tagged with ``source_type`` ("article" | "section" |
            "regulation") plus the joined fields the reg_search RPC returned.
        mode: ``"precise"`` — richer article text + section context, but skips
            fetching full child-article bodies for section results (uses title
            list only). ``"full"`` — fetches all child article bodies for
            section results in addition to the wider caps.

    Returns:
        Markdown string for the aggregator.  Empty string if
        ``source_type`` is unrecognised.
    """
    source_type = row.get("source_type", "")
    full_mode = mode == "full"

    if source_type == "article":
        return _build_full_article_ura(supabase, row)

    elif source_type == "section":
        return _build_full_section_ura(
            supabase, row, include_child_articles=full_mode
        )

    elif source_type == "regulation":
        return _build_full_regulation_ura(
            supabase, row, include_section_summaries=True
        )

    else:
        logger.warning(
            "build_ura_content: unknown source_type %r — returning empty content",
            source_type,
        )
        return ""
