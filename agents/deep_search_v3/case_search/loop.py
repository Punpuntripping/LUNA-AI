"""Graph nodes and entry points for the case_search loop.

Two pipelines share the module:

Legacy (prompt_1 / prompt_2):
    ExpanderNode → SearchNode → RerankerNode → End(CaseSearchResult)

Sectioned (prompt_3+):
    SectionedExpanderNode → SectionedSearchNode → FusionNode
                          → SectionedRerankerNode → End(CaseSearchResult)

No retry loop, no local aggregator. The shared deep_search_v3/aggregator/
handles synthesis; this module returns reranker_results for it to consume.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Union

from pydantic_graph import BaseNode, End, Graph, GraphRunContext

from .unfold import assemble_kept_cases
from .unfold import (
    enrich_candidates,
    format_bucket_for_reranker,
    format_candidate_for_reranker,
)
from .expander import EXPANDER_LIMITS, create_expander_agent, get_expander_model_id as _get_expander_model_id
from .fusion import assemble_buckets, rrf_fuse, wrap_as_fused
from .logger import (
    save_expander_md,
    save_reranker_query_md,
    save_run_json,
    save_run_overview_md,
    save_search_query_md,
)
from .models import (
    CaseSearchDeps,
    CaseSearchResult,
    ChannelCandidate,
    ExpanderOutput,
    ExpanderOutputV2,
    FusedCandidate,
    LoopState,
    RerankerQueryResult,
    SearchResult,
    TypedQuery,
)
from .prompts import (
    DEFAULT_EXPANDER_PROMPT,
    build_expander_user_message,
    get_expander_prompt,
    is_sectioned_prompt,
)
from .reranker import run_reranker_for_query
from .search import search_case_section, search_cases_pipeline
from .sector_vocab import canonicalize_sectors

logger = logging.getLogger(__name__)


# -- ExpanderNode --------------------------------------------------------------


class ExpanderNode(BaseNode[LoopState, CaseSearchDeps, CaseSearchResult]):
    """Runs QueryExpander agent. Creates 1-4 search queries. Always → SearchNode."""

    async def run(
        self,
        ctx: GraphRunContext[LoopState, CaseSearchDeps],
    ) -> SearchNode:
        state = ctx.state
        state.round_count += 1

        logger.info(
            "ExpanderNode round %d -- focus: %s",
            state.round_count,
            state.focus_instruction[:80],
        )

        expander = create_expander_agent(
            prompt_key=state.expander_prompt_key,
            thinking_effort=state.thinking_effort,
            model_override=state.model_override,
        )

        user_message = build_expander_user_message(
            state.focus_instruction,
            state.user_context,
        )

        try:
            result = await expander.run(user_message, usage_limits=EXPANDER_LIMITS)
            output: ExpanderOutput = result.output

            eu = result.usage()
            usage_entry = {
                "agent": "expander",
                "round": state.round_count,
                "requests": eu.requests,
                "input_tokens": eu.input_tokens,
                "output_tokens": eu.output_tokens,
                "total_tokens": eu.total_tokens,
            }
            if eu.details:
                usage_entry["details"] = dict(eu.details)
            state.inner_usage.append(usage_entry)

            state.expander_output = output
            state.all_queries_used.extend(output.queries)

            state.sse_events.append({
                "type": "status",
                "text": f"تم توليد {len(output.queries)} استعلامات بحث في السوابق القضائية",
            })

            logger.info(
                "ExpanderNode: %d queries -- %s",
                len(output.queries),
                ", ".join(q[:40] for q in output.queries),
            )

            if ctx.deps._log_id:
                try:
                    save_expander_md(
                        log_id=ctx.deps._log_id,
                        round_num=state.round_count,
                        prompt_key=state.expander_prompt_key,
                        system_prompt=get_expander_prompt(state.expander_prompt_key),
                        user_message=user_message,
                        output=output,
                        usage=result.usage(),
                        messages_json=result.all_messages_json(),
                    )
                except Exception as e:
                    logger.warning("Failed to save expander MD: %s", e)

        except Exception as e:
            logger.error("ExpanderNode error: %s", e, exc_info=True)
            state.sse_events.append({
                "type": "status",
                "text": "حدث خطأ أثناء توسيع الاستعلامات.",
            })
            state.expander_output = ExpanderOutput(
                queries=[state.focus_instruction],
                rationales=["Fallback: expander failed"],
            )
            state.all_queries_used.append(state.focus_instruction)

        return SearchNode()


# -- SearchNode ----------------------------------------------------------------


class SearchNode(BaseNode[LoopState, CaseSearchDeps, CaseSearchResult]):
    """Programmatic search — no LLM. Runs queries concurrently. Always → RerankerNode."""

    async def run(
        self,
        ctx: GraphRunContext[LoopState, CaseSearchDeps],
    ) -> RerankerNode:
        state = ctx.state
        deps = ctx.deps

        queries = state.expander_output.queries if state.expander_output else []
        if not queries:
            logger.warning("SearchNode: no queries to execute")
            return RerankerNode()

        logger.info(
            "SearchNode: executing %d queries (concurrency=%d)",
            len(queries),
            state.concurrency,
        )
        state.sse_events.append({
            "type": "status",
            "text": f"جاري تنفيذ {len(queries)} استعلامات بحث في الأحكام القضائية...",
        })

        from agents.utils.embeddings import embed_regulation_queries_alibaba

        embeddings = await embed_regulation_queries_alibaba(queries)

        sem = asyncio.Semaphore(state.concurrency)
        tasks = [
            search_cases_pipeline(
                query=q,
                deps=deps,
                precomputed_embedding=emb,
                semaphore=sem,
            )
            for q, emb in zip(queries, embeddings)
        ]

        results_raw: list[tuple[str, int]] = await asyncio.gather(*tasks)

        rationales = (
            state.expander_output.rationales
            if state.expander_output and state.expander_output.rationales
            else []
        )

        for qi, (query, (raw_markdown, result_count)) in enumerate(
            zip(queries, results_raw), 1
        ):
            rationale = rationales[qi - 1] if qi <= len(rationales) else ""

            state.all_search_results.append(
                SearchResult(query=query, raw_markdown=raw_markdown, result_count=result_count)
            )

            state.search_results_log.append({
                "round": state.round_count,
                "query": query,
                "rationale": rationale,
                "result_count": result_count,
                "raw_markdown_length": len(raw_markdown),
                "raw_markdown": raw_markdown,
            })

            if deps._log_id:
                try:
                    save_search_query_md(
                        log_id=deps._log_id,
                        round_num=state.round_count,
                        query_index=qi,
                        query=query,
                        raw_markdown=raw_markdown,
                        result_count=result_count,
                        rationale=rationale,
                    )
                except Exception as e:
                    logger.warning("Failed to save search MD: %s", e)

        total_count = sum(rc for _, rc in results_raw)
        logger.info(
            "SearchNode: %d queries returned %d total results",
            len(queries),
            total_count,
        )
        state.sse_events.append({
            "type": "status",
            "text": f"تم استرجاع {total_count} حكم قضائي — جاري التقييم والتصفية...",
        })

        return RerankerNode()


# -- RerankerNode --------------------------------------------------------------


class RerankerNode(BaseNode[LoopState, CaseSearchDeps, CaseSearchResult]):
    """Per-query LLM reranker — concurrent. Stores results, returns End."""

    async def run(
        self,
        ctx: GraphRunContext[LoopState, CaseSearchDeps],
    ) -> End[CaseSearchResult]:
        state = ctx.state
        deps = ctx.deps

        current_round_logs = [
            sr for sr in state.search_results_log
            if sr.get("round") == state.round_count
        ]

        if not current_round_logs:
            logger.warning("RerankerNode: no search results for round %d", state.round_count)
            return End(
                CaseSearchResult(
                    reranker_results=[],
                    queries_used=list(state.all_queries_used),
                    rounds_used=state.round_count,
                    expander_prompt_key=state.expander_prompt_key,
                )
            )

        state.sse_events.append({
            "type": "status",
            "text": f"جاري تصنيف وتصفية النتائج ({len(current_round_logs)} استعلام)...",
        })

        # Run per-query reranking concurrently
        tasks = [
            run_reranker_for_query(
                query=sr["query"],
                rationale=sr.get("rationale", ""),
                raw_markdown=sr.get("raw_markdown", ""),
                model_override=state.model_override,
            )
            for sr in current_round_logs
        ]

        try:
            all_results = await asyncio.gather(*tasks)
        except Exception as e:
            logger.error("RerankerNode: gather failed: %s", e, exc_info=True)
            state.sse_events.append({
                "type": "status",
                "text": "حدث خطأ أثناء تصنيف نتائج البحث.",
            })
            return End(
                CaseSearchResult(
                    reranker_results=[],
                    queries_used=list(state.all_queries_used),
                    rounds_used=state.round_count,
                    expander_prompt_key=state.expander_prompt_key,
                )
            )

        total_kept = 0
        total_dropped = 0

        for qi, (reranker_result, usage_entries, decision_log) in enumerate(all_results, 1):
            state.reranker_results.append(reranker_result)

            for ue in usage_entries:
                ue["round"] = state.round_count
                state.inner_usage.append(ue)

            total_kept += len(reranker_result.results)
            total_dropped += reranker_result.dropped_count

            if deps._log_id:
                try:
                    save_reranker_query_md(
                        log_id=deps._log_id,
                        query_index=qi,
                        query=reranker_result.query,
                        reranker_result=reranker_result,
                        decision_log=decision_log,
                    )
                except Exception as e:
                    logger.warning("Failed to save reranker MD: %s", e)

        logger.info(
            "RerankerNode: %d queries — %d kept, %d dropped",
            len(all_results),
            total_kept,
            total_dropped,
        )
        state.sse_events.append({
            "type": "status",
            "text": (
                f"اكتملت تصفية الأحكام: {total_kept} حكم محتفظ به، "
                f"{total_dropped} محذوف"
            ),
        })

        return End(
            CaseSearchResult(
                reranker_results=list(state.reranker_results),
                queries_used=list(state.all_queries_used),
                rounds_used=state.round_count,
                expander_prompt_key=state.expander_prompt_key,
            )
        )


# -- Sectioned pipeline (prompt_3+) -------------------------------------------


class SectionedExpanderNode(BaseNode[LoopState, CaseSearchDeps, CaseSearchResult]):
    """Sectioned expander — emits ExpanderOutputV2 (sectors + typed queries).

    Writes state.expander_output_v2 and state.all_queries_used (for logging).
    Always → SectionedSearchNode.
    """

    async def run(
        self,
        ctx: GraphRunContext[LoopState, CaseSearchDeps],
    ) -> "SectionedSearchNode":
        state = ctx.state
        state.round_count += 1

        logger.info(
            "SectionedExpanderNode round %d -- focus: %s",
            state.round_count,
            state.focus_instruction[:80],
        )

        expander = create_expander_agent(
            prompt_key=state.expander_prompt_key,
            thinking_effort=state.thinking_effort,
            model_override=state.model_override,
        )
        user_message = build_expander_user_message(
            state.focus_instruction,
            state.user_context,
        )

        try:
            result = await expander.run(user_message, usage_limits=EXPANDER_LIMITS)
            output: ExpanderOutputV2 = result.output

            eu = result.usage()
            usage_entry = {
                "agent": "expander",
                "round": state.round_count,
                "requests": eu.requests,
                "input_tokens": eu.input_tokens,
                "output_tokens": eu.output_tokens,
                "total_tokens": eu.total_tokens,
            }
            if eu.details:
                usage_entry["details"] = dict(eu.details)
            state.inner_usage.append(usage_entry)

            state.expander_output_v2 = output
            state.all_queries_used.extend(q.text for q in output.queries)

            by_channel: dict[str, int] = {}
            for q in output.queries:
                by_channel[q.channel] = by_channel.get(q.channel, 0) + 1

            state.sse_events.append({
                "type": "status",
                "text": (
                    f"تم توليد {len(output.queries)} استعلامات عبر {len(by_channel)} قنوات "
                    f"({', '.join(f'{c}:{n}' for c, n in by_channel.items())})"
                ),
            })

            if output.legal_sectors:
                state.sse_events.append({
                    "type": "status",
                    "text": f"قطاعات مُصنَّفة: {' | '.join(output.legal_sectors)}",
                })

            logger.info(
                "SectionedExpanderNode: %d queries, sectors=%s, by_channel=%s",
                len(output.queries),
                output.legal_sectors,
                by_channel,
            )

            if ctx.deps._log_id:
                try:
                    # Re-use legacy logger by adapting V2 output to the flat shape
                    flat = ExpanderOutput(
                        queries=[q.text for q in output.queries],
                        rationales=[
                            f"[{q.channel}] {q.rationale}" for q in output.queries
                        ],
                    )
                    save_expander_md(
                        log_id=ctx.deps._log_id,
                        round_num=state.round_count,
                        prompt_key=state.expander_prompt_key,
                        system_prompt=get_expander_prompt(state.expander_prompt_key),
                        user_message=user_message,
                        output=flat,
                        usage=result.usage(),
                        messages_json=result.all_messages_json(),
                    )
                except Exception as e:
                    logger.warning("Failed to save sectioned expander MD: %s", e)

        except Exception as e:
            logger.error("SectionedExpanderNode error: %s", e, exc_info=True)
            state.sse_events.append({
                "type": "status",
                "text": "حدث خطأ أثناء توسيع الاستعلامات (sectioned).",
            })
            # Fallback: single principle query with the focus instruction
            state.expander_output_v2 = ExpanderOutputV2(
                legal_sectors=None,
                queries=[
                    TypedQuery(
                        text=state.focus_instruction,
                        channel="principle",
                        rationale="Fallback: sectioned expander failed",
                    )
                ],
            )
            state.all_queries_used.append(state.focus_instruction)

        return SectionedSearchNode()


class SectionedSearchNode(BaseNode[LoopState, CaseSearchDeps, CaseSearchResult]):
    """Dispatch each typed query to search_case_section concurrently.

    Groups results by channel into state.channel_candidates, then → FusionNode.
    """

    async def run(
        self,
        ctx: GraphRunContext[LoopState, CaseSearchDeps],
    ) -> "FusionNode":
        state = ctx.state
        deps = ctx.deps

        output = state.expander_output_v2
        queries: list[TypedQuery] = list(output.queries) if output else []

        # CLI --channels subset override: drop queries whose channel isn't allowed.
        if deps.cli_channels:
            before = len(queries)
            queries = [q for q in queries if q.channel in deps.cli_channels]
            if before != len(queries):
                logger.info(
                    "SectionedSearchNode: --channels %s dropped %d/%d queries",
                    deps.cli_channels, before - len(queries), before,
                )

        if not queries:
            logger.warning("SectionedSearchNode: no queries to execute")
            return FusionNode()

        # Sector filter: CLI override > expander's pick.
        #   deps.cli_sectors is None  → use expander
        #   deps.cli_sectors == []    → force no filter
        #   deps.cli_sectors == [...] → use the list verbatim (still canonicalized)
        if deps.cli_sectors is not None:
            raw_sectors = list(deps.cli_sectors)
            logger.info("SectionedSearchNode: --sectors override = %s", raw_sectors)
        else:
            raw_sectors = list(output.legal_sectors) if output and output.legal_sectors else []

        if raw_sectors:
            canonical = canonicalize_sectors(raw_sectors)
            if canonical != raw_sectors:
                logger.info(
                    "SectionedSearchNode: sector canonicalization: %s -> %s",
                    raw_sectors, canonical,
                )
            sectors: list[str] | None = canonical or None
        else:
            sectors = None

        logger.info(
            "SectionedSearchNode: %d queries, sectors=%s, concurrency=%d",
            len(queries), sectors, state.concurrency,
        )
        # Count DISTINCT channels — not query count — so the status reflects
        # actual channel coverage (principle/facts/basis ≤ 3).
        distinct_channels = len({q.channel for q in queries})
        state.sse_events.append({
            "type": "status",
            "text": f"جاري تنفيذ {len(queries)} استعلامات مُقنّنة على {distinct_channels} قناة...",
        })

        # Batch-embed all queries in one API call (Alibaba v4, 1024 dims)
        from agents.utils.embeddings import embed_regulation_queries_alibaba

        query_texts = [q.text for q in queries]
        embeddings = await embed_regulation_queries_alibaba(query_texts)

        sem = asyncio.Semaphore(state.concurrency)
        tasks = [
            search_case_section(
                query=q,
                deps=deps,
                sectors=sectors,
                precomputed_embedding=emb,
                semaphore=sem,
            )
            for q, emb in zip(queries, embeddings)
        ]
        per_query_candidates: list[list[ChannelCandidate]] = await asyncio.gather(*tasks)

        # Group candidates by channel. If multiple typed queries share a
        # channel, keep each query's list intact but merge by case_id —
        # best rank wins so a case that placed #1 for any query keeps rank 1.
        by_channel: dict[str, dict[str, ChannelCandidate]] = {}
        for q, cands in zip(queries, per_query_candidates):
            bucket = by_channel.setdefault(q.channel, {})
            for c in cands:
                existing = bucket.get(c.case_id)
                if existing is None or c.rank < existing.rank:
                    bucket[c.case_id] = c

        # Re-rank within each channel so ranks are 1..N after merging
        channel_candidates: dict[str, list[ChannelCandidate]] = {}
        for channel, by_case in by_channel.items():
            merged = sorted(by_case.values(), key=lambda c: c.rank)
            channel_candidates[channel] = [
                ChannelCandidate(
                    case_id=c.case_id,
                    channel=c.channel,
                    rank=i + 1,
                    score=c.score,
                    row=c.row,
                )
                for i, c in enumerate(merged)
            ]

        # Enrich every candidate with minimal case-header metadata in ONE
        # batched `cases` SELECT. The search RPC only returns
        # (case_id, case_ref, score, section_text) — the reranker needs
        # court / date / case_number / legal_domains on top of that.
        all_cands = [c for lst in channel_candidates.values() for c in lst]
        if all_cands:
            await enrich_candidates(deps.supabase, all_cands)

        state.channel_candidates = channel_candidates

        # Build per-query enriched candidate lists (for the per-query reranker
        # path, mirroring reg_search). Each list preserves the query's own
        # 1..N rank; only the row metadata is lifted from the channel-merged
        # enriched copies so reranker markdown carries court/date/domains.
        per_query_enriched: list[tuple[TypedQuery, list[ChannelCandidate]]] = []
        for q, cands in zip(queries, per_query_candidates):
            enriched_by_id = {
                c.case_id: c
                for c in channel_candidates.get(q.channel, [])
            }
            enriched_q_cands: list[ChannelCandidate] = []
            for c in cands:
                merged = enriched_by_id.get(c.case_id)
                if merged is not None:
                    # Keep query-scoped rank/score; adopt merged row metadata.
                    enriched_q_cands.append(
                        ChannelCandidate(
                            case_id=c.case_id,
                            channel=c.channel,
                            rank=c.rank,
                            score=c.score,
                            row=merged.row,
                        )
                    )
                else:
                    enriched_q_cands.append(c)
            per_query_enriched.append((q, enriched_q_cands))
        state.per_query_candidates = per_query_enriched

        # Record per-query search results for logging parity with legacy path
        for qi, (q, cands) in enumerate(zip(queries, per_query_candidates), start=1):
            # Pull the enriched version for this query from the list we just built
            display = per_query_enriched[qi - 1][1][:15]

            if display:
                header = f"## {q.channel} — {q.text} ({len(cands)} نتيجة)\n"
                blocks = [
                    format_candidate_for_reranker(c, i)
                    for i, c in enumerate(display, start=1)
                ]
                raw_md = "\n".join([header, *blocks])
            else:
                raw_md = "لم يتم العثور على سوابق قضائية مطابقة للاستعلام."

            state.all_search_results.append(
                SearchResult(
                    query=q.text,
                    raw_markdown=raw_md,
                    result_count=len(cands),
                    channel=q.channel,
                )
            )

            state.search_results_log.append({
                "round": state.round_count,
                "query": q.text,
                "channel": q.channel,
                "rationale": q.rationale,
                "result_count": len(cands),
                "raw_markdown_length": len(raw_md),
                "raw_markdown": raw_md,
            })

            if deps._log_id:
                try:
                    save_search_query_md(
                        log_id=deps._log_id,
                        round_num=state.round_count,
                        query_index=qi,
                        query=f"[{q.channel}] {q.text}",
                        raw_markdown=raw_md,
                        result_count=len(cands),
                        rationale=q.rationale,
                    )
                except Exception as e:
                    logger.warning("Failed to save sectioned search MD: %s", e)

        # Post-dedup count (after merging duplicate case_ids within a channel).
        total = sum(len(cs) for cs in channel_candidates.values())
        by_ch_counts = {ch: len(cs) for ch, cs in channel_candidates.items()}
        # Pre-dedup raw RPC result count — matches search_results_log's result_count sum.
        raw_total = sum(len(cands) for cands in per_query_candidates)
        logger.info(
            "SectionedSearchNode: raw=%d deduped=%d by_channel=%s",
            raw_total, total, by_ch_counts,
        )
        state.sse_events.append({
            "type": "status",
            "text": f"تم استرجاع {total} حكم فريد عبر القنوات — جاري الدمج...",
        })

        return FusionNode()


class FusionNode(BaseNode[LoopState, CaseSearchDeps, CaseSearchResult]):
    """RRF fuse per-channel candidates into the 4-bucket output.

    Writes state.fused_buckets, then → SectionedRerankerNode.
    """

    async def run(
        self,
        ctx: GraphRunContext[LoopState, CaseSearchDeps],
    ) -> "SectionedRerankerNode":
        state = ctx.state

        if not state.channel_candidates:
            logger.warning("FusionNode: no channel candidates")
            state.fused_buckets = {"principle": [], "facts": [], "basis": [], "fused": []}
            return SectionedRerankerNode()

        fused = rrf_fuse(state.channel_candidates)
        buckets = assemble_buckets(state.channel_candidates, fused)
        state.fused_buckets = buckets

        logger.info(
            "FusionNode: fused=%d (from %d unique cases), buckets per_channel=%s",
            len(buckets.get("fused", [])),
            len(fused),
            {ch: len(buckets.get(ch, [])) for ch in ("principle", "facts", "basis")},
        )
        state.sse_events.append({
            "type": "status",
            "text": (
                f"تم الدمج: {len(buckets.get('fused', []))} حكم في القائمة الموحّدة، "
                f"{len(buckets.get('principle', []))} مبدأ، "
                f"{len(buckets.get('facts', []))} وقائع، "
                f"{len(buckets.get('basis', []))} اسانيد"
            ),
        })
        return SectionedRerankerNode()


class SectionedRerankerNode(BaseNode[LoopState, CaseSearchDeps, CaseSearchResult]):
    """Rerank each typed query against ITS OWN channel candidates in parallel.

    Mirrors reg_search's per-query reranker pattern (agents/deep_search_v3/
    reg_search/loop.py:_process_one → asyncio.gather): every typed query
    sees only the results retrieved for it, not a cross-query fused bucket.
    Each call is independent so we launch them all concurrently.

    Per-query full-content substitution via `assemble_kept_cases` — kept
    cases are refetched (full ruling) per query before handoff to the
    shared aggregator. `state.fused_buckets` stays populated for analytics
    but no longer feeds the reranker.
    """

    # Cap per-query candidates shown to the reranker LLM (mirrors the legacy
    # fused-bucket cap). Reranker is a binary classifier — 15 strong hits
    # per query is plenty and keeps context small enough to avoid the
    # output-truncation class of bugs we hit before.
    _TOP_N_PER_QUERY = 15

    async def run(
        self,
        ctx: GraphRunContext[LoopState, CaseSearchDeps],
    ) -> End[CaseSearchResult]:
        state = ctx.state
        deps = ctx.deps

        per_query = list(state.per_query_candidates)

        if not per_query:
            logger.warning("SectionedRerankerNode: no per-query candidates")
            return End(
                CaseSearchResult(
                    reranker_results=[],
                    queries_used=list(state.all_queries_used),
                    rounds_used=state.round_count,
                    expander_prompt_key=state.expander_prompt_key,
                )
            )

        # Truncate each query's list up-front so both the rendered markdown
        # AND the bucket we pass into `assemble_kept_cases` agree on what
        # position N points at — otherwise a kept decision on position 12
        # would dereference a case the reranker never saw.
        capped_per_query: list[tuple[TypedQuery, list[ChannelCandidate]]] = [
            (q, cands[: self._TOP_N_PER_QUERY]) for q, cands in per_query
        ]

        state.sse_events.append({
            "type": "status",
            "text": (
                f"جاري تصنيف {sum(len(c) for _, c in capped_per_query)} حكم "
                f"موزّعة على {len(capped_per_query)} استعلام (بالتوازي)..."
            ),
        })

        async def _process_one(
            qi: int,
            q: TypedQuery,
            cands: list[ChannelCandidate],
        ) -> tuple[RerankerQueryResult, list[dict], list[dict]]:
            """Rerank one query's own candidates + substitute full content."""
            if not cands:
                return (
                    RerankerQueryResult(
                        query=q.text,
                        rationale=f"[{q.channel}] {q.rationale}",
                        sufficient=False,
                        results=[],
                        dropped_count=0,
                        summary_note="لا توجد مرشحات لهذا الاستعلام",
                    ),
                    [],
                    [],
                )

            raw_markdown, _count = format_bucket_for_reranker(
                cands, bucket_label=f"q{qi}_{q.channel}",
            )

            try:
                reranker_result, usage_entries, decision_log = await run_reranker_for_query(
                    query=q.text,
                    rationale=f"[{q.channel}] {q.rationale}",
                    raw_markdown=raw_markdown,
                    model_override=state.model_override,
                )
            except Exception as e:
                logger.error(
                    "SectionedRerankerNode q%d [%s]: reranker call failed: %s",
                    qi, q.channel, e, exc_info=True,
                )
                return (
                    RerankerQueryResult(
                        query=q.text,
                        rationale=f"[{q.channel}] {q.rationale}",
                        sufficient=False,
                        results=[],
                        dropped_count=len(cands),
                        summary_note=f"خطأ في التصنيف: {str(e)[:100]}",
                    ),
                    [],
                    [],
                )

            # Per-query full-content substitution: wrap this query's
            # ChannelCandidates as FusedCandidate-shape so the shared
            # `assemble_kept_cases` API works unchanged.
            keep_decisions = [d for d in decision_log if d.get("action") == "keep"]
            if keep_decisions:
                try:
                    pseudo_bucket = wrap_as_fused(cands)
                    full_results = await assemble_kept_cases(
                        deps.supabase,
                        kept_decisions=keep_decisions,
                        fused_bucket=pseudo_bucket,
                    )
                    section_count = len(reranker_result.results)
                    if full_results:
                        logger.info(
                            "SectionedRerankerNode q%d [%s]: replaced %d "
                            "section-text results with %d full-content results",
                            qi, q.channel, section_count, len(full_results),
                        )
                        reranker_result.results = full_results
                    else:
                        logger.warning(
                            "SectionedRerankerNode q%d [%s]: assemble_kept_cases "
                            "returned 0 full-content results for %d kept decisions "
                            "— keeping %d section-text results as fallback",
                            qi, q.channel, len(keep_decisions), section_count,
                        )
                except Exception as e:
                    logger.error(
                        "SectionedRerankerNode q%d [%s]: full-content fetch failed: %s",
                        qi, q.channel, e, exc_info=True,
                    )
                    # Fall through with section-text results.

            return reranker_result, usage_entries, decision_log

        # Launch all per-query reranker tasks concurrently.
        tasks = [
            _process_one(qi, q, cands)
            for qi, (q, cands) in enumerate(capped_per_query, start=1)
        ]
        all_results = await asyncio.gather(*tasks)

        total_kept = 0
        total_dropped = 0
        for qi, (reranker_result, usage_entries, decision_log) in enumerate(all_results, 1):
            state.reranker_results.append(reranker_result)
            for ue in usage_entries:
                ue["round"] = state.round_count
                state.inner_usage.append(ue)
            total_kept += len(reranker_result.results)
            total_dropped += reranker_result.dropped_count

            if deps._log_id:
                try:
                    save_reranker_query_md(
                        log_id=deps._log_id,
                        query_index=qi,
                        query=reranker_result.query,
                        reranker_result=reranker_result,
                        decision_log=decision_log,
                    )
                except Exception as e:
                    logger.warning("Failed to save sectioned reranker MD: %s", e)

        logger.info(
            "SectionedRerankerNode: %d queries (per-query parallel) — %d kept, %d dropped",
            len(all_results), total_kept, total_dropped,
        )
        state.sse_events.append({
            "type": "status",
            "text": (
                f"اكتملت تصفية الأحكام (sectioned): {total_kept} محتفظ به، "
                f"{total_dropped} محذوف"
            ),
        })

        return End(
            CaseSearchResult(
                reranker_results=list(state.reranker_results),
                queries_used=list(state.all_queries_used),
                rounds_used=state.round_count,
                expander_prompt_key=state.expander_prompt_key,
            )
        )


