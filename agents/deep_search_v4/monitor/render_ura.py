"""Pure rendering helpers: URA + AggregatorOutput -> Markdown.

No I/O, no httpx, no supabase. Walks the Pydantic objects produced by
``run_full_loop`` and emits markdown that mirrors the shape of the
``retrieval_artifacts.ura_json`` / synthesis DB rows so a human can read a
single dump and see exactly what the aggregator saw.
"""
from __future__ import annotations

from typing import Any

from agents.deep_search_v4.aggregator.models import AggregatorOutput
from agents.deep_search_v4.ura.schema import UnifiedRetrievalArtifact


def _md_escape(text: str) -> str:
    """Light escape for table cells (pipes + newlines)."""
    if text is None:
        return ""
    return str(text).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _truncate(text: str, n: int = 220) -> str:
    if not text:
        return ""
    s = str(text)
    return s if len(s) <= n else s[:n] + "..."


# ---------------------------------------------------------------------------
# URA renderer
# ---------------------------------------------------------------------------

def _render_result_block(idx: int, r: Any) -> str:
    domain = getattr(r, "domain", "?")
    source_type = getattr(r, "source_type", "?")
    ref_id = getattr(r, "ref_id", "?")
    title = getattr(r, "title", "")
    relevance = getattr(r, "relevance", "?")
    reasoning = getattr(r, "reasoning", "") or ""
    appears = getattr(r, "appears_in_sub_queries", []) or []
    rrf_max = getattr(r, "rrf_max", 0.0)
    content = getattr(r, "content", "") or ""

    lines: list[str] = []
    lines.append(f"### [{idx}] {ref_id} -- {domain} -- {source_type}")
    lines.append("")
    lines.append(f"- **title**: {title}")
    lines.append(f"- **relevance**: {relevance}")
    lines.append(f"- **rrf_max**: {rrf_max}")
    lines.append(f"- **appears_in_sub_queries**: {appears}")
    if reasoning:
        lines.append(f"- **reasoning**: {reasoning}")

    if domain == "regulations":
        lines.append(f"- **regulation_title**: {getattr(r, 'regulation_title', '')}")
        if getattr(r, "article_num", None):
            lines.append(f"- **article_num**: {r.article_num}")
        if getattr(r, "section_title", None):
            lines.append(f"- **section_title**: {r.section_title}")
        if getattr(r, "article_context", ""):
            lines.append(f"- **article_context**: {r.article_context}")
        if getattr(r, "section_summary", ""):
            lines.append(f"- **section_summary**: {r.section_summary}")
        if getattr(r, "references_content", ""):
            lines.append("")
            lines.append("**references_content**:")
            lines.append("")
            lines.append("```")
            lines.append(r.references_content)
            lines.append("```")
    elif domain == "compliance":
        lines.append(f"- **service_ref**: {getattr(r, 'service_ref', '')}")
        lines.append(f"- **provider_name**: {getattr(r, 'provider_name', '')}")
        lines.append(f"- **platform_name**: {getattr(r, 'platform_name', '')}")
        if getattr(r, "service_url", ""):
            lines.append(f"- **service_url**: {r.service_url}")
        if getattr(r, "target_audience", ""):
            lines.append(f"- **target_audience**: {r.target_audience}")
        if getattr(r, "service_channels", ""):
            lines.append(f"- **service_channels**: {r.service_channels}")
        lines.append(f"- **is_most_used**: {getattr(r, 'is_most_used', False)}")
        if getattr(r, "service_markdown", ""):
            lines.append("")
            lines.append("**service_markdown**:")
            lines.append("")
            lines.append("```")
            lines.append(r.service_markdown)
            lines.append("```")
    elif domain == "cases":
        for fld in (
            "court", "city", "court_level", "case_number", "judgment_number",
            "date_hijri", "appeal_result",
        ):
            val = getattr(r, fld, None)
            if val:
                lines.append(f"- **{fld}**: {val}")
        legal_domains = getattr(r, "legal_domains", []) or []
        if legal_domains:
            lines.append(f"- **legal_domains**: {legal_domains}")
        ref_regs = getattr(r, "referenced_regulations", []) or []
        if ref_regs:
            lines.append(f"- **referenced_regulations**: {ref_regs}")

    lines.append("")
    lines.append("**content**:")
    lines.append("")
    lines.append("```")
    lines.append(content)
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def render_ura_md(ura: UnifiedRetrievalArtifact | None) -> str:
    """Render a URA as a markdown dump similar to retrieval_artifacts.ura_json."""
    if ura is None:
        return "# URA\n\n_No URA produced (pipeline failed before merger)._\n"

    lines: list[str] = []
    lines.append("# UnifiedRetrievalArtifact")
    lines.append("")
    lines.append("## Header")
    lines.append("")
    lines.append(f"- **schema_version**: {ura.schema_version}")
    lines.append(f"- **query_id**: {ura.query_id}")
    lines.append(f"- **log_id**: {ura.log_id}")
    lines.append(f"- **produced_at**: {ura.produced_at}")
    lines.append(f"- **original_query**: {ura.original_query}")
    lines.append(f"- **produced_by**: {ura.produced_by}")
    lines.append(f"- **sector_filter**: {ura.sector_filter}")
    lines.append(f"- **sub_queries** count: {len(ura.sub_queries or [])}")
    lines.append(f"- **high_results** count: {len(ura.high_results or [])}")
    lines.append(f"- **medium_results** count: {len(ura.medium_results or [])}")
    lines.append(f"- **dropped** count: {len(ura.dropped or [])}")
    lines.append("")

    lines.append(f"## High Tier ({len(ura.high_results or [])})")
    lines.append("")
    if ura.high_results:
        for i, r in enumerate(ura.high_results, start=1):
            lines.append(_render_result_block(i, r))
    else:
        lines.append("_(empty)_")
        lines.append("")

    lines.append(f"## Medium Tier ({len(ura.medium_results or [])})")
    lines.append("")
    if ura.medium_results:
        for i, r in enumerate(ura.medium_results, start=1):
            lines.append(_render_result_block(i, r))
    else:
        lines.append("_(empty)_")
        lines.append("")

    lines.append("## Sub-queries")
    lines.append("")
    if ura.sub_queries:
        lines.append("| idx | domain | sufficient | dropped | summary_note | query |")
        lines.append("|-----|--------|------------|---------|--------------|-------|")
        for sq in ura.sub_queries:
            lines.append(
                "| {idx} | {domain} | {suf} | {drop} | {note} | {q} |".format(
                    idx=sq.get("index", "?"),
                    domain=sq.get("domain", "?"),
                    suf=sq.get("sufficient", "?"),
                    drop=sq.get("dropped_count", 0),
                    note=_md_escape(_truncate(sq.get("summary_note", ""), 180)),
                    q=_md_escape(_truncate(sq.get("query", ""), 180)),
                )
            )
    else:
        lines.append("_(empty)_")
    lines.append("")

    lines.append(f"## Dropped ({len(ura.dropped or [])})")
    lines.append("")
    if ura.dropped:
        for j, d in enumerate(ura.dropped, start=1):
            lines.append(f"- [{j}] {d}")
    else:
        lines.append("_(empty)_")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# AggregatorOutput renderer
