"""Search pipeline for case_search domain loop.

Two pipelines live here:

- `search_cases_pipeline` — legacy (prompt_1 / prompt_2): single-vector
  hybrid search via `hybrid_search_cases` RPC, returns formatted markdown.
- `search_case_section` — sectioned (prompt_3+): per-channel pure-semantic
  search via `search_case_sections` RPC, returns structured ChannelCandidates
  for the fusion layer to merge.

Both share the same score-fallback / formatting helpers at the bottom.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import CaseSearchDeps, ChannelCandidate, TypedQuery

logger = logging.getLogger(__name__)

# Default result counts
CASES_TOP_N = 10
MATCH_COUNT = 30

# Sectioned pipeline — how many hits to pull from each channel RPC before fusion.
# Fusion works on ranks so pulling more candidates costs little; the HNSW
# index handles it efficiently.
SECTION_MATCH_COUNT = 30

# Content truncation for case formatting
MAX_CONTENT_CHARS = 5_000


async def search_cases_pipeline(
    query: str,
    deps: CaseSearchDeps,
    precomputed_embedding: list[float] | None = None,
    semaphore: asyncio.Semaphore | None = None,
) -> tuple[str, int]:
    """Search cases via embed -> search_cases RPC -> RRF score fallback -> format.

    Args:
        query: Arabic search query.
        deps: CaseSearchDeps with supabase, embedding_fn.
        precomputed_embedding: Pre-computed embedding vector (skips embed step).
        semaphore: Optional concurrency limiter.

    Returns:
        (result_markdown, result_count) tuple.
    """
    if semaphore:
        async with semaphore:
            return await _search_cases_inner(query, deps, precomputed_embedding)
    return await _search_cases_inner(query, deps, precomputed_embedding)


async def _search_cases_inner(
    query: str,
    deps: CaseSearchDeps,
    precomputed_embedding: list[float] | None = None,
) -> tuple[str, int]:
    """Inner search implementation."""
    # Check for mock results
    if deps.mock_results and "cases" in deps.mock_results:
        mock_md = deps.mock_results["cases"]
        if isinstance(mock_md, str):
            return mock_md, 2

    events = deps._events

    try:
        events.append({
            "type": "status",
            "text": f"جاري البحث في السوابق القضائية: {query[:80]}...",
        })

        # Step 1: Embed query (or use precomputed)
        embedding = precomputed_embedding or await deps.embedding_fn(query)

        # Step 2: Hybrid search via RPC
        # Cases use low BM25 weight (0.1) because long Arabic queries cause
        # AND-based FTS to return 0 results. Semantic search is primary.
        events.append({"type": "status", "text": "جاري البحث في قاعدة بيانات الأحكام القضائية..."})
        candidates = await _hybrid_rpc_search(
            deps.supabase, "cases", query, embedding, MATCH_COUNT,
            full_text_weight=0.1, semantic_weight=0.9,
        )

        if not candidates:
            events.append({"type": "status", "text": "لم يتم العثور على سوابق قضائية مطابقة."})
            return "لم يتم العثور على سوابق قضائية مطابقة للاستعلام.", 0

        logger.info("Cases search: %d candidates for '%s'", len(candidates), query[:80])

        # Step 3: Score threshold filtering
        if deps.score_threshold > 0:
            before = len(candidates)
            candidates = [c for c in candidates if (c.get("score") or 0.0) >= deps.score_threshold]
            if before != len(candidates):
                logger.info("Score threshold %.4f: %d -> %d candidates", deps.score_threshold, before, len(candidates))

        if not candidates:
            events.append({"type": "status", "text": "لم يتم العثور على سوابق قضائية تتجاوز عتبة الدقة."})
            return "لم يتم العثور على سوابق قضائية مطابقة للاستعلام.", 0

        # Step 3b: RRF score fallback (top N by score)
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

        return result_md, len(top_candidates)

    except Exception as e:
        logger.error("Cases search failed for '%s': %s", query[:80], e, exc_info=True)
        events.append({"type": "status", "text": "حدث خطأ أثناء البحث في السوابق القضائية."})
        return f"خطأ أثناء البحث في السوابق القضائية: {e}", 0


# -- Shared helpers ------------------------------------------------------------


async def _hybrid_rpc_search(
    supabase: Any,
    domain: str,
    query_text: str,
    embedding: list[float],
    match_count: int,
    full_text_weight: float = 0.25,
    semantic_weight: float = 0.75,
    rrf_k: int = 1,
    filter_entity_id: str | None = None,
    filter_court_level: str | None = None,
) -> list[dict]:
    """Call a Supabase hybrid search RPC (BM25 + semantic via RRF).

    Must pass filter_entity_id + filter_court_level to disambiguate
    the overloaded DB function (PostgREST PGRST203).
    """
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
                    "rrf_k": rrf_k,
                    "filter_entity_id": filter_entity_id,
                    "filter_court_level": filter_court_level,
                },
            ).execute()
            return result.data or []
        except Exception as e:
            logger.warning("%s RPC failed: %s", rpc_name, e)
            return []

    return await asyncio.to_thread(_call)


def _score_fallback(candidates: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    """Sort by hybrid RRF score (descending, higher=better) and take top N."""
    return sorted(candidates, key=lambda c: c.get("score", 0.0), reverse=True)[:top_n]


def _truncate(text: str, max_chars: int) -> str:
    """Truncate text, appending '...' if truncated."""
    if not text or len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


# -- Case formatting ----------------------------------------------------------


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

    # Relevance scores
    hybrid_score = row.get("score")
    if hybrid_score is not None:
        lines.append(f"**درجة الصلة:** RRF: {round(float(hybrid_score), 4)}")

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

    # Content (the ruling text -- primary payload)
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


# -- Sectioned pipeline (prompt_3+) -------------------------------------------


async def search_case_section(
    query: "TypedQuery",
    deps: "CaseSearchDeps",
    sectors: list[str] | None = None,
    precomputed_embedding: list[float] | None = None,
    match_count: int = SECTION_MATCH_COUNT,
    semaphore: asyncio.Semaphore | None = None,
) -> list["ChannelCandidate"]:
    """Retrieve case-sections for one channel-tagged query.

    Calls the `search_case_sections` RPC (Wave 1 migration) against the
    single channel specified by `query.channel`, optionally pre-filtered by
    `legal_domains` overlap with `sectors`. The RPC is expected to return
    case-level metadata joined onto each `case_sections` hit so downstream
    formatting can render results without an N+1 follow-up.

    Args:
        query: TypedQuery with `text` and `channel`.
        deps: CaseSearchDeps — embedding fn, supabase client, mocks.
        sectors: Canonicalized legal-domain names; None / empty = no filter.
        precomputed_embedding: Skip embedding if provided (batched upstream).
        match_count: Upper bound on RPC rows returned.
        semaphore: Concurrency limiter for the sectioned search node.

    Returns:
        Ranked list of ChannelCandidate. Empty list on zero hits or error.
    """
    if semaphore:
        async with semaphore:
            return await _search_case_section_inner(
                query, deps, sectors, precomputed_embedding, match_count
            )
    return await _search_case_section_inner(
        query, deps, sectors, precomputed_embedding, match_count
    )


async def _search_case_section_inner(
    query: "TypedQuery",
    deps: "CaseSearchDeps",
    sectors: list[str] | None,
    precomputed_embedding: list[float] | None,
    match_count: int,
) -> list["ChannelCandidate"]:
    """Inner worker: embed → RPC → ChannelCandidate list."""
    from .models import ChannelCandidate

    # Mock hook (used by CLI --mock)
    if deps.mock_results and "case_sections" in deps.mock_results:
        mock_rows = deps.mock_results["case_sections"].get(query.channel, [])
        return [
            ChannelCandidate(
                case_id=r.get("case_id", ""),
                channel=query.channel,
                rank=i + 1,
                score=float(r.get("score", 0.0)),
                row=r,
            )
            for i, r in enumerate(mock_rows)
        ]

    events = deps._events
    events.append({
        "type": "status",
        "text": f"بحث [{query.channel}]: {query.text[:70]}...",
    })

    # Step 1: embed query (or use precomputed)
    try:
        embedding = precomputed_embedding or await deps.embedding_fn(query.text)
    except Exception as e:
        logger.error("Embedding failed for [%s] %s: %s", query.channel, query.text[:60], e)
        return []

    # Step 2: RPC call
    rows = await _case_sections_rpc(
        deps.supabase,
        channel=query.channel,
        embedding=embedding,
        sectors=sectors or None,
        match_count=match_count,
    )

    if not rows and sectors:
        # Sector filter may have wiped out all rows — retry without filter.
        logger.info(
            "search_case_section [%s]: sector filter %s returned 0 -- retrying without filter",
            query.channel, sectors,
        )
        rows = await _case_sections_rpc(
            deps.supabase,
            channel=query.channel,
            embedding=embedding,
            sectors=None,
            match_count=match_count,
        )

    # Step 3: score threshold filter (reuse same knob as legacy pipeline)
    if deps.score_threshold > 0:
        before = len(rows)
        rows = [r for r in rows if (r.get("score") or 0.0) >= deps.score_threshold]
        if before != len(rows):
            logger.debug(
                "search_case_section [%s]: score>=%.4f filtered %d -> %d",
                query.channel, deps.score_threshold, before, len(rows),
            )

    # Step 4: assemble candidates with 1-based rank
    candidates: list[ChannelCandidate] = []
    for i, row in enumerate(rows, start=1):
        case_id = row.get("case_id") or row.get("id") or ""
        if not case_id:
            continue
        candidates.append(
            ChannelCandidate(
                case_id=str(case_id),
                channel=query.channel,
                rank=i,
                score=float(row.get("score") or 0.0),
                row=row,
            )
        )

    logger.info(
        "search_case_section [%s]: %d candidates for '%s'",
        query.channel, len(candidates), query.text[:60],
    )
    return candidates


async def _case_sections_rpc(
    supabase: Any,
    *,
    channel: str,
    embedding: list[float],
    sectors: list[str] | None,
    match_count: int,
) -> list[dict]:
    """Call the `search_case_sections` RPC (Wave 1 migration).

    Expected RPC signature (for the DB side to match):

        search_case_sections(
            p_channel          case_channel,
            p_query_embedding  VECTOR(1024),
            p_sectors          TEXT[]  DEFAULT NULL,
            p_match_count      INT     DEFAULT 30
        )
        RETURNS TABLE (
            case_id                UUID,
            case_ref               TEXT,
            score                  REAL,       -- 1 - cosine_distance
            section_text           TEXT,       -- channel text (for debugging / reranker)
            court                  TEXT,
            city                   TEXT,
            court_level            TEXT,
            case_number            TEXT,
            judgment_number        TEXT,
            date_hijri             TEXT,
            details_url            TEXT,
            legal_domains          JSONB,
            referenced_regulations JSONB,
            appeal_result          TEXT,
            appeal_court           TEXT,
            appeal_date_hijri      TEXT,
            content                TEXT        -- full concatenated ruling for reranker
        );

    Filter semantics: `p_sectors IS NULL` means no filter; otherwise retain
    rows whose `legal_domains ?| p_sectors` (JSONB any-key-exists).
    """
    def _call() -> list[dict]:
        try:
            params = {
                "p_channel": channel,
                "p_query_embedding": embedding,
                "p_sectors": sectors,
                "p_match_count": match_count,
            }
            result = supabase.rpc("search_case_sections", params).execute()
            return result.data or []
        except Exception as e:
            logger.warning("search_case_sections RPC failed (%s): %s", channel, e)
            return []

    return await asyncio.to_thread(_call)


# Reranker-shape rendering moved to `case_unfold_reranker.py`.
# Aggregator-shape full-case assembly lives in `case_unfold_aggregator.py`.
# This module stays focused on search (RPC + per-query candidate retrieval).