# -- Graph assembly ------------------------------------------------------------


case_search_graph = Graph(nodes=[ExpanderNode, SearchNode, RerankerNode])
case_search_sectioned_graph = Graph(
    nodes=[
        SectionedExpanderNode,
        SectionedSearchNode,
        FusionNode,
        SectionedRerankerNode,
    ],
)


async def run_case_search(
    focus_instruction: str,
    user_context: str,
    deps: CaseSearchDeps,
    expander_prompt_key: str = DEFAULT_EXPANDER_PROMPT,
    thinking_effort: str | None = None,
    model_override: str | None = None,
    concurrency: int = 10,
    sectioned: bool | None = None,
) -> CaseSearchResult:
    """Run the case_search loop for a focus instruction.

    Dispatches to the sectioned pipeline when `sectioned=True` or when the
    prompt key is registered as sectioned (prompt_3+). Otherwise runs the
    legacy hybrid-search path.

    Args:
        focus_instruction: Arabic instruction — what to search for.
        user_context: Arabic context — user's situation/question.
        deps: CaseSearchDeps with supabase, embedding_fn, etc.
        expander_prompt_key: Which expander prompt variant to use.
        thinking_effort: Reasoning effort for the expander agent.
        model_override: Registry key to override all agent models.
        concurrency: Max concurrent search pipelines.
        sectioned: Force sectioned pipeline regardless of prompt key.
            Default (None) routes by `is_sectioned_prompt(expander_prompt_key)`.

    Returns:
        CaseSearchResult with reranker_results for the shared aggregator.
    """
    import time

    from .logger import create_run_dir, make_log_id

    use_sectioned = sectioned if sectioned is not None else is_sectioned_prompt(expander_prompt_key)

    logger.info(
        "run_case_search: focus='%s', expander_prompt=%s, sectioned=%s",
        focus_instruction[:80],
        expander_prompt_key,
        use_sectioned,
    )

    log_id = (
        make_log_id(deps._query_id)
        if deps._query_id
        else datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    )
    deps._log_id = log_id
    create_run_dir(log_id)

    state = LoopState(
        focus_instruction=focus_instruction,
        user_context=user_context,
        expander_prompt_key=expander_prompt_key,
        thinking_effort=thinking_effort,
        model_override=model_override,
        concurrency=concurrency,
    )

    t0 = time.perf_counter()
    error_msg: str | None = None

    try:
        if use_sectioned:
            graph_result = await case_search_sectioned_graph.run(
                SectionedExpanderNode(),
                state=state,
                deps=deps,
            )
        else:
            graph_result = await case_search_graph.run(
                ExpanderNode(),
                state=state,
                deps=deps,
            )

        deps._events.extend(state.sse_events)
        output = graph_result.output

        logger.info(
            "run_case_search complete: sectioned=%s, rounds=%d, queries=%d, reranker_results=%d",
            use_sectioned,
            output.rounds_used,
            len(output.queries_used),
            len(output.reranker_results),
        )

    except Exception as e:
        logger.error("run_case_search failed: %s", e, exc_info=True)
        error_msg = str(e)
        deps._events.extend(state.sse_events)
        deps._events.append({
            "type": "status",
            "text": "حدث خطأ أثناء حلقة البحث في السوابق القضائية.",
        })

        output = CaseSearchResult(
            reranker_results=[],
            queries_used=list(state.all_queries_used),
            rounds_used=state.round_count,
            expander_prompt_key=expander_prompt_key,
        )

    duration = time.perf_counter() - t0
    round_summaries = _build_round_summaries(state)

    try:
        save_run_overview_md(
            log_id=log_id,
            focus_instruction=focus_instruction,
            user_context=user_context,
            expander_prompt_key=expander_prompt_key,
            duration_s=duration,
            result=output,
            round_summaries=round_summaries,
        )
    except Exception as e:
        logger.warning("Failed to save run overview: %s", e)

    try:
        save_run_json(
            log_id=log_id,
            focus_instruction=focus_instruction,
            user_context=user_context,
            expander_prompt_key=expander_prompt_key,
            duration_s=duration,
            result=output,
            events=list(deps._events),
            round_summaries=round_summaries,
            search_results_log=list(state.search_results_log),
            inner_usage=list(state.inner_usage),
            error=error_msg,
            query_id=deps._query_id,
            model_name=_get_expander_model_id(),
            thinking_effort=thinking_effort,
        )
    except Exception as e:
        logger.warning("Failed to save run JSON: %s", e)

    return output


