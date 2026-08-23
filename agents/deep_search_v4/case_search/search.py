"""Search pipeline for case_search domain loop.

Two pipelines live here:

- `search_cases_pipeline` — legacy (prompt_1 / prompt_2): single-vector
  hybrid search via `hybrid_search_cases` RPC, returns formatted markdown.
- `search_case_section` — sectioned (prompt_3+): per-channel pure-semantic
  search via the `search_case_topics` RPC (migration 101), grouped by case
  into structured ChannelCandidates for the reranker + fusion layers.

Wave 1 retarget (`.claude/plans/case_topics_loop.md` §5): the sectioned path
used to hit `search_case_sections`, which reaches only 20,669 of 30,531 cases.
`case_topics` reaches 29,734 — +43% corpus. The RPC returns FLAT topic rows
(deliberately not deduped) joined to the case header, so:

- grouping by `case_id` happens here (`group_topic_rows`), keeping EVERY
  matched topic per case (decision D1) instead of one row per case, and
- there is no enrichment round trip anymore — the header comes off the join.

Both share the same score-fallback / formatting helpers at the bottom.

RELIABILITY (incident 2026-08-22 — `.claude/plans/deep_search_retrieval_reliability.md`)
----------------------------------------------------------------------------
Both RPC helpers in this module queue through
`agents.deep_search_v4.shared.db_gate.search_gate()`, the process-wide
admission cap on concurrent Supabase search RPCs. The per-executor
`asyncio.Semaphore(state.concurrency)` in `loop.py` still bounds pipeline
work, but it is NOT the database ceiling: case_search and
reg_compliance_search run in parallel and each built its own, so peak
in-flight was ~20 against one Postgres instance. On 2026-08-22 all 16 RPCs of
one turn hit `httpx.ReadTimeout` at 15.0s together — a throughput knee (past
~6 concurrent, MORE parallelism makes the batch slower), not a slow query.

And the sectioned path no longer returns a bare list: `search_case_section`
returns a `CaseSearchOutcome` so a dead socket is distinguishable from an
empty corpus. See that dataclass for why the failure is TYPED rather than
raised.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from agents.deep_search_v4.shared.court_levels import court_level_ar
from agents.deep_search_v4.shared.db_gate import search_gate

if TYPE_CHECKING:
    from .models import (
        CaseSearchDeps,
        CaseSearchOutcome,
        ChannelCandidate,
        TypedQuery,
    )

logger = logging.getLogger(__name__)

# Default result counts
CASES_TOP_N = 10
MATCH_COUNT = 30

# Sectioned pipeline — how many TOPIC rows to pull from the per-kind RPC before
# grouping. Rows are topics, not cases: a case averages 2.1 principle / 3.6 fact
# / 3.7 basis topics, so N rows collapse to fewer than N distinct cases. 60 is
# the plan's calibrated floor (§4.3) so the grouped output reliably yields ≥ 25
# distinct cases — more than the 15 the reranker is shown. Grouping and fusion
# work on ranks, so pulling more costs nothing downstream.
SECTION_MATCH_COUNT = 60

# Channel (agent vocabulary) → `case_topic_kind` (DB enum).
#
# ⚠ THE ONLY TRANSLATION POINT. `facts` is PLURAL in the agent/expander
# vocabulary and SINGULAR (`fact`) in the DB enum. A mismatch makes the RPC
# return zero rows with NO error — a silent recall wipe-out. Never inline this
# mapping anywhere else; import the constant. See plan §5.2 / trap 1.
CHANNEL_TO_KIND: dict[str, str] = {
    "principle": "principle",
    "facts": "fact",
    "basis": "basis",
}

# Case-header columns the `search_case_topics` RPC joins onto every topic row.
# Lifted verbatim into `ChannelCandidate.row` by `group_topic_rows`, which is
# what the reranker formatter renders.
CASE_HEADER_FIELDS: tuple[str, ...] = (
    "case_ref",
    "court",
    "city",
    "court_level",
    "case_number",
    "date_hijri",
    "short_summary",
)

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
        topic_ev = {
            "type": "status",
            "text": f"جاري البحث في السوابق القضائية: {query[:80]}...",
        }
        events.append(topic_ev)
        # Stream the topic line LIVE; tag the SAME dict `streamed` on a successful
        # emit so the orchestrator's terminal flush skips it (no double-send). The
        # object stays on `_events` for forensic dumps. Other mechanics status
        # lines stay batched (message_service drops them).
        if deps.emit_sse is not None:
            try:
                deps.emit_sse(topic_ev)
                topic_ev["streamed"] = True
            except Exception:  # pragma: no cover - defensive; sink must not break search
                pass

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

    Gated on `search_gate()` — this is a real search RPC (a full hybrid scan
    over `cases`), so it consumes a database slot exactly like
    `search_case_topics` does and must queue behind the same cap.

    KNOWN LAUNDERING SITE, deliberately left in place. The `except` below
    turns a transport death into `[]`, which the caller cannot tell from "no
    matches" — the same bug that produced the 2026-08-22 incident on the
    sectioned path. It survives here because this is the LEGACY
    (prompt_1 / prompt_2) pipeline: nothing in production reaches it
    (`orchestrator.py:103` pins `case_expander_prompt_key = "prompt_3"`), and
    fixing it means changing `_search_cases_inner`'s `(markdown, count)`
    contract, which is a different piece of work from this one. If the legacy
    path is ever revived, fix this first.
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
            logger.error("%s RPC failed: %s", rpc_name, e, exc_info=True)
            return []

    async with search_gate() as gate_wait_ms:
        if gate_wait_ms >= 1000.0:
            logger.info(
                "%s: waited %.0fms at the search gate", rpc_name, gate_wait_ms,
            )
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

    # Header. `court_level` has THREE values in prod — the two-branch ternary
    # that used to live here relabelled all 125 supreme-court rulings as
    # ابتدائي. Canonical map only (agents/deep_search_v4/shared/court_levels.py).
    level_label = court_level_ar(court_level)
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
) -> "CaseSearchOutcome":
    """Retrieve case topics for one channel-tagged query, grouped by case.

    Calls the `search_case_topics` RPC (migration 101) against the single
    `case_topic_kind` that `query.channel` maps to via `CHANNEL_TO_KIND`, then
    groups the flat topic rows by `case_id` so each returned candidate carries
    EVERY topic of that case inside this sub-query's result window (D1).

    The RPC joins the case header onto each topic row, so the returned rows are
    already complete — there is no enrichment round trip downstream.

    Args:
        query: TypedQuery with `text` and `channel`.
        deps: CaseSearchDeps — embedding fn, supabase client, mocks.
        sectors: Canonicalized legal-domain names; None / empty = no filter.
            **Always None on the production path** (decision D3 / plan §1.2):
            the case executor no longer consumes `sector_picker`, because the
            9,860 cases with an empty `legal_domains` array are exactly the
            batch this retarget was built to recover, and 91% of the tagged
            cases carry المعاملات التجارية (near-zero selectivity). The
            argument survives only as a CLI experiment hatch (`--sectors`),
            and `p_sectors` survives in the RPC so the filter can be
            re-enabled after a `legal_domains` backfill.
        precomputed_embedding: Skip embedding if provided (batched upstream).
        match_count: Upper bound on RPC topic rows returned (pre-grouping).
        semaphore: Concurrency limiter for the sectioned search node. NOT the
            database ceiling — the RPC itself queues on `db_gate.search_gate`.

    Returns:
        A :class:`CaseSearchOutcome`. ``.candidates`` is the ranked
        ChannelCandidate list (one per case) and is empty on zero hits AND on
        failure — ``.error`` is what tells the two apart. Never raises for a
        dead RPC: the caller fans these out through a plain
        ``asyncio.gather``, so one dead socket must not take the node down.
    """
    if semaphore:
        async with semaphore:
            return await _search_case_section_inner(
                query, deps, sectors, precomputed_embedding, match_count,
            )
    return await _search_case_section_inner(
        query, deps, sectors, precomputed_embedding, match_count,
    )


async def _search_case_section_inner(
    query: "TypedQuery",
    deps: "CaseSearchDeps",
    sectors: list[str] | None,
    precomputed_embedding: list[float] | None,
    match_count: int,
) -> "CaseSearchOutcome":
    """Inner worker: embed → RPC → score capture → threshold → group.

    Every exit builds a :class:`CaseSearchOutcome`. The three failure exits
    (embedding dead, unmapped channel, RPC dead) set ``error`` to the
    exception type name and leave ``candidates`` empty; the success exits
    leave ``error`` None. Nothing here re-raises — see the dataclass docstring
    for why the failure is typed rather than thrown.
    """
    from .models import CaseSearchOutcome

    # Mock hook (used by CLI --mock). Accepts either the legacy
    # ``case_sections`` key or the current ``case_topics`` key; values are
    # per-channel lists of RPC-shaped TOPIC rows.
    mock_topics = None
    if deps.mock_results:
        mock_topics = (
            deps.mock_results.get("case_topics")
            or deps.mock_results.get("case_sections")
        )
    if isinstance(mock_topics, dict):
        mock_rows = mock_topics.get(query.channel, [])
        mock_cands = group_topic_rows(mock_rows, query.channel)
        return CaseSearchOutcome(
            candidates=mock_cands,
            count=len(mock_cands),
            max_score=_max_row_score(mock_rows),
            top_scores=_top_row_scores(mock_rows),
            topic_rows=len(mock_rows),
            topic_rows_kept=len(mock_rows),
        )

    events = deps._events
    topic_ev = {
        "type": "status",
        "text": f"بحث [{query.channel}]: {query.text[:70]}...",
    }
    events.append(topic_ev)
    # Stream the sectioned-channel topic line LIVE; tag the SAME dict `streamed`
    # on a successful emit so the orchestrator's terminal flush skips it (no
    # double-send). The object stays on `_events` for forensic dumps.
    if deps.emit_sse is not None:
        try:
            deps.emit_sse(topic_ev)
            topic_ev["streamed"] = True
        except Exception:  # pragma: no cover - defensive; sink must not break search
            pass

    # Step 1: embed query (or use precomputed)
    try:
        embedding = precomputed_embedding or await deps.embedding_fn(query.text)
    except Exception as e:
        logger.error("Embedding failed for [%s] %s: %s", query.channel, query.text[:60], e)
        return CaseSearchOutcome(error=type(e).__name__)

    # Step 2: RPC call. `kind` is the ONE place the channel vocabulary is
    # translated to the DB enum (`facts` → `fact`).
    kind = CHANNEL_TO_KIND.get(query.channel)
    if kind is None:
        logger.error(
            "search_case_section: unknown channel %r (expected one of %s) — "
            "no RPC call made",
            query.channel, sorted(CHANNEL_TO_KIND),
        )
        # A programming error, not a transport one, but it is still not an
        # empty corpus — so it gets an `error` tag too. The name is not an
        # exception type because nothing was raised; that is deliberate and
        # legible in the span.
        return CaseSearchOutcome(error="UnknownChannel")

    try:
        rows, gate_wait_ms = await search_case_topics_rpc(
            deps.supabase,
            kind=kind,
            embedding=embedding,
            sectors=sectors or None,
            match_count=match_count,
        )
    except Exception as e:
        # THE 2026-08-22 SITE. This used to live inside `search_case_topics_rpc`
        # as `except Exception: return []`, which made `httpx.ReadTimeout`
        # indistinguishable from "the corpus has nothing". The catch stays
        # (per-query isolation: `SectionedSearchNode` gathers these without
        # `return_exceptions=True`), but the fate now comes back WITH the
        # empty list instead of replacing it.
        logger.error(
            "search_case_section [%s/%s]: RPC died in transit (%s: %s) for '%s'",
            query.channel, kind, type(e).__name__, e, query.text[:60],
            exc_info=True,
        )
        events.append({
            "type": "status",
            "text": "تعذّر الوصول إلى قاعدة بيانات الأحكام لهذا الاستعلام.",
        })
        return CaseSearchOutcome(error=type(e).__name__)
    # NOTE: there is deliberately NO "filter returned 0 rows → retry
    # unfiltered" fallback here. It used to hide the sector filter's damage —
    # a partial wipe-out looked like a successful filter in the logs, so the
    # 9,860 untagged cases stayed invisible. Plan §1.2 / trap 4: do not
    # reintroduce it. If a sector filter ever comes back, its zero-row cases
    # must be loud.

    # Step 3: capture the similarity scores BEFORE anything destroys them.
    #
    # Order matters and both destroyers are directly below: the threshold
    # filter drops low rows, and `group_topic_rows` collapses every topic of a
    # case into one candidate carrying only the MAX. Reading the score after
    # either one answers a different question than "how good was the best
    # thing the database found for this sub-query?", which is the question the
    # reranker's empty output cannot answer for itself.
    topic_rows = len(rows)
    max_score = _max_row_score(rows)
    top_scores = _top_row_scores(rows)

    # Step 4: score threshold filter, applied to TOPIC rows before grouping
    # (a case survives if any of its matched topics clears the threshold).
    if deps.score_threshold > 0:
        before = len(rows)
        rows = [r for r in rows if (r.get("score") or 0.0) >= deps.score_threshold]
        if before != len(rows):
            logger.debug(
                "search_case_section [%s]: score>=%.4f filtered %d -> %d topic rows",
                query.channel, deps.score_threshold, before, len(rows),
            )

    # Step 5: group flat topic rows into one candidate per case (D1)
    candidates = group_topic_rows(rows, query.channel)

    logger.info(
        "search_case_section [%s/%s]: %d topic rows (%d kept) -> %d cases, "
        "max_score=%.4f, gate_wait=%.0fms for '%s'",
        query.channel, kind, topic_rows, len(rows), len(candidates),
        max_score, gate_wait_ms, query.text[:60],
    )
    return CaseSearchOutcome(
        candidates=candidates,
        count=len(candidates),
        max_score=max_score,
        top_scores=top_scores,
        topic_rows=topic_rows,
        topic_rows_kept=len(rows),
        gate_wait_ms=gate_wait_ms,
    )


def _max_row_score(rows: list[dict[str, Any]]) -> float:
    """Best `score` across raw RPC topic rows. 0.0 on an empty list.

    Call this on the RPC's own output — before the threshold filter and
    before `group_topic_rows`. See `CaseSearchOutcome` for why.
    """
    return max((float(r.get("score") or 0.0) for r in rows), default=0.0)


def _top_row_scores(rows: list[dict[str, Any]], n: int = 5) -> list[float]:
    """Top `n` raw topic scores, descending, rounded to 4 dp.

    Five, not one: a single 0.61 next to four rows at 0.24 is a lucky match in
    a barren neighbourhood, while five rows clustered at 0.57-0.61 is real
    coverage. The shape of the head is what separates those, and it costs a
    handful of floats on a span.
    """
    scores = sorted((float(r.get("score") or 0.0) for r in rows), reverse=True)
    return [round(s, 4) for s in scores[:n]]


def group_topic_rows(
    rows: list[dict[str, Any]],
    channel: str,
) -> list["ChannelCandidate"]:
    """Group flat `search_case_topics` rows into one candidate per case.

    Decision D1: a case that surfaces via 2+ topics inside the same sub-query
    window is rendered ONCE with ALL of its matched topics, not once per topic.
    This replaces the old `enrich_candidates` hop — the case header rides along
    on every topic row (RPC join), so there is nothing left to fetch.

    - `topics` is score-desc, so `topics[0]` is always the best match.
    - case score = max topic score.
    - cases are ranked by that score, 1-based, insertion order breaking ties
      (the RPC already returns rows in ANN order).

    Args:
        rows: RPC rows — `case_id`, `topic_ref`, `topic_index`, `topic_text`,
            `attrs`, `score` + the CASE_HEADER_FIELDS join.
        channel: agent-facing channel name stamped onto each candidate.

    Returns:
        Ranked list of ChannelCandidate (one per distinct case_id).
    """
    from .models import ChannelCandidate

    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        case_id = str(row.get("case_id") or row.get("id") or "").strip()
        if not case_id:
            continue
        score = float(row.get("score") or 0.0)
        entry = grouped.get(case_id)
        if entry is None:
            header = {f: row.get(f) for f in CASE_HEADER_FIELDS}
            entry = {
                "header": header,
                "topics": [],
                "score": score,
                "order": len(grouped),
            }
            grouped[case_id] = entry
        if score > entry["score"]:
            entry["score"] = score
        entry["topics"].append({
            "topic_ref": row.get("topic_ref") or "",
            "topic_index": row.get("topic_index"),
            "text": (row.get("topic_text") or row.get("text") or "").strip(),
            "attrs": row.get("attrs") or {},
            "score": score,
            # DB `case_topic_kind` verbatim (`fact`, not `facts`). Carried so
            # the reranker formatter can tag the topic without re-deriving the
            # kind from the channel — i.e. without a SECOND channel→kind
            # translation point (trap 1).
            "kind": row.get("kind") or "",
        })

    ordered = sorted(
        grouped.items(),
        key=lambda kv: (-kv[1]["score"], kv[1]["order"]),
    )

    candidates: list[ChannelCandidate] = []
    for i, (case_id, entry) in enumerate(ordered, start=1):
        topics = sorted(
            entry["topics"],
            key=lambda t: float(t.get("score") or 0.0),
            reverse=True,
        )
        row = dict(entry["header"])
        # `score` on the row mirrors ChannelCandidate.score — kept for the
        # forensic dumps / fusion row-merge heuristic that read row dicts.
        row["score"] = entry["score"]
        candidates.append(
            ChannelCandidate(
                case_id=case_id,
                channel=channel,
                rank=i,
                score=entry["score"],
                row=row,
                topics=topics,
            )
        )
    return candidates


async def search_case_topics_rpc(
    supabase: Any,
    *,
    kind: str,
    embedding: list[float],
    sectors: list[str] | None,
    match_count: int,
) -> tuple[list[dict], float]:
    """Call the `search_case_topics` RPC (migration 101).

    RPC signature (live on prod):

        search_case_topics(
            p_kind            case_topic_kind,   -- principle | fact | basis
            p_query_embedding vector(1024),
            p_sectors         text[] DEFAULT NULL,
            p_match_count     int    DEFAULT 60
        )
        RETURNS TABLE (
            topic_id      uuid,
            topic_ref     text,
            case_id       uuid,
            case_ref      text,
            entity_ref    text,
            kind          text,
            topic_index   int,
            topic_text    text,
            attrs         jsonb,
            score         real,      -- 1 - cosine_distance
            court         text,      -- ↓ case header, joined once
            city          text,
            court_level   text,
            case_number   text,
            date_hijri    text,
            short_summary text
        );

    Rows are FLAT topic rows, NOT deduped by case — grouping happens in
    `group_topic_rows` so a case can carry >1 matched topic (D1).

    `kind` must already be a `case_topic_kind` value; callers map from the
    channel vocabulary via `CHANNEL_TO_KIND` (`facts` → `fact`).

    Admission-gated on `db_gate.search_gate()`. That gate is the real ceiling
    on concurrent database load — the per-executor semaphores in `loop.py`
    only bound how many pipeline tasks may queue here. Nothing else in this
    function may be moved above the `async with`: the point is that the RPC
    does not start until a slot exists.

    Returns:
        ``(rows, gate_wait_ms)`` — the flat topic rows, and how long this call
        queued for a database slot. The wait is returned rather than logged
        alone so a slow turn can be attributed to QUEUEING (the gate working,
        fan-out too wide) rather than to a slow database (wait ≈ 0, RPC slow).
        In a latency histogram those two are indistinguishable.

    Raises:
        Whatever the transport raises — `httpx.ReadTimeout` above all, which
        is what killed all 6 case RPCs on 2026-08-22 — plus
        `db_gate.SearchGateTimeout` when no slot opened in time. This used to
        `return []`, and that swallow is the whole reason the turn told a
        lawyer the corpus was empty. The catch now lives ONE level up, in
        `_search_case_section_inner`, where the failure can be recorded as a
        failure instead of forged into a result.
    """
    def _call() -> list[dict]:
        params = {
            "p_kind": kind,
            "p_query_embedding": embedding,
            # D3: always NULL from the executor. Kept in the signature so
            # the filter can be re-enabled after a legal_domains backfill.
            "p_sectors": sectors,
            "p_match_count": match_count,
        }
        result = supabase.rpc("search_case_topics", params).execute()
        return result.data or []

    async with search_gate() as gate_wait_ms:
        rows = await asyncio.to_thread(_call)
    return rows, gate_wait_ms


# Reranker-shape rendering moved to `case_unfold_reranker.py`.
# Aggregator-shape full-case assembly lives in `case_unfold_aggregator.py`.
# This module stays focused on search (RPC + per-query candidate retrieval).
