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
import math
from datetime import datetime, timezone
from typing import Union

from pydantic_graph import BaseNode, End, Graph, GraphRunContext

from agents.deep_search_v4.shared import DEFAULT_SEARCH_CONCURRENCY
from agents.deep_search_v4.shared.context import ContextBlock

# Divisor floor for the dynamic result-budget model (MODE_PROFILES.md §1).
# When the planner passes a ``result_budget``, the per-sub-query reranker keep
# is ceil(result_budget / max(N, MIN_EXPANDER_DIVISOR)) where N is the
# expander's actual emitted query count.
MIN_EXPANDER_DIVISOR = 3

# Hard ceiling on kept cases per sub-query — **total**, high + medium together.
# NOT a per-tier cap.
#
# Set to 7 (was an effective 10 fixed / up to 10 dynamic) for two reasons:
#   1. The tier-split caps downstream (ura/merger.py) starved `medium`: 12 high
#      but only 4 medium, so the cross-forum `medium` keeps that decision D4 and
#      the cross-forum rule exist to surface were silently truncated while high
#      slots went unused. A single total cap removes the asymmetry.
#   2. Measured saturation: one `principle` sub-query returned 14 keeps, all
#      `high`, from 13 near-verbatim copies of ONE holding. Volume was not
#      buying coverage.
#
# This clamps the planner's dynamic budget too. `case_led` mode with 6
# sub-queries would otherwise compute ceil(60/6)=10, keep 10, and have the
# merger discard 3 AFTER the tokens were already spent — the cap has to bind at
# the reranker, not after it.
MAX_KEEP_PER_SUBQUERY = 7


def _resolve_reranker_max_keep(deps: "CaseSearchDeps", n_queries: int) -> int:
    """Per-sub-query keep (total, high+medium): dynamic budget or fixed fallback,
    clamped to :data:`MAX_KEEP_PER_SUBQUERY`.

    When ``deps.result_budget`` is set (planner / orchestrator path), derive
    the keep from the expander's ACTUAL emitted query count ``n_queries``.
    When it is None (CLI path), use the fixed ``deps.reranker_max_keep``.
    Either way the result is clamped — the ceiling is a hard product decision,
    not a per-mode knob.
    """
    if deps.result_budget is None:
        return min(deps.reranker_max_keep, MAX_KEEP_PER_SUBQUERY)
    keep = math.ceil(deps.result_budget / max(n_queries, MIN_EXPANDER_DIVISOR))
    clamped = min(keep, MAX_KEEP_PER_SUBQUERY)
    logger.info(
        "case_search dynamic keep — result_budget=%d, N=%d -> max_keep=%d%s",
        deps.result_budget, n_queries, clamped,
        f" (clamped from {keep})" if clamped != keep else "",
    )
    return clamped