async def run_sectioned_case_search(
    focus_instruction: str,
    user_context: str,
    deps: CaseSearchDeps,
    expander_prompt_key: str = "prompt_3",
    thinking_effort: str | None = None,
    model_override: str | None = None,
    concurrency: int = 10,
) -> CaseSearchResult:
    """Convenience entry point that forces the sectioned pipeline.

    Thin wrapper over `run_case_search(..., sectioned=True)`. Defaults the
    prompt key to `prompt_3` but any sectioned prompt works.
    """
    return await run_case_search(
        focus_instruction=focus_instruction,
        user_context=user_context,
        deps=deps,
        expander_prompt_key=expander_prompt_key,
        thinking_effort=thinking_effort,
        model_override=model_override,
        concurrency=concurrency,
        sectioned=True,
    )


def _build_round_summaries(state: LoopState) -> list[dict]:
    """Build per-round summary dicts from state for logging."""
    summaries: list[dict] = []
    search_by_round: dict[int, list] = {}
    for sr in state.search_results_log:
        rn = sr.get("round", 0)
        search_by_round.setdefault(rn, []).append(sr)

    for rn in range(1, state.round_count + 1):
        summary: dict = {"round": rn}

        exp_usage = [u for u in state.inner_usage if u.get("agent") == "expander" and u.get("round") == rn]
        rer_usage = [u for u in state.inner_usage if u.get("agent") == "reranker" and u.get("round") == rn]

        round_searches = search_by_round.get(rn, [])
        if round_searches:
            summary["expander_queries"] = [s["query"] for s in round_searches]
            summary["search_queries"] = len(round_searches)
            summary["search_total"] = sum(s.get("result_count", 0) for s in round_searches)

        if exp_usage:
            summary["expander_usage"] = exp_usage[0]

        if rer_usage:
            # Aggregate across ALL reranker calls in the round (there is one
            # per typed query). Previously this stored rer_usage[0] only,
            # making subsequent calls invisible in run.json.
            summary["reranker_usage"] = {
                "requests": sum(u.get("requests", 0) for u in rer_usage),
                "input_tokens": sum(u.get("input_tokens", 0) for u in rer_usage),
                "output_tokens": sum(u.get("output_tokens", 0) for u in rer_usage),
                "total_tokens": sum(u.get("total_tokens", 0) for u in rer_usage),
                "call_count": len(rer_usage),
            }
            # Kept for backwards-compat with consumers that read this field directly.
            summary["reranker_total_tokens"] = sum(u.get("total_tokens", 0) for u in rer_usage)

        if rn == state.round_count and state.reranker_results:
            summary["reranker_kept"] = sum(len(r.results) for r in state.reranker_results)
            summary["reranker_dropped"] = sum(r.dropped_count for r in state.reranker_results)
            summary["reranker_queries"] = len(state.reranker_results)
            summary["reranker_sufficient"] = [r.sufficient for r in state.reranker_results]

        summaries.append(summary)

    return summaries
