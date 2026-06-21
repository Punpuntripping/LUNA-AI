"""Graph nodes and entry point for the compliance_search loop.

Three nodes forming the expand-search-rerank loop:
- ExpanderNode: LLM query expansion (QueryExpander agent)
- SearchNode: Programmatic search execution, dedup by service_ref (no LLM)
- RerankerNode: LLM result classification — keep/drop, sufficiency gate, retry control

Retry loop: when RerankerNode returns sufficient=False and retries remain,
weak_axes are fed back to ExpanderNode as dynamic instructions.
Max 1 round (single pass, no retries).
"""
from __future__ import annotations

import asyncio
import logging
import math
import time as _time
from datetime import datetime, timezone
from typing import Union

from pydantic_graph import BaseNode, End, Graph, GraphRunContext

from agents.deep_search_v4.shared.context import ContextBlock

# Divisor floor for the dynamic result-budget model (MODE_PROFILES.md §1).
# When the planner passes a ``result_budget``, the keep is
# ceil(result_budget / max(N, MIN_EXPANDER_DIVISOR)) where N is the
# expander's actual emitted query count.
MIN_EXPANDER_DIVISOR = 3

from .expander import EXPANDER_LIMITS, create_expander_agent
from .logger import (
    save_expander_md,
    save_reranker_md,
    save_search_query_md,
)
from .models import (
    ComplianceSearchDeps,
    ComplianceSearchResult,
    ExpanderOutput,
    LoopState,
    RerankedServiceResult,
    ServiceRerankerOutput,
    WeakAxis,
)
from .prompts import (
    EXPANDER_SYSTEM_PROMPT,
    build_expander_dynamic_instructions,
    build_expander_user_message,
)
from .reranker import run_reranker_for_query
from .search import search_compliance_raw

logger = logging.getLogger(__name__)

MAX_ROUNDS = 1


# -- ExpanderNode --------------------------------------------------------------


class ExpanderNode(BaseNode[LoopState, ComplianceSearchDeps, ComplianceSearchResult]):
    """Runs QueryExpander agent with structured output.

    Identifies distinct compliance tasks in the focus_instruction and generates
    one query per independent need (1–5 queries). On round 2+, injects weak_axes
    as dynamic instructions targeting only the identified gaps.
    Always transitions to SearchNode.
    """

    async def run(
        self,
        ctx: GraphRunContext[LoopState, ComplianceSearchDeps],
    ) -> SearchNode:
        state = ctx.state
        state.round_count += 1

        logger.info(
            "ExpanderNode round %d -- focus: %s",
            state.round_count,
            state.focus_instruction[:80],
        )

        # Create expander agent, with weak_axes on round 2+
        weak_axes = state.weak_axes if state.round_count > 1 else None
        expander = create_expander_agent(weak_axes=weak_axes)

        # Build user message via the shared helper — focus + user_context +
        # planner-curated <context_blocks> XML (§5.1.C). The reranker still
        # receives zero blocks; this expander surface is the only consumer.
        user_message = build_expander_user_message(
            state.focus_instruction,
            state.user_context,
            context_blocks=state.context_blocks,
        )

        # On round 2+, append dynamic instructions for weak axes
        if state.round_count > 1 and state.weak_axes:
            dynamic_instructions = build_expander_dynamic_instructions(state.weak_axes)
            if dynamic_instructions:
                user_message = f"{user_message}\n\n{dynamic_instructions}"

        try:
            result = await expander.run(
                user_message,
                usage_limits=EXPANDER_LIMITS,
            )
            output: ExpanderOutput = result.output

            # Capture usage
            eu = result.usage()
            usage_dict = {
                "agent": "expander",
                "round": state.round_count,
                "requests": eu.requests,
                "input_tokens": eu.input_tokens,
                "output_tokens": eu.output_tokens,
                "total_tokens": eu.total_tokens,
                "cached_tokens": int(getattr(eu, "cache_read_tokens", 0) or 0),
            }
            state.inner_usage.append(usage_dict)

            # Store in state
            state.expander_output = output
            state.queries_used.extend(output.queries)

            task_count = getattr(output, "task_count", len(output.queries))
            state.sse_events.append({
                "type": "status",
                "text": (
                    f"تم توليد {len(output.queries)} استعلامات بحث "
                    f"({task_count} احتياج تنفيذي — الجولة {state.round_count})"
                ),
            })

            logger.info(
                "ExpanderNode: %d queries, task_count=%d -- %s",
                len(output.queries),
                task_count,
                ", ".join(q[:40] for q in output.queries),
            )

            # Log to file
            if state.log_id:
                try:
                    save_expander_md(
                        log_id=state.log_id,
                        round_num=state.round_count,
                        system_prompt=EXPANDER_SYSTEM_PROMPT,
                        user_message=user_message,
                        output=output,
                        usage=usage_dict,
                        messages_json=result.all_messages_json(),
                    )
                except Exception as le:
                    logger.warning("save_expander_md failed: %s", le)

        except Exception as e:
            logger.error("ExpanderNode error: %s", e, exc_info=True)
            state.sse_events.append({
                "type": "status",
                "text": "حدث خطأ أثناء توسيع الاستعلامات.",
            })
            # Fallback: use focus_instruction as a single query
            state.expander_output = ExpanderOutput(
                queries=[state.focus_instruction],
                rationales=["Fallback: expander failed"],
                task_count=1,
            )
            state.queries_used.append(state.focus_instruction)

        return SearchNode()