from .unfold_ura import assemble_kept_cases
from .unfold_reranker import (
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

logger = logging.getLogger(__name__)


# -- Channel merge helpers (sectioned path) ------------------------------------


def _topic_key(topic: dict) -> object:
    """Stable identity for a matched topic — `topic_ref`, else index+text."""
    ref = str(topic.get("topic_ref") or "").strip()
    if ref:
        return ref
    return (topic.get("topic_index"), str(topic.get("text") or "").strip())


def union_topics(*topic_lists: list[dict]) -> list[dict]:
    """Union matched-topic lists, deduped by topic identity, score-desc.

    Two sub-queries on the same channel can surface the same case via
    DIFFERENT topics. The channel merge keeps one candidate per case, so the
    topic lists must be unioned — overwriting would silently shrink the
    reranker payload (decision D1). On a duplicate topic the higher score wins.
    """
    merged: dict[object, dict] = {}
    for topics in topic_lists:
        for topic in topics or []:
            key = _topic_key(topic)
            prev = merged.get(key)
            if prev is None or float(topic.get("score") or 0.0) > float(
                prev.get("score") or 0.0
            ):
                merged[key] = topic
    return sorted(
        merged.values(),
        key=lambda t: float(t.get("score") or 0.0),
        reverse=True,
    )


def merge_channel_candidates(
    queries: list["TypedQuery"],
    per_query_candidates: list[list["ChannelCandidate"]],
) -> dict[str, list["ChannelCandidate"]]:
    """Merge per-sub-query candidate lists into per-channel ranked lists.

    Best-rank-wins for the case's rank/row (a case that placed #1 for any
    sub-query keeps rank 1), max score, and a UNION of the matched topics.
    Ranks are renumbered 1..N per channel after merging.

    A NEW ChannelCandidate is built for every merge — the per-sub-query lists
    stay untouched because they are what each reranker call actually sees, and
    their topic sets must stay query-scoped.
    """
    by_channel: dict[str, dict[str, "ChannelCandidate"]] = {}
    for q, cands in zip(queries, per_query_candidates):
        bucket = by_channel.setdefault(q.channel, {})
        for c in cands:
            existing = bucket.get(c.case_id)
            if existing is None:
                bucket[c.case_id] = c
                continue
            winner = c if c.rank < existing.rank else existing
            bucket[c.case_id] = ChannelCandidate(
                case_id=winner.case_id,
                channel=winner.channel,
                rank=winner.rank,
                score=max(existing.score, c.score),
                row=winner.row or existing.row or c.row,
                topics=union_topics(existing.topics, c.topics),
            )

    channel_candidates: dict[str, list["ChannelCandidate"]] = {}
    for channel, by_case in by_channel.items():
        merged = sorted(by_case.values(), key=lambda c: c.rank)
        channel_candidates[channel] = [
            ChannelCandidate(
                case_id=c.case_id,
                channel=c.channel,
                rank=i + 1,
                score=c.score,
                row=c.row,
                topics=list(c.topics),
            )
            for i, c in enumerate(merged)
        ]
    return channel_candidates


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
            model_override=state.model_override,
        )

        user_message = build_expander_user_message(
            state.focus_instruction,
            state.user_context,
            context_blocks=state.context_blocks,
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
                "cached_tokens": int(getattr(eu, "cache_read_tokens", 0) or 0),
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

        # Live progress — the reranker IS the "تقييم وترجيح" stage. See the twin
        # emit in reg_search/loop.py RerankerNode. Guarded; peer executors emit
        # the same stage and the client keeps it monotonic.
        if deps.emit_sse is not None:
            try:
                deps.emit_sse({
                    "type": "agent_progress",
                    "stage": "evaluating",
                    "text": "تقييم النتائج وترجيحها",
                    "data": {},
                })
            except Exception:  # pragma: no cover - defensive
                logger.debug("RerankerNode: emit_sse failed", exc_info=True)

        # Dynamic result-budget model (MODE_PROFILES.md §1): per-sub-query keep
        # is derived from the expander's actual query count N when the planner
        # passed a ``result_budget``; otherwise the fixed fallback is used.
        reranker_max_keep = _resolve_reranker_max_keep(deps, len(current_round_logs))

        # Run per-query reranking concurrently (each gets its own trace list)
        per_query_traces: list[list[dict]] = [[] for _ in current_round_logs]
        tasks = [
            run_reranker_for_query(
                query=sr["query"],
                rationale=sr.get("rationale", ""),
                raw_markdown=sr.get("raw_markdown", ""),
                model_override=state.model_override,
                round_trace=per_query_traces[i],
                max_keep=reranker_max_keep,
            )
            for i, sr in enumerate(current_round_logs)
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
            reranker_result._round_trace = per_query_traces[qi - 1]  # type: ignore[attr-defined]
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
            model_override=state.model_override,
        )
        user_message = build_expander_user_message(
            state.focus_instruction,
            state.user_context,
            context_blocks=state.context_blocks,
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
                "cached_tokens": int(getattr(eu, "cache_read_tokens", 0) or 0),
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

            logger.info(
                "SectionedExpanderNode: %d queries, by_channel=%s",
                len(output.queries),
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

        # Sector filter: CLI ``--sectors`` experiment hatch only. The case path
        # does NOT consume ``sector_picker`` anymore (decision D3 / plan §§1.2,
        # 9) — filtering on ``legal_domains`` dropped the whole untagged batch,
        # i.e. exactly the cases this retarget recovers, for ~no selectivity.
        # There is no picker future to await here, which also takes the bounded
        # picker grace off the case critical path.
        sectors: list[str] | None = (
            list(state.sectors_override) if state.sectors_override else None
        )

        logger.info(
            "SectionedSearchNode: %d queries, sectors_source=%s, concurrency=%d",
            len(queries),
            "cli_override" if sectors else "none",
            state.concurrency,
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

        # Merge per-sub-query lists into per-channel ranked lists (analytics /
        # fusion input): best-rank-wins per case, with the matched-topic lists
        # UNIONed rather than overwritten (D1).
        channel_candidates = merge_channel_candidates(queries, per_query_candidates)
        state.channel_candidates = channel_candidates

        # No enrichment hop: the `search_case_topics` RPC joins the case header
        # onto every topic row, so each candidate already carries court / city /
        # court_level / case_number / date_hijri / short_summary AND its matched
        # topics. The per-sub-query lists therefore ARE the reranker payload,
        # each with its own query-scoped rank and its own topic set (mirroring
        # reg_search's per-query reranker pattern — no cross-query blending).
        state.per_query_candidates = list(zip(queries, per_query_candidates))

        # Record per-query search results for logging parity with legacy path
        for qi, (q, cands) in enumerate(zip(queries, per_query_candidates), start=1):
            # Same cap the reranker sees, so the forensic dump matches its input.
            display = cands[: SectionedRerankerNode._TOP_N_PER_QUERY]

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

        # Live progress — sectioned path's twin of RerankerNode's emit above.
        if deps.emit_sse is not None:
            try:
                deps.emit_sse({
                    "type": "agent_progress",
                    "stage": "evaluating",
                    "text": "تقييم النتائج وترجيحها",
                    "data": {},
                })
            except Exception:  # pragma: no cover - defensive
                logger.debug("SectionedRerankerNode: emit_sse failed", exc_info=True)

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

        # Dynamic result-budget model (MODE_PROFILES.md §1): per-sub-query keep
        # is derived from the expander's actual typed-query count N when the
        # planner passed a ``result_budget``; otherwise the fixed fallback.
        reranker_max_keep = _resolve_reranker_max_keep(deps, len(capped_per_query))

        state.sse_events.append({
            "type": "status",
            "text": (
                f"جاري تصنيف {sum(len(c) for _, c in capped_per_query)} حكم "
                f"موزّعة على {len(capped_per_query)} استعلام (بالتوازي)..."
            ),
        })

        # Wave 3 (plan §7 / decision D6): the reranker now receives the
        # planner's distilled brief. ONLY the ``planner_brief`` label — never
        # ``case_brief`` (raw case memory) or ``prior_search_lessons``; the
        # reranker grades one sub-query against candidates and must not be
        # handed the whole context bundle.
        #
        # Hoisted OUT of ``_process_one`` deliberately: every one of the N
        # concurrent reranker calls in this round carries the identical brief,
        # and it is rendered at the HEAD of the user message so the shared
        # prefix stays cacheable (see build_reranker_user_message). Resolving it
        # per task would be pure repeat work.
        planner_brief = next(
            (
                b.body
                for b in (state.context_blocks or [])
                if b.label == "planner_brief" and (b.body or "").strip()
            ),
            None,
        )

        logger.info(
            "SectionedRerankerNode: launching %d parallel reranker tasks "
            "(planner_brief=%s)",
            len(capped_per_query),
            planner_brief is not None,
        )

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
                _rt: list[dict] = []
                reranker_result, usage_entries, decision_log = await run_reranker_for_query(
                    query=q.text,
                    rationale=f"[{q.channel}] {q.rationale}",
                    raw_markdown=raw_markdown,
                    model_override=state.model_override,
                    max_keep=reranker_max_keep,
                    round_trace=_rt,
                    planner_brief=planner_brief,
                )
                reranker_result._round_trace = _rt  # type: ignore[attr-defined]
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

            # Re-apply the keep cap to the DECISION LOG before rebuilding.
            #
            # `run_reranker_for_query` already truncated `reranker_result.results`
            # to `max_keep`, but `decision_log` is the raw, UNCAPPED list of the
            # LLM's keeps — and the substitution below overwrites `.results`
            # wholesale from it. Without this, the cap is silently undone:
            # measured on R-INS-05, one sub-query emitted 9 keeps against a
            # ceiling of 7 and all 9 reached the aggregator.
            #
            # Ordering mirrors reranker.py exactly (high before medium, then
            # retrieval score desc) so the same results survive here as there.
            if len(keep_decisions) > reranker_max_keep:
                score_by_pos = {i + 1: c.score for i, c in enumerate(cands)}
                keep_decisions.sort(
                    key=lambda d: (
                        (d.get("relevance") or "") != "high",
                        -score_by_pos.get(int(d.get("position", 0) or 0), 0.0),
                    )
                )
                logger.info(
                    "SectionedRerankerNode q%d [%s]: cap truncated keep_decisions "
                    "%d -> %d (max_keep=%d)",
                    qi, q.channel, len(keep_decisions), reranker_max_keep,
                    reranker_max_keep,
                )
                keep_decisions = keep_decisions[:reranker_max_keep]

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

            # Forensic drop reconstruction (best-effort). The markdown-based
            # reranker is blind to cases.id, so we rebuild the dropped list from
            # decision_log + cands here, where ChannelCandidate.case_id IS the
            # cases.id UUID. Positions in decision_log are 1-based indices into
            # `cands` (format_bucket_for_reranker numbers candidates 1..N), so
            # candidate for position p is cands[p-1] when 1 <= p <= len(cands).
            try:
                def _cand_title(row: dict) -> str:
                    title = " | ".join(
                        x for x in (
                            row.get("court", "") or "",
                            row.get("case_number", "") or "",
                            row.get("date_hijri", "") or "",
                        ) if x
                    )
                    return title or (row.get("case_ref", "") or "")

                def _cand_topic_ref(candidate: ChannelCandidate) -> str:
                    """`topic_ref` of the TOP matched topic (topics are score-desc).

                    Lets @reranker-run-judge see WHAT matched on a dropped
                    case, not just which case was dropped (plan §6.3).
                    """
                    topics = getattr(candidate, "topics", None) or []
                    if not topics:
                        return ""
                    return str(topics[0].get("topic_ref") or "")

                # UUIDs of cases that actually survived into the final results.
                survived: set[str] = set()
                for res in reranker_result.results:
                    uuid = (getattr(res, "db_uuid", "") or "").strip()
                    if not uuid:
                        uuid = (getattr(res, "db_id", "") or "").strip()
                    if uuid:
                        survived.add(uuid)

                dropped: list[dict] = []
                seen_refs: set[str] = set()
                for entry in decision_log:
                    pos = int(entry.get("position", 0) or 0)
                    if not (1 <= pos <= len(cands)):
                        continue
                    cand = cands[pos - 1]
                    ref_id = (cand.case_id or "").strip()
                    if not ref_id or ref_id in seen_refs:
                        continue
                    action = entry.get("action")
                    title = _cand_title(cand.row or {})
                    topic_ref = _cand_topic_ref(cand)
                    if action in ("drop", "undecided"):
                        dropped.append({
                            "source_table": "cases",
                            "ref_id": ref_id,
                            "title": title,
                            "drop_reason": "llm",
                            "reasoning": entry.get("reasoning", "") or "",
                            "source_type": "case",
                            "topic_ref": topic_ref,
                        })
                        seen_refs.add(ref_id)
                    elif action == "keep" and survived:
                        # Kept by the LLM but didn't survive → cap-truncated.
                        # Guarded on `survived`: in the fallback path (full-content
                        # substitution failed) the section-text results carry no
                        # id, so we can't tell survivors from cap-drops — skip
                        # cap detection entirely rather than mislabel every keep.
                        case_ref = (cand.row or {}).get("case_ref", "") or ""
                        if ref_id in survived or (case_ref and case_ref in survived):
                            continue
                        dropped.append({
                            "source_table": "cases",
                            "ref_id": ref_id,
                            "title": title,
                            "drop_reason": "cap",
                            "reasoning": "",
                            "source_type": "case",
                            "topic_ref": topic_ref,
                        })
                        seen_refs.add(ref_id)

                reranker_result.dropped_results = dropped
            except Exception as e:
                logger.warning(
                    "SectionedRerankerNode q%d [%s]: dropped-forensic "
                    "reconstruction failed: %s",
                    qi, q.channel, e,
                )

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
    model_override: str | None = None,
    concurrency: int = DEFAULT_SEARCH_CONCURRENCY,
    sectioned: bool | None = None,
    sectors_override: list[str] | None = None,
    score_threshold: float | None = None,
    context_blocks: list[ContextBlock] | None = None,
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
        model_override: Registry key to override all agent models.
        concurrency: Max concurrent search pipelines.
        sectioned: Force sectioned pipeline regardless of prompt key.
            Default (None) routes by `is_sectioned_prompt(expander_prompt_key)`.
        sectors_override: CLI-only sector experiment hatch. There is no
            ``sectors_future`` parameter anymore — the case path stopped
            consuming ``sector_picker`` (decision D3 / plan §9), so nothing
            populates this on the production path.

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
        model_override=model_override,
        concurrency=concurrency,
        sectors_override=list(sectors_override) if sectors_override else None,
        context_blocks=list(context_blocks) if context_blocks else [],
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
        )
    except Exception as e:
        logger.warning("Failed to save run JSON: %s", e)

    # Surface per-LLM-call usage to the orchestrator. Without this the
    # phase wrapper can only record wall time — token totals come out 0.
    output.inner_usage = list(state.inner_usage)

    return output


async def run_sectioned_case_search(
    focus_instruction: str,
    user_context: str,
    deps: CaseSearchDeps,
    expander_prompt_key: str = "prompt_3",
    model_override: str | None = None,
    concurrency: int = DEFAULT_SEARCH_CONCURRENCY,
    context_blocks: list[ContextBlock] | None = None,
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
        model_override=model_override,
        concurrency=concurrency,
        sectioned=True,
        context_blocks=context_blocks,
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
                "cached_tokens": sum(u.get("cached_tokens", 0) for u in rer_usage),
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
