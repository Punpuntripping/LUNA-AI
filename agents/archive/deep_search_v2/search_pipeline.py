"""Search pipeline wrapper for deep_search_v2.

Three real search pipelines using hybrid search (BM25 + semantic via RRF):
- Regulations: embed → 3 parallel hybrid RPCs → optional Jina rerank → unfold
- Cases: embed → hybrid_search_cases RPC → optional Jina rerank → format
- Services (compliance): embed → hybrid_search_services RPC → optional Jina rerank → format

All pipelines share the same hybrid RPC + optional Jina rerank infrastructure.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import DeepSearchDeps, SearchResult

logger = logging.getLogger(__name__)

# Reuse Jina reranking from regulation executor (no duplication)
JINA_RERANK_URL = "https://api.jina.ai/v1/rerank"
JINA_MODEL = "jina-reranker-v3"

# Default result counts
CASES_TOP_N = 10
SERVICES_TOP_N = 3
MATCH_COUNT = 30

# Content truncation for case formatting
MAX_CONTENT_CHARS = 5_000


# ── Main dispatcher ──────────────────────────────────────────────────────────


async def run_search_pipeline(
    query: str,
    tool: str,
    deps: DeepSearchDeps,
    sse_events: list[dict] | None = None,
) -> SearchResult:
    """Execute a single search query against the specified tool.

    Args:
        query: Arabic search query to execute.
        tool: "regulations", "cases", or "compliance".
        deps: DeepSearchDeps with supabase, embedding_fn, etc.
        sse_events: Optional list to append status events to.

    Returns:
        SearchResult with raw markdown output from the tool.
    """
    from .models import SearchResult as SR

    events = sse_events if sse_events is not None else []

    # Check for injected mock results first (for testing)
    if deps.mock_results and tool in deps.mock_results:
        mock_md = deps.mock_results[tool]
        if isinstance(mock_md, str):
            return SR(
                tool=tool,
                query=query,
                raw_markdown=mock_md,
                result_count=2,
                is_mock=True,
            )

    if tool == "regulations":
        return await _search_regulations(query, deps, events)
    elif tool == "cases":
        return await _search_cases(query, deps, events)
    elif tool == "compliance":
        return await _search_compliance(query, deps, events)
    else:
        logger.warning("Unknown search tool: %s", tool)
        return SR(
            tool=tool,
            query=query,
            raw_markdown=f"أداة بحث غير معروفة: {tool}",
            result_count=0,
            is_mock=False,
        )


# ── Regulations pipeline (self-contained) ────────────────────────────────────

# Distribution of match_count across the three regulation RPCs
_REG_ARTICLE_RATIO = 0.50   # 50% → articles
_REG_SECTION_RATIO = 0.33   # 33% → sections
_REG_BATCH_1_SIZE = 5
_REG_BATCH_2_SIZE = 5


async def _search_regulations(
    query: str,
    deps: DeepSearchDeps,
    events: list[dict],
) -> SearchResult:
    """Search regulations via embed → 3 parallel RPCs → Jina rerank → unfold → format.

    Self-contained: no imports from agents/regulation_executor/.
    Uses the same _hybrid_rpc_search + optional _rerank helpers as cases/services.
    """
    from .models import SearchResult as SR
    from .regulation_unfold import (
        collect_references,
        format_unfolded_result,
        unfold_article,
        unfold_regulation,
        unfold_section,
    )

    try:
        events.append({
            "type": "status",
            "text": f"جاري البحث في الأنظمة واللوائح: {query[:80]}...",
        })

        # Step 1: Embed query
        embedding = await deps.embedding_fn(query)

        # Step 2: Parallel search across 3 regulation RPCs
        events.append({"type": "status", "text": "جاري البحث في قاعدة بيانات الأنظمة..."})

        article_count = max(1, int(MATCH_COUNT * _REG_ARTICLE_RATIO))
        section_count = max(1, int(MATCH_COUNT * _REG_SECTION_RATIO))
        regulation_count = max(1, MATCH_COUNT - article_count - section_count)

        articles, sections, regulations = await asyncio.gather(
            _hybrid_rpc_search(deps.supabase, "articles", query, embedding, article_count),
            _hybrid_rpc_search(deps.supabase, "sections", query, embedding, section_count),
            _hybrid_rpc_search(deps.supabase, "regulations", query, embedding, regulation_count),
        )

        # Step 3: Merge and tag with source_type + _text for reranker
        candidates: list[dict[str, Any]] = []
        for row in articles:
            row["source_type"] = "article"
            row["_text"] = row.get("content", "")
            candidates.append(row)
        for row in sections:
            row["source_type"] = "section"
            row["_text"] = row.get("section_summary") or row.get("content", "")
            candidates.append(row)
        for row in regulations:
            row["source_type"] = "regulation"
            row["_text"] = row.get("regulation_summary", "")
            candidates.append(row)

        if not candidates:
            events.append({"type": "status", "text": "لم يتم العثور على أنظمة مطابقة."})
            return SR(
                tool="regulations", query=query,
                raw_markdown="لم يتم العثور على نتائج. لا توجد أنظمة أو مواد مطابقة للاستعلام.",
                result_count=0, is_mock=False,
            )

        logger.info(
            "Regulation candidates: %d articles, %d sections, %d regulations",
            len(articles), len(sections), len(regulations),
        )

        # Step 4: Rerank (Jina if --rerank, otherwise top by hybrid score)
        if deps.use_reranker:
            events.append({"type": "status", "text": f"جاري إعادة ترتيب {len(candidates)} نتيجة عبر Jina..."})
            top_candidates = await _rerank(
                deps.http_client, deps.jina_api_key, query, candidates, 10,
            )
        else:
            events.append({"type": "status", "text": f"جاري اختيار أفضل {min(10, len(candidates))} نتيجة..."})
            top_candidates = _score_fallback(candidates, 10)

        # Step 5: Unfold top results
        events.append({"type": "status", "text": f"جاري استخراج التفاصيل لأفضل {len(top_candidates)} نتيجة..."})
        unfolded: list[dict[str, Any]] = []
        for candidate in top_candidates:
            try:
                st = candidate.get("source_type", "")
                if st == "article":
                    unfolded.append(unfold_article(deps.supabase, candidate))
                elif st == "section":
                    unfolded.append(unfold_section(deps.supabase, candidate))
                elif st == "regulation":
                    unfolded.append(unfold_regulation(deps.supabase, candidate))
            except Exception as e:
                logger.warning("Unfold failed for %s %s: %s", candidate.get("source_type"), candidate.get("id"), e)

        if not unfolded:
            return SR(
                tool="regulations", query=query,
                raw_markdown="لم يتم العثور على نتائج كافية بعد التوسع.",
                result_count=0, is_mock=False,
            )

        # Step 6: Format into markdown
        batch_1 = unfolded[:_REG_BATCH_1_SIZE]
        batch_2 = unfolded[_REG_BATCH_1_SIZE:_REG_BATCH_1_SIZE + _REG_BATCH_2_SIZE]

        output_lines: list[str] = [f"## نتائج البحث — {len(unfolded)} نتيجة\n"]
        output_lines.append("---")
        output_lines.append(f"## الدفعة الأولى (الأعلى صلة) — {len(batch_1)} نتائج\n")
        for i, result in enumerate(batch_1, start=1):
            output_lines.append(format_unfolded_result(result, i))

        if batch_2:
            output_lines.append("---")
            output_lines.append(f"## الدفعة الثانية — {len(batch_2)} نتائج\n")
            for i, result in enumerate(batch_2, start=_REG_BATCH_1_SIZE + 1):
                output_lines.append(format_unfolded_result(result, i))

        refs_block = collect_references(unfolded)
        if refs_block:
            output_lines.append("\n---")
            output_lines.append(refs_block)

        result_md = "\n".join(output_lines)
        result_count = len(unfolded)

        events.append({
            "type": "status",
            "text": f"تم استرجاع {result_count} نتيجة من الأنظمة واللوائح.",
        })

        return SR(
            tool="regulations", query=query,
            raw_markdown=result_md,
            result_count=result_count, is_mock=False,
        )

    except Exception as e:
        logger.error("Regulation search failed for '%s': %s", query[:80], e, exc_info=True)
        events.append({"type": "status", "text": "حدث خطأ أثناء البحث في الأنظمة."})
        return SR(
            tool="regulations", query=query,
            raw_markdown=f"خطأ أثناء البحث في الأنظمة واللوائح: {e}\n\nلم يتم العثور على نتائج بسبب خطأ تقني.",
            result_count=0, is_mock=False,
        )


# ── Cases pipeline (real) ────────────────────────────────────────────────────


async def _search_cases(
    query: str,
    deps: DeepSearchDeps,
    events: list[dict],
) -> SearchResult:
    """Search court cases via embed → search_cases RPC → Jina rerank → format."""
    from .models import SearchResult as SR

    try:
        events.append({
            "type": "status",
            "text": f"جاري البحث في السوابق القضائية: {query[:80]}...",
        })

        # Step 1: Embed query
        embedding = await deps.embedding_fn(query)

        # Step 2: Hybrid search via RPC
        events.append({"type": "status", "text": "جاري البحث في قاعدة بيانات الأحكام القضائية..."})
        candidates = await _hybrid_rpc_search(deps.supabase, "cases", query, embedding, MATCH_COUNT)

        if not candidates:
            events.append({"type": "status", "text": "لم يتم العثور على سوابق قضائية مطابقة."})
            return SR(
                tool="cases",
                query=query,
                raw_markdown="لم يتم العثور على سوابق قضائية مطابقة للاستعلام.",
                result_count=0,
                is_mock=False,
            )

        logger.info("Cases search: %d candidates for '%s'", len(candidates), query[:80])

        # Step 3: Rerank via Jina on content field
        for c in candidates:
            c["_text"] = c.get("content", "")

        if deps.use_reranker:
            events.append({"type": "status", "text": f"جاري إعادة ترتيب {len(candidates)} نتيجة قضائية عبر Jina..."})
            top_candidates = await _rerank(
                deps.http_client, deps.jina_api_key, query, candidates, CASES_TOP_N,
            )
        else:
            events.append({"type": "status", "text": f"جاري اختيار أفضل {min(CASES_TOP_N, len(candidates))} نتيجة قضائية..."})
            top_candidates = _score_fallback(candidates, CASES_TOP_N)

        # Step 4: Format results
        output_lines: list[str] = []
        output_lines.append(f"## نتائج البحث في السوابق القضائية — {len(top_candidates)} نتيجة\n")

        for i, row in enumerate(top_candidates, start=1):
            output_lines.append(_format_case_result(row, i))

        # References block
        refs = _collect_case_references(top_candidates)
        if refs:
            output_lines.append("\n---")
            output_lines.append(refs)

        result_md = "\n".join(output_lines)

        events.append({
            "type": "status",
            "text": f"تم استرجاع {len(top_candidates)} حكم قضائي.",
        })

        return SR(
            tool="cases",
            query=query,
            raw_markdown=result_md,
            result_count=len(top_candidates),
            is_mock=False,
        )

    except Exception as e:
        logger.error("Cases search failed for '%s': %s", query[:80], e, exc_info=True)
        events.append({"type": "status", "text": "حدث خطأ أثناء البحث في السوابق القضائية."})
        return SR(
            tool="cases",
            query=query,
            raw_markdown=f"خطأ أثناء البحث في السوابق القضائية: {e}",
            result_count=0,
            is_mock=False,
        )


# ── Services/compliance pipeline (real) ──────────────────────────────────────


async def _search_compliance(
    query: str,
    deps: DeepSearchDeps,
    events: list[dict],
) -> SearchResult:
    """Search government services via embed → search_services RPC → Jina rerank → format."""
    from .models import SearchResult as SR

    try:
        events.append({
            "type": "status",
            "text": f"جاري البحث في الخدمات الحكومية: {query[:80]}...",
        })

        # Step 1: Embed query
        embedding = await deps.embedding_fn(query)

        # Step 2: Hybrid search via RPC
        events.append({"type": "status", "text": "جاري البحث في قاعدة بيانات الخدمات الحكومية..."})
        candidates = await _hybrid_rpc_search(deps.supabase, "services", query, embedding, MATCH_COUNT)

        if not candidates:
            events.append({"type": "status", "text": "لم يتم العثور على خدمات حكومية مطابقة."})
            return SR(
                tool="compliance",
                query=query,
                raw_markdown="لم يتم العثور على خدمات حكومية مطابقة للاستعلام.",
                result_count=0,
                is_mock=False,
            )

        logger.info("Services search: %d candidates for '%s'", len(candidates), query[:80])

        # Step 3: Rerank via Jina on service_markdown field
        for c in candidates:
            c["_text"] = c.get("service_markdown") or c.get("service_context") or c.get("service_name_ar", "")

        if deps.use_reranker:
            events.append({"type": "status", "text": f"جاري إعادة ترتيب {len(candidates)} خدمة حكومية عبر Jina..."})
            top_candidates = await _rerank(
                deps.http_client, deps.jina_api_key, query, candidates, SERVICES_TOP_N,
            )
        else:
            events.append({"type": "status", "text": f"جاري اختيار أفضل {min(SERVICES_TOP_N, len(candidates))} خدمة حكومية..."})
            top_candidates = _score_fallback(candidates, SERVICES_TOP_N)

        # Step 4: Format results
        output_lines: list[str] = []
        output_lines.append(f"## نتائج البحث في الخدمات الحكومية — {len(top_candidates)} نتيجة\n")

        for i, row in enumerate(top_candidates, start=1):
            output_lines.append(_format_service_result(row, i))

        # References block
        refs = _collect_service_references(top_candidates)
        if refs:
            output_lines.append("\n---")
            output_lines.append(refs)

        result_md = "\n".join(output_lines)

        events.append({
            "type": "status",
            "text": f"تم استرجاع {len(top_candidates)} خدمة حكومية.",
        })

        return SR(
            tool="compliance",
            query=query,
            raw_markdown=result_md,
            result_count=len(top_candidates),
            is_mock=False,
        )

    except Exception as e:
        logger.error("Services search failed for '%s': %s", query[:80], e, exc_info=True)
        events.append({"type": "status", "text": "حدث خطأ أثناء البحث في الخدمات الحكومية."})
        return SR(
            tool="compliance",
            query=query,
            raw_markdown=f"خطأ أثناء البحث في الخدمات الحكومية: {e}",
            result_count=0,
            is_mock=False,
        )


# ── Shared helpers ───────────────────────────────────────────────────────────


async def _hybrid_rpc_search(
    supabase: Any,
    domain: str,
    query_text: str,
    embedding: list[float],
    match_count: int,
    full_text_weight: float = 0.25,
    semantic_weight: float = 0.75,
) -> list[dict]:
    """Call a Supabase hybrid search RPC (BM25 + semantic via RRF)."""
    rpc_name = f"hybrid_search_{domain}"

    def _call() -> list[dict]:
        try:
            result = supabase.rpc(
                rpc_name,
                {
                    "query_text": query_text,
                    "query_embedding": embedding,
                    "match_count": match_count,
                    "full_text_weight": full_text_weight,
                    "semantic_weight": semantic_weight,
                },
            ).execute()
            return result.data or []
        except Exception as e:
            logger.warning("%s RPC failed: %s", rpc_name, e)
            return []

    return await asyncio.to_thread(_call)


async def _rerank(
    http_client: Any,
    jina_api_key: str,
    query: str,
    candidates: list[dict[str, Any]],
    top_n: int,
) -> list[dict[str, Any]]:
    """Rerank candidates using Jina Reranker v3. Falls back to cosine distance."""
    if not jina_api_key:
        logger.info("No Jina API key — falling back to hybrid score sort")
        return _score_fallback(candidates, top_n)

    documents: list[str] = []
    for c in candidates:
        text = c.get("_text", "")
        if not text:
            text = c.get("content", "") or c.get("service_markdown", "") or c.get("title", "")
        documents.append(text[:2000] if text else "(empty)")

    try:
        response = await http_client.post(
            JINA_RERANK_URL,
            headers={
                "Authorization": f"Bearer {jina_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": JINA_MODEL,
                "query": query,
                "documents": documents,
                "top_n": top_n,
            },
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()

        reranked = data.get("results", [])
        if not reranked:
            logger.warning("Jina returned empty — falling back to hybrid score")
            return _score_fallback(candidates, top_n)

        top: list[dict[str, Any]] = []
        for item in reranked:
            idx = item.get("index", 0)
            if 0 <= idx < len(candidates):
                candidate = candidates[idx]
                candidate["reranker_score"] = item.get("relevance_score", 0.0)
                top.append(candidate)

        logger.info("Jina reranking: %d → %d results", len(candidates), len(top))
        return top

    except Exception as e:
        logger.warning("Jina reranking failed: %s — falling back to hybrid score", e)
        return _score_fallback(candidates, top_n)


def _score_fallback(candidates: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    """Sort by hybrid RRF score (descending, higher=better) and take top N."""
    return sorted(candidates, key=lambda c: c.get("score", 0.0), reverse=True)[:top_n]


def _truncate(text: str, max_chars: int) -> str:
    """Truncate text, appending '...' if truncated."""
    if not text or len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


# ── Case formatting ──────────────────────────────────────────────────────────


def _format_case_result(row: dict[str, Any], position: int) -> str:
    """Format a single case result into a readable markdown block."""
    lines: list[str] = []

    court = row.get("court", "")
    city = row.get("city", "")
    court_level = row.get("court_level", "")

    # Header
    level_label = "استئناف" if court_level == "appeal" else "ابتدائي"
    header = f"### [{position}] حكم: {court}"
    if city:
        header += f" — {city}"
    header += f" ({level_label})"
    lines.append(header)

    # Metadata
    case_number = row.get("case_number", "")
    judgment_number = row.get("judgment_number", "")
    date_hijri = row.get("date_hijri", "")

    meta_parts: list[str] = []
    if case_number:
        meta_parts.append(f"**رقم القضية:** {case_number}")
    if judgment_number:
        meta_parts.append(f"**رقم الحكم:** {judgment_number}")
    if meta_parts:
        lines.append(" | ".join(meta_parts))

    if date_hijri:
        lines.append(f"**التاريخ:** {date_hijri}")

    details_url = row.get("details_url", "")
    if details_url:
        lines.append(f"**رابط التفاصيل:** {details_url}")

    lines.append("")

    # Content (the ruling text — primary payload)
    content = row.get("content", "")
    if content:
        lines.append(_truncate(content, MAX_CONTENT_CHARS))
        lines.append("")

    # Legal domains
    legal_domains = row.get("legal_domains") or []
    if legal_domains and isinstance(legal_domains, list):
        lines.append(f"**المجالات القانونية:** {' · '.join(str(d) for d in legal_domains)}")
        lines.append("")

    # Referenced regulations
    refs = row.get("referenced_regulations") or []
    if refs and isinstance(refs, list):
        lines.append("**أنظمة مُشار إليها:**")
        for ref in refs[:8]:  # Limit to 8 references
            if isinstance(ref, dict):
                reg_name = ref.get("النظام", ref.get("regulation_name", ""))
                art_num = ref.get("الرقم", ref.get("article_number", ""))
                if reg_name:
                    line = f"  - {reg_name}"
                    if art_num:
                        line += f" (المادة {art_num})"
                    lines.append(line)
        lines.append("")

    # Appeal info (if present)
    appeal_result = row.get("appeal_result")
    if appeal_result:
        appeal_court = row.get("appeal_court", "")
        appeal_date = row.get("appeal_date_hijri", "")
        appeal_parts = [f"**الاستئناف:** {appeal_result}"]
        if appeal_court:
            appeal_parts.append(appeal_court)
        if appeal_date:
            appeal_parts.append(appeal_date)
        lines.append(" | ".join(appeal_parts))
        lines.append("")

    return "\n".join(lines)


def _collect_case_references(results: list[dict[str, Any]]) -> str:
    """Collect deduplicated case references block."""
    seen: set[str] = set()
    ref_lines: list[str] = []

    for row in results:
        case_ref = row.get("case_ref", "")
        court = row.get("court", "")
        case_number = row.get("case_number", "")
        city = row.get("city", "")

        key = case_ref or case_number
        if key and key not in seen:
            seen.add(key)
            parts = [case_ref, court]
            if city:
                parts.append(city)
            ref_lines.append(f"- {' | '.join(p for p in parts if p)}")

    if not ref_lines:
        return ""
    return "<references>\n" + "\n".join(ref_lines) + "\n</references>"


# ── Service formatting ───────────────────────────────────────────────────────


def _format_service_result(row: dict[str, Any], position: int) -> str:
    """Format a single service result into a readable markdown block."""
    lines: list[str] = []

    service_name = row.get("service_name_ar", "")
    provider = row.get("provider_name", "")
    platform = row.get("platform_name", "")

    # Header
    header = f"### [{position}] خدمة: {service_name}"
    if provider:
        header += f" — {provider}"
    lines.append(header)

    # Metadata
    meta_parts: list[str] = []
    if platform:
        meta_parts.append(f"**المنصة:** {platform}")
    url = row.get("service_url") or row.get("url", "")
    if url:
        meta_parts.append(f"**الرابط:** {url}")
    if meta_parts:
        lines.append(" | ".join(meta_parts))

    category = row.get("category", "")
    if category:
        lines.append(f"**التصنيف:** {category}")

    lines.append("")

    # Service markdown (the main content — pass through as-is)
    service_md = row.get("service_markdown", "")
    if service_md:
        lines.append(service_md.strip())
    else:
        # Fallback to service_context if no markdown
        context = row.get("service_context", "")
        if context:
            lines.append(context.strip())

    lines.append("")

    return "\n".join(lines)


def _collect_service_references(results: list[dict[str, Any]]) -> str:
    """Collect deduplicated service references block."""
    seen: set[str] = set()
    ref_lines: list[str] = []

    for row in results:
        service_ref = row.get("service_ref", "")
        service_name = row.get("service_name_ar", "")
        provider = row.get("provider_name", "")

        key = service_ref or service_name
        if key and key not in seen:
            seen.add(key)
            parts = [service_ref, service_name]
            if provider:
                parts.append(provider)
            ref_lines.append(f"- {' | '.join(p for p in parts if p)}")

    if not ref_lines:
        return ""
    return "<references>\n" + "\n".join(ref_lines) + "\n</references>"