# -- SearchNode ----------------------------------------------------------------


class SearchNode(BaseNode[LoopState, ComplianceSearchDeps, ComplianceSearchResult]):
    """Programmatic search -- no LLM. Runs queries via asyncio.gather.

    Reads state.expander_output.queries, executes them concurrently via
    search_compliance_raw, deduplicates by service_ref (keeping highest score),
    and merges new unique results into state.all_results_flat.
    Always transitions to RerankerNode.
    """

    async def run(
        self,
        ctx: GraphRunContext[LoopState, ComplianceSearchDeps],
    ) -> RerankerNode:
        state = ctx.state
        deps = ctx.deps

        queries = state.expander_output.queries if state.expander_output else []
        if not queries:
            logger.warning("SearchNode: no queries to execute")
            return RerankerNode()

        logger.info("SearchNode: executing %d queries concurrently", len(queries))
        # services.sectors[] and regulations_v2.sectors[] now share the unified
        # 38-entry vocabulary (verified 2026-05-17 in shared.sector_vocab.unified
        # docstring). The historical "log but don't apply" path for the static
        # sectors_override stays — it preserved back-compat with legacy callers
        # — but the picker future (which emits canonical names from VALID_SECTORS)
        # is applied at the RPC layer via search_compliance_raw's sectors_future
        # argument below.
        if state.sectors_override:
            logger.info(
                "compliance.search: sectors_override=%s — not applied as a "
                "DB filter (legacy back-compat path)",
                state.sectors_override,
            )
        if state.sectors_future is not None:
            logger.info(
                "compliance.search: sectors_future present — picker output will "
                "be applied as filter_sectors at the RPC layer",
            )
        state.sse_events.append({
            "type": "status",
            "text": f"جاري تنفيذ {len(queries)} استعلامات بحث...",
        })

        # Batch-embed all queries in one API call (parity with case_search /
        # reg_search). Auto-splits at MAX_EMBED_BATCH inside the helper, so a
        # round that emits >25 queries still costs 1 call per 25-query chunk
        # instead of N per-query calls.
        from agents.utils.embeddings import embed_regulation_queries_alibaba

        try:
            embeddings = await embed_regulation_queries_alibaba(queries)
        except Exception as e:
            logger.error(
                "compliance.SearchNode: batch embed failed (%d queries): %s",
                len(queries), e, exc_info=True,
            )
            # Degrade gracefully — fall back to per-query embedding via the
            # injected ``deps.embedding_fn``. Keeps the round alive at the
            # cost of N API calls.
            embeddings = [None] * len(queries)  # type: ignore[list-item]

        sem = asyncio.Semaphore(state.concurrency)
        tasks = [
            search_compliance_raw(
                q,
                deps,
                precomputed_embedding=emb,
                semaphore=sem,
                sectors_future=state.sectors_future,
            )
            for q, emb in zip(queries, embeddings)
        ]
        results_per_query: list[list[dict]] = await asyncio.gather(*tasks)

        # Capture per-query service_ref attribution BEFORE dedup so the
        # orchestrator can hand it to compliance_to_rqr (Option A per Q1
        # in the Loop V2 plan). Accumulates across rounds — on retry the
        # fresh queries append to any lists built in earlier rounds.
        for q, rows in zip(queries, results_per_query):
            refs = state.per_query_service_refs.setdefault(q, [])
            for row in rows:
                sref = row.get("service_ref", "") or ""
                if sref:
                    refs.append(sref)

        # Log per-query results
        for qi, (query, results) in enumerate(zip(queries, results_per_query), 1):
            state.search_results_log.append({
                "round": state.round_count,
                "query": query,
                "result_count": len(results),
            })
            if state.log_id:
                try:
                    rationale = (
                        state.expander_output.rationales[qi - 1]
                        if state.expander_output and qi <= len(state.expander_output.rationales)
                        else ""
                    )
                    save_search_query_md(
                        log_id=state.log_id,
                        round_num=state.round_count,
                        query_index=qi,
                        query=query,
                        results=results,
                        rationale=rationale,
                    )
                except Exception as le:
                    logger.warning("save_search_query_md failed: %s", le)

        # Retain per-sub-query rows so RerankerNode can issue ONE reranker call
        # per sub-query (parity with reg_search / case_search). Dedup WITHIN the
        # query by service_ref (keep highest score) so a single sub-query never
        # shows the LLM the same service twice; cross-query overlap is resolved
        # after the per-query gather, in the node.
        for qi, (query, results) in enumerate(zip(queries, results_per_query)):
            within: dict[str, dict] = {}
            for row in results:
                ref = row.get("service_ref", "") or ""
                if not ref:
                    continue
                existing = within.get(ref)
                if existing is None or row.get("score", 0.0) > existing.get("score", 0.0):
                    within[ref] = row
            rows_sorted = sorted(
                within.values(), key=lambda r: r.get("score", 0.0), reverse=True
            )
            rationale = (
                state.expander_output.rationales[qi]
                if state.expander_output and qi < len(state.expander_output.rationales)
                else ""
            )
            state.per_query_rows.append({
                "round": state.round_count,
                "query": query,
                "rationale": rationale,
                "rows": rows_sorted,
            })

        # Dedup by service_ref within this batch — keep row with highest score
        service_map: dict[str, dict] = {}
        for results in results_per_query:
            for row in results:
                ref = row.get("service_ref", "")
                if not ref:
                    continue
                existing = service_map.get(ref)
                if existing is None or row.get("score", 0.0) > existing.get("score", 0.0):
                    service_map[ref] = row

        # Merge new unique results into all_results_flat (accumulated across rounds)
        existing_refs = {r.get("service_ref") for r in state.all_results_flat}
        new_rows = [r for ref, r in service_map.items() if ref not in existing_refs]
        state.all_results_flat.extend(new_rows)

        # Sort flat list by score DESC
        state.all_results_flat.sort(key=lambda r: r.get("score", 0.0), reverse=True)

        total = len(state.all_results_flat)
        logger.info(
            "SearchNode: %d new unique services added, %d total unique services",
            len(new_rows),
            total,
        )
        state.sse_events.append({
            "type": "status",
            "text": f"تم استرجاع {total} خدمة حكومية فريدة.",
        })

        return RerankerNode()