# ---------------------------------------------------------------------------

def render_aggregator_md(out: AggregatorOutput | None) -> str:
    if out is None:
        return "# AggregatorOutput\n\n_No output produced (aggregator stage failed)._\n"

    lines: list[str] = []
    lines.append("# AggregatorOutput")
    lines.append("")
    lines.append(f"- **prompt_key**: {out.prompt_key}")
    lines.append(f"- **model_used**: {out.model_used}")
    lines.append(f"- **confidence**: {out.confidence}")
    lines.append("")

    lines.append("## synthesis_md")
    lines.append("")
    lines.append(out.synthesis_md or "_(empty)_")
    lines.append("")

    lines.append(f"## References ({len(out.references)})")
    lines.append("")
    if out.references:
        lines.append("| n | source_type | domain | regulation_title | title | snippet | ref_id | relevance |")
        lines.append("|---|-------------|--------|------------------|-------|---------|--------|-----------|")
        for ref in out.references:
            lines.append(
                "| {n} | {st} | {dom} | {regt} | {title} | {snip} | {ref} | {rel} |".format(
                    n=ref.n,
                    st=ref.source_type,
                    dom=ref.domain,
                    regt=_md_escape(_truncate(ref.regulation_title, 120)),
                    title=_md_escape(_truncate(ref.title, 120)),
                    snip=_md_escape(_truncate(ref.snippet, 200)),
                    ref=_md_escape(ref.ref_id),
                    rel=ref.relevance,
                )
            )
    else:
        lines.append("_(no references)_")
    lines.append("")

    lines.append("## Gaps")
    lines.append("")
    if out.gaps:
        for g in out.gaps:
            lines.append(f"- {g}")
    else:
        lines.append("_(none)_")
    lines.append("")

    if out.disclaimer_ar:
        lines.append("## Disclaimer")
        lines.append("")
        lines.append(out.disclaimer_ar)
        lines.append("")

    return "\n".join(lines)


__all__ = ["render_ura_md", "render_aggregator_md"]