# -- RerankerNode --------------------------------------------------------------


class RerankerNode(BaseNode[LoopState, ComplianceSearchDeps, ComplianceSearchResult]):
    """Runs the ServiceReranker ONCE PER EXPANDER SUB-QUERY, in parallel.

    Mirrors reg_search / case_search: each sub-query's retrieved pool
    (``state.per_query_rows``) is reranked by its own ``run_reranker_for_query``
    call, all launched concurrently via ``asyncio.gather``. After the gather the
    node merges every sub-query's kept services — deduping by ``service_ref``
    (high relevance beats medium, ties by RRF score), applies the global keep
    cap, and aggregates the per-query ``sufficient`` / ``weak_axes`` signals.

    The external contract is unchanged: ``state.kept_results`` is the same typed
    ``RerankedServiceResult`` list the orchestrator + URA adapter consume.

    Routes to ExpanderNode (loop back) or End(ComplianceSearchResult).
    """

    async def run(
        self,
        ctx: GraphRunContext[LoopState, ComplianceSearchDeps],
    ) -> Union[ExpanderNode, End[ComplianceSearchResult]]:
        state = ctx.state
        deps = ctx.deps

        # Per-sub-query rows captured by SearchNode for the current round.
        current_round = [
            e for e in state.per_query_rows if e.get("round") == state.round_count
        ]

        logger.info(
            "RerankerNode round %d -- %d sub-queries (1 reranker call each), "
            "%d unique services in pool",
            state.round_count, len(current_round), len(state.all_results_flat),
        )

        if not current_round or not state.all_results_flat:
            # Nothing to classify — exit with weak quality
            logger.warning("RerankerNode: no results to classify, exiting with weak quality")
            state.sse_events.append({
                "type": "status",
                "text": "لم يتم العثور على خدمات حكومية ذات صلة.",
            })
            return End(ComplianceSearchResult(
                kept_results=list(state.kept_results),
                quality="weak",
                queries_used=list(state.queries_used),
                rounds_used=state.round_count,
            ))

        n_queries = len(current_round)

        # Dynamic result-budget model (MODE_PROFILES.md §1). When the planner
        # passes a ``result_budget``, derive the PER-SUB-QUERY keep cap from the
        # actual emitted query count N. When None (CLI / monitor path), fall
        # back to the fixed ``reranker_max_keep``.
        if deps.result_budget is not None:
            max_keep = math.ceil(
                deps.result_budget / max(n_queries, MIN_EXPANDER_DIVISOR)
            )
            logger.info(
                "RerankerNode: dynamic keep — result_budget=%d, N=%d -> "
                "per-query max_keep=%d",
                deps.result_budget, n_queries, max_keep,
            )
        else:
            max_keep = deps.reranker_max_keep

        state.sse_events.append({
            "type": "status",
            "text": f"جاري تصنيف النتائج عبر {n_queries} استعلام بالتوازي...",
        })

        # -- One reranker call per sub-query, concurrently --------------------
        async def _process_one(qi: int, entry: dict):
            query = entry.get("query", "")
            rationale = entry.get("rationale", "")
            rows = entry.get("rows", []) or []
            try:
                output, kept, usage, user_message, messages_json, dropped = (
                    await run_reranker_for_query(
                        query=query,
                        rationale=rationale,
                        rows=rows,
                        max_keep=max_keep,
                        round_count=state.round_count,
                        model_override=deps.model_override,
                    )
                )
            except Exception as e:
                logger.error("RerankerNode q%d error: %s", qi + 1, e, exc_info=True)
                return None

            # Record per-sub-query drops keyed by the same ``query`` string used
            # for ``state.per_query_service_refs`` (SearchNode) — the orchestrator
            # threads this into ``compliance_to_rqr`` as ``per_query_dropped``.
            state.per_query_dropped[query] = dropped

            usage["round"] = state.round_count
            usage["query_index"] = qi + 1
            state.inner_usage.append(usage)

            if state.log_id and rows:
                try:
                    save_reranker_md(
                        log_id=state.log_id,
                        round_num=state.round_count,
                        user_message=user_message,
                        output=output,
                        all_results_flat=rows,
                        usage=usage,
                        messages_json=messages_json,
                        query_index=qi + 1,
                        query=query,
                    )
                except Exception as le:
                    logger.warning("save_reranker_md failed: %s", le)

            return output, kept

        gathered = await asyncio.gather(
            *[_process_one(qi, e) for qi, e in enumerate(current_round)]
        )
        per_query = [g for g in gathered if g is not None]

        if not per_query:
            # Every sub-query reranker failed.
            logger.warning("RerankerNode: all per-query rerankers failed")
            state.sse_events.append({
                "type": "status",
                "text": "حدث خطأ أثناء تصنيف النتائج.",
            })
            return End(ComplianceSearchResult(
                kept_results=list(state.kept_results),
                quality="weak",
                queries_used=list(state.queries_used),
                rounds_used=state.round_count,
            ))

        # -- Merge across sub-queries -----------------------------------------
        # A service surfaced (and kept) by two sub-queries is judged twice; keep
        # the better verdict (high beats medium, ties by score). Seed with any
        # results already kept in earlier rounds.
        _RANK = {"high": 1, "medium": 0}

        def _better(
            a: RerankedServiceResult, b: RerankedServiceResult
        ) -> RerankedServiceResult:
            if _RANK.get(a.relevance, 0) != _RANK.get(b.relevance, 0):
                return a if _RANK.get(a.relevance, 0) > _RANK.get(b.relevance, 0) else b
            return a if a.score >= b.score else b

        best_by_ref: dict[str, RerankedServiceResult] = {
            r.service_ref: r for r in state.kept_results if r.service_ref
        }
        sufficient_all = True
        merged_weak_axes: list[WeakAxis] = []
        notes: list[str] = []
        for output, kept in per_query:
            if not output.sufficient:
                sufficient_all = False
            merged_weak_axes.extend(output.weak_axes)
            if output.summary_note:
                notes.append(output.summary_note)
            for r in kept:
                if not r.service_ref:
                    continue
                existing = best_by_ref.get(r.service_ref)
                best_by_ref[r.service_ref] = (
                    r if existing is None else _better(r, existing)
                )

        merged = list(best_by_ref.values())
        high_kept = sorted(
            [r for r in merged if r.relevance == "high"], key=lambda r: -r.score
        )
        med_kept = sorted(
            [r for r in merged if r.relevance != "high"], key=lambda r: -r.score
        )
        ordered = high_kept + med_kept

        # Global cap over the merged pool. With a planner budget the union is
        # capped at the total budget; otherwise allow up to N×per-query keep
        # (effectively the full deduped union — parity with reg/case, which do
        # not re-trim across sub-queries).
        global_cap = (
            deps.result_budget
            if deps.result_budget is not None
            else n_queries * max_keep
        )
        cap_truncated = max(0, len(ordered) - global_cap)
        state.kept_results = ordered[:global_cap]
        if cap_truncated > 0:
            logger.info(
                "RerankerNode: global cap truncated %d results (cap=%d)",
                cap_truncated, global_cap,
            )

        # Merged round-level output (state + summaries + routing signal).
        merged_output = ServiceRerankerOutput(
            sufficient=sufficient_all,
            keeps=[],
            weak_axes=merged_weak_axes,
            summary_note=" | ".join(notes)[:500],
        )
        state.reranker_output = merged_output

        kept_count = len(state.kept_results)
        logger.info(
            "RerankerNode: merged kept=%d across %d sub-queries, sufficient=%s, "
            "weak_axes=%d",
            kept_count, len(per_query), sufficient_all, len(merged_weak_axes),
        )
        state.sse_events.append({
            "type": "status",
            "text": (
                f"تم الاحتفاظ بـ {kept_count} خدمة ذات صلة — "
                f"الجودة: {'كافية' if sufficient_all else 'غير كافية'}"
            ),
        })

        # Track round summary
        state.round_summaries.append({
            "round": state.round_count,
            "expander_queries": list(state.expander_output.queries) if state.expander_output else [],
            "search_total": len(state.all_results_flat),
            "reranker_kept": kept_count,
            "reranker_sufficient": sufficient_all,
            "weak_axes_count": len(merged_weak_axes),
        })

        # Route: loop back if not sufficient and rounds remain
        if not sufficient_all and state.round_count < MAX_ROUNDS:
            state.weak_axes = merged_weak_axes
            state.sse_events.append({
                "type": "status",
                "text": f"جاري إعادة البحث (الجولة {state.round_count + 1})...",
            })
            logger.info(
                "RerankerNode: looping back -- %d weak axes",
                len(merged_weak_axes),
            )
            return ExpanderNode()

        # Sufficient or max rounds reached — determine quality
        if sufficient_all and state.round_count == 1:
            quality = "strong"
        elif sufficient_all:
            quality = "moderate"
        else:
            quality = "weak"

        state.sse_events.append({
            "type": "status",
            "text": f"اكتمل البحث — الجودة: {quality}",
        })

        return End(ComplianceSearchResult(
            kept_results=list(state.kept_results),
            quality=quality,
            queries_used=list(state.queries_used),
            rounds_used=state.round_count,
        ))


# -- Graph assembly and entry point --------------------------------------------


compliance_search_graph = Graph(
    nodes=[ExpanderNode, SearchNode, RerankerNode],
)


async def run_compliance_search(
    focus_instruction: str,
    user_context: str,
    deps: ComplianceSearchDeps,
    log_id: str | None = None,
    context_blocks: list[ContextBlock] | None = None,
) -> ComplianceSearchResult:
    """Run the compliance search loop.

    Creates fresh LoopState, runs the pydantic_graph from ExpanderNode,
    transfers SSE events to deps._events, returns ComplianceSearchResult.

    Args:
        focus_instruction: Arabic -- what to search for (regulation context + query).
        user_context: Arabic -- user's personal situation/question (may be empty).
        deps: ComplianceSearchDeps with supabase, embedding_fn, etc.

    Returns:
        ComplianceSearchResult with kept_results (raw service dicts), quality,
        queries_used, and rounds_used.
    """
    from .logger import create_run_dir, make_log_id, save_run_log

    logger.info(
        "run_compliance_search: focus='%s'",
        focus_instruction[:80],
    )

    if not log_id:
        log_id = make_log_id()
    create_run_dir(log_id)

    state = LoopState(
        focus_instruction=focus_instruction,
        user_context=user_context,
        log_id=log_id,
        context_blocks=list(context_blocks) if context_blocks else [],
    )

    t0 = _time.perf_counter()
    error_msg: str | None = None

    try:
        graph_result = await compliance_search_graph.run(
            ExpanderNode(),
            state=state,
            deps=deps,
        )

        # Transfer SSE events from loop state to deps
        deps._events.extend(state.sse_events)

        output = graph_result.output

        logger.info(
            "run_compliance_search complete: quality=%s, rounds=%d, kept=%d, queries=%d",
            output.quality,
            output.rounds_used,
            len(output.kept_results),
            len(output.queries_used),
        )

    except Exception as e:
        logger.error("run_compliance_search failed: %s", e, exc_info=True)
        error_msg = str(e)
        deps._events.extend(state.sse_events)
        deps._events.append({
            "type": "status",
            "text": "حدث خطأ أثناء حلقة البحث في الخدمات الحكومية.",
        })

        output = ComplianceSearchResult(
            kept_results=list(state.kept_results),
            quality="weak",
            queries_used=list(state.queries_used),
            rounds_used=state.round_count,
        )

    duration = _time.perf_counter() - t0

    # Save logs (run.json + run.md + per-node MDs already written inline)
    save_run_log(
        log_id=log_id,
        focus_instruction=focus_instruction,
        user_context=user_context,
        duration_s=duration,
        result=output,
        events=list(deps._events),
        search_results_log=list(state.search_results_log),
        inner_usage=list(state.inner_usage),
        round_summaries=list(state.round_summaries),
        error=error_msg,
    )

    return output
