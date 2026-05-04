"""CLI test runner for reg_search.

Usage:
    # Full loop — random query from test_queries.json (default)
    python -m agents.deep_search_v4.reg_search.cli

    # Full loop — specific query by ID
    python -m agents.deep_search_v4.reg_search.cli --query-id 23

    # Full loop — custom query text (auto-appended to test_queries.json)
    python -m agents.deep_search_v4.reg_search.cli "your query"

    # With explicit sector filter (planner-equivalent override)
    python -m agents.deep_search_v4.reg_search.cli --query-id 23 --sectors "العمل والتوظيف"

    # Expander + search only (no reranker)
    python -m agents.deep_search_v4.reg_search.cli --query-id 5 --expand-only

    # Aggregator only (re-use search results from a previous log)
    python -m agents.deep_search_v4.reg_search.cli --aggregate-only query_23/20260405_140000

    # Utilities
    python -m agents.deep_search_v4.reg_search.cli --list-prompts
    python -m agents.deep_search_v4.reg_search.cli --list-logs
    python -m agents.deep_search_v4.reg_search.cli --read-log query_23/20260405_140000

Logs are organized by query: logs/query_{id}/{timestamp}/
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .logger import LOGS_DIR


def _get_expander_model_id(model_override: str | None = None) -> str:
    """Lazy import — returns the actual model ID string being used."""
    if model_override:
        from agents.model_registry import MODEL_REGISTRY
        config = MODEL_REGISTRY.get(model_override)
        return config.model_id if config else model_override
    from .expander import get_expander_model_id
    return get_expander_model_id()


def _resolve_models(model_override: str | None = None) -> dict[str, str]:
    """Resolve the model ID for each agent in the reg_search pipeline.

    Reranker is hardcoded to its registry default (--model does NOT apply).
    Expander uses the override if provided; reranker always uses its default.
    """
    from agents.model_registry import MODEL_REGISTRY
    from agents.utils.agent_models import AGENT_MODELS

    def resolve(override: str | None, default_key: str) -> str:
        key = override or AGENT_MODELS.get(default_key, "")
        config = MODEL_REGISTRY.get(key)
        return config.model_id if config else key

    return {
        "expander": resolve(model_override, "reg_search_expander"),
        "reranker": resolve(None, "reg_search_reranker"),
    }


# All OpenRouter model keys available for --model
OR_MODEL_CHOICES = [
    "or-minimax-m2.7",
    "or-gemini-3.1-pro",
    "or-gemini-3.1-pro-tools",
    "or-gemini-2.5-pro",
    "or-gemini-2.5-flash",
    "or-deepseek-chat",
    "or-qwen3.5-397b",
    "or-deepseek-v3.2",
    "or-mimo-v2-pro",
    "or-glm-5-turbo",
    "or-gemma-4-31b",
]

# All Alibaba (Qwen) model keys available for --model
ALIBABA_MODEL_CHOICES = [
    "qwen3.6-plus",
    "qwen3.5-plus",
    "qwen3.5-flash",
    "qwen3-max",
    "qwen3-coder-plus",
    "qwen3-coder-flash",
    "qwen3-vl-plus",
    "qwen3-vl-flash",
    "qwq-plus",
    "qvq-max",
    "qwen-plus",
    "qwen-long",
    "qwen-vl-ocr",
]

ALL_MODEL_CHOICES = OR_MODEL_CHOICES + ALIBABA_MODEL_CHOICES


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def format_result(result) -> str:
    """Format RegSearchResult for terminal display."""
    quality_labels = {
        "strong": "قوية",
        "moderate": "متوسطة",
        "weak": "ضعيفة",
    }

    lines: list[str] = []
    lines.append(f"\n{'=' * 60}")
    lines.append(f"Quality: {result.quality} ({quality_labels.get(result.quality, '')})")
    lines.append(f"Rounds: {result.rounds_used}")
    lines.append(f"Queries: {len(result.queries_used)}")
    lines.append(f"Citations: {len(result.citations)}")
    lines.append(f"Prompt keys: expander={result.expander_prompt_key}")
    lines.append(f"{'=' * 60}\n")

    # Queries used
    lines.append("Queries used:")
    for i, q in enumerate(result.queries_used, 1):
        lines.append(f"  {i}. {q}")
    lines.append("")

    # Citations
    if result.citations:
        lines.append(f"Citations ({len(result.citations)}):")
        for i, c in enumerate(result.citations, 1):
            lines.append(f"  {i}. [{c.source_type}] {c.ref} -- {c.title}")
        lines.append("")

    # Summary preview
    lines.append(f"{'~' * 40}")
    lines.append(f"Summary ({len(result.summary_md)} chars):")
    preview = result.summary_md[:1000]
    lines.append(preview)
    if len(result.summary_md) > 1000:
        lines.append(f"... ({len(result.summary_md) - 1000} more chars)")

    return "\n".join(lines)


def list_logs(limit: int = 20) -> list[str]:
    """List recent log IDs. Handles nested query_{id}/{timestamp}/run.json."""
    if not LOGS_DIR.exists():
        return []
    # Find all run.json files at any depth
    run_files = sorted(LOGS_DIR.glob("**/run.json"), reverse=True)
    # Return the path relative to LOGS_DIR (minus /run.json)
    return [str(f.parent.relative_to(LOGS_DIR)) for f in run_files[:limit]]


def read_log(log_id: str) -> dict:
    """Read a log entry. Supports query_{id}/{timestamp} paths."""
    path = LOGS_DIR / log_id / "run.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    # Legacy fallback: flat file
    old_path = LOGS_DIR / f"{log_id}.json"
    if old_path.exists():
        return json.loads(old_path.read_text(encoding="utf-8"))
    return {"error": f"Log {log_id} not found"}


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CLI test runner for reg_search agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            '  python -m agents.deep_search_v4.reg_search.cli "أحكام إنهاء عقد العمل"\n'
            '  python -m agents.deep_search_v4.reg_search.cli "query" --sectors "العمل والتوظيف"\n'
            "  python -m agents.deep_search_v4.reg_search.cli --list-prompts\n"
            "  python -m agents.deep_search_v4.reg_search.cli --list-logs\n"
            "  python -m agents.deep_search_v4.reg_search.cli --read-log LOG_ID\n"
        ),
    )

    parser.add_argument(
        "query",
        nargs="?",
        default=None,
        help="Focus instruction / search query in Arabic (default: random from test_queries.json)",
    )
    parser.add_argument(
        "--query-id",
        type=int,
        default=None,
        help="Use a specific query ID from test_queries.json (e.g. --query-id 23)",
    )
    parser.add_argument(
        "--user-context",
        default="",
        help="User context string (Arabic)",
    )
    parser.add_argument(
        "--expander-prompt",
        default="prompt_1",
        help="Expander prompt variant key (default: prompt_1)",
    )
    parser.add_argument(
        "--aggregator-prompt",
        default="prompt_1",
        help="Aggregator prompt variant key (default: prompt_1)",
    )
    # Mode flags
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--expand-only",
        action="store_true",
        help="Run expander + search only (no aggregator). Prints queries and raw search results.",
    )
    mode_group.add_argument(
        "--aggregate-only",
        metavar="LOG_ID",
        default=None,
        help="Run aggregator only on search results from a previous log. No expander or search.",
    )
    mode_group.add_argument(
        "--replay-reranker",
        metavar="PATH",
        default=None,
        help=(
            "Re-run the reranker on existing search results. "
            "PATH is a folder containing a search/ subfolder "
            "(absolute path or log_id relative to LOGS_DIR, e.g. query_27/20260427_092619). "
            "Results are written to PATH/reranker/, overwriting previous output."
        ),
    )

    parser.add_argument(
        "--sectors",
        default=None,
        help="Pipe- or comma-separated sector names to filter by (planner-equivalent override). "
             "Names must match the regulations vocab exactly.",
    )
    parser.add_argument(
        "--rerank",
        action="store_true",
        help="Enable Jina reranker (off by default)",
    )
    parser.add_argument(
        "--score-threshold",
        type=float,
        default=0.005,
        help="Minimum score to include after Jina rerank (default: 0.005).",
    )
    parser.add_argument(
        "--rrf-min",
        type=float,
        default=0.1,
        help="Minimum RRF score to keep before reranker (default: 0.1). "
             "Cuts tail positions (19-30) that are almost always dropped by the LLM reranker. "
             "Set to 0 to disable.",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use mock results (no real DB/API calls)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print extra debug info",
    )
    parser.add_argument(
        "--batch",
        metavar="IDS",
        default=None,
        help="Run expand-only on multiple query IDs sequentially with delay. "
             "Comma-separated IDs (e.g. --batch 5,9,14,23,27). "
             "Uses --expander-prompt and --expand-only implicitly.",
    )
    parser.add_argument(
        "--delay",
        type=int,
        default=5,
        help="Seconds to wait between queries in --batch mode (default: 5)",
    )
    parser.add_argument(
        "--thinking",
        choices=["low", "medium", "high", "none"],
        default=None,
        help="Reasoning effort for the expander agent (default: per-prompt setting). "
             "Use 'none' to disable reasoning entirely.",
    )
    parser.add_argument(
        "--model",
        choices=ALL_MODEL_CHOICES,
        default=None,
        help="Override the model for the expander (reranker uses its default)."
             "(OpenRouter or Alibaba/Qwen models). "
             "Default: per-agent setting from agent_models.py.",
    )
    parser.add_argument(
        "--unfold",
        choices=["precise", "detailed"],
        default="precise",
        help="Unfolding mode: 'precise' (compact, default) or 'detailed' (full content).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="Max concurrent search pipelines (default: 10). Tune for your infra.",
    )
    parser.add_argument(
        "--skip-reranker",
        action="store_true",
        help="Skip the reranker node (default: reranker enabled). "
             "Results go directly from search to aggregator.",
    )
    parser.add_argument(
        "--reranker-only",
        action="store_true",
        help="Run expander + search + reranker only (no aggregator). "
             "Validates reranker output without synthesis.",
    )
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="Print available models (OpenRouter + Alibaba) with pricing info, then exit",
    )
    parser.add_argument(
        "--list-prompts",
        action="store_true",
        help="Print available prompt keys for the expander, then exit",
    )
    parser.add_argument(
        "--list-logs",
        action="store_true",
        help="List recent log files",
    )
    parser.add_argument(
        "--read-log",
        metavar="LOG_ID",
        default=None,
        help="Read a specific log by ID",
    )

    return parser


# ---------------------------------------------------------------------------
# Expand-only: expander + search, no aggregator
# ---------------------------------------------------------------------------

def _parse_cli_sectors(value: str | None) -> list[str] | None:
    """Parse --sectors CLI value (pipe- or comma-separated) into a list."""
    if not value:
        return None
    parts = [s.strip() for s in value.replace("|", ",").split(",")]
    cleaned = [p for p in parts if p]
    return cleaned or None


async def run_expand_only(args, query: str, deps) -> None:
    """Run expander agent + search pipeline. Skip aggregator."""
    import time

    from .expander import EXPANDER_LIMITS, create_expander_agent
    from .prompts import build_expander_user_message
    from .search import search_regulations_pipeline

    model_label = args.model or "(default)"
    print(f"[expand-only] Expander prompt: {args.expander_prompt}")
    print(f"[expand-only] Model: {model_label}")
    print(f"[expand-only] Running expander...")

    expander = create_expander_agent(
        prompt_key=args.expander_prompt,
        thinking_effort=args.thinking,
        model_override=args.model,
    )
    user_message = build_expander_user_message(query, args.user_context)

    t0 = time.perf_counter()
    result = await expander.run(user_message, usage_limits=EXPANDER_LIMITS)
    exp_duration = time.perf_counter() - t0
    output = result.output

    print(f"\n[expand-only] Expander done in {exp_duration:.1f}s")
    print(f"[expand-only] Queries ({len(output.queries)}):")
    for i, q in enumerate(output.queries, 1):
        rationale = output.rationales[i - 1] if i <= len(output.rationales) else ""
        print(f"  {i}. {q}")
        if rationale:
            print(f"     rationale: {rationale}")

    # Usage
    eu = result.usage()
    print(f"[expand-only] Expander usage: {eu.input_tokens} in / {eu.output_tokens} out / {eu.requests} req")

    # Sector filter comes from --sectors only (planner-equivalent override).
    filter_sectors = _parse_cli_sectors(args.sectors)
    print(f"[expand-only] Sector filter: {' | '.join(filter_sectors) if filter_sectors else '(none)'}")

    # Now run search for each query
    import asyncio
    from agents.utils.embeddings import embed_regulation_queries_alibaba

    print(f"\n[expand-only] Batch-embedding {len(output.queries)} queries...")
    t_emb = time.perf_counter()
    embeddings = await embed_regulation_queries_alibaba(list(output.queries))
    emb_duration = time.perf_counter() - t_emb
    print(f"[expand-only] Embeddings done in {emb_duration:.1f}s")

    print(f"[expand-only] Running search for {len(output.queries)} queries (concurrency={args.concurrency})...")
    sem = asyncio.Semaphore(args.concurrency)

    t1 = time.perf_counter()
    tasks = [
        search_regulations_pipeline(
            q, deps, filter_sectors=filter_sectors, unfold_mode=args.unfold,
            precomputed_embedding=emb, semaphore=sem,
        )
        for q, emb in zip(output.queries, embeddings)
    ]
    results_raw = await asyncio.gather(*tasks)
    search_duration = time.perf_counter() - t1

    print(f"[expand-only] Search done in {search_duration:.1f}s")
    print(f"\n{'=' * 60}")

    total_count = 0
    for i, (query_text, (raw_md, count)) in enumerate(zip(output.queries, results_raw), 1):
        total_count += count
        print(f"\n--- Query {i}: \"{query_text}\" ({count} results) ---")
        preview = raw_md[:2000]
        print(preview)
        if len(raw_md) > 2000:
            print(f"... ({len(raw_md) - 2000} more chars)")

    print(f"\n{'=' * 60}")
    print(f"Total: {len(output.queries)} queries, {total_count} results")
    print(f"Duration: expander {exp_duration:.1f}s + search {search_duration:.1f}s = {exp_duration + search_duration:.1f}s")

    # Save per-file logs
    from .logger import (
        create_run_dir,
        make_log_id,
        save_expander_md,
        save_run_json,
        save_run_overview_md,
        save_search_query_md,
    )
    from .prompts import get_expander_prompt
    from .models import RegSearchResult

    log_id = make_log_id(deps._query_id) + "_expand" if deps._query_id else datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_expand"
    create_run_dir(log_id)

    # Expander markdown
    save_expander_md(
        log_id=log_id, round_num=1, prompt_key=args.expander_prompt,
        system_prompt=get_expander_prompt(args.expander_prompt),
        user_message=user_message, output=output,
        usage=eu, messages_json=result.all_messages_json(),
    )

    # Per-query search markdowns
    search_log = []
    for qi, (q_text, (raw_md, count)) in enumerate(zip(output.queries, results_raw), 1):
        rationale = output.rationales[qi - 1] if qi <= len(output.rationales) else ""
        save_search_query_md(
            log_id=log_id, round_num=1, query_index=qi,
            query=q_text, raw_markdown=raw_md, result_count=count,
            rationale=rationale,
        )
        search_log.append({
            "round": 1, "query": q_text, "rationale": rationale,
            "result_count": count,
            "raw_markdown_length": len(raw_md), "raw_markdown": raw_md,
        })

    # Overview + JSON
    pending_result = RegSearchResult(
        quality="pending", summary_md="", citations=[],
        queries_used=list(output.queries), rounds_used=1,
        expander_prompt_key=args.expander_prompt,
    )
    save_run_overview_md(
        log_id=log_id, focus_instruction=query, user_context=args.user_context,
        expander_prompt_key=args.expander_prompt,
        duration_s=exp_duration + search_duration, result=pending_result,
        round_summaries=[{"round": 1, "expander_queries": list(output.queries),
                          "search_queries": len(output.queries), "search_total": total_count}],
    )
    save_run_json(
        log_id=log_id, focus_instruction=query, user_context=args.user_context,
        expander_prompt_key=args.expander_prompt,
        duration_s=exp_duration + search_duration, result=pending_result,
        events=deps._events,
        round_summaries=[{"round": 1, "expander_queries": list(output.queries),
                          "search_queries": len(output.queries), "search_total": total_count}],
        search_results_log=search_log,
        models=_resolve_models(args.model),
        thinking_effort=args.thinking,
    )
    print(f"\nLog dir: agents/deep_search_v3/reg_search/logs/{log_id}/")


# ---------------------------------------------------------------------------
# Batch: run expand-only on multiple queries sequentially
# ---------------------------------------------------------------------------

async def run_batch(args) -> None:
    """Run expand-only on multiple queries with delay between each."""
    import time

    from .expander import EXPANDER_LIMITS, create_expander_agent
    from .prompts import build_expander_user_message
    from .logger import (
        create_run_dir, make_log_id, resolve_query_id,
        save_expander_md, save_run_json, save_run_overview_md, save_search_query_md,
    )
    from .prompts import get_expander_prompt
    from .models import RegSearchDeps, RegSearchResult
    from .search import search_regulations_pipeline

    from agents.utils.embeddings import embed_regulation_query
    from shared.config import get_settings
    from shared.db.client import get_supabase_client

    query_ids = [int(x.strip()) for x in args.batch.split(",")]
    prompt_key = args.expander_prompt
    delay = args.delay

    model_label = args.model or "(default)"
    print(f"{'=' * 90}")
    print(f"BATCH RUN — {len(query_ids)} queries | prompt: {prompt_key} | model: {model_label} | delay: {delay}s")
    print(f"{'=' * 90}")

    settings = get_settings()
    supabase = get_supabase_client()

    # Per-query summary rows for final table
    summary_rows: list[dict] = []

    for run_idx, qid in enumerate(query_ids):
        # Delay between queries (skip first)
        if run_idx > 0:
            print(f"\n--- waiting {delay}s ---\n")
            await asyncio.sleep(delay)

        # Resolve query
        try:
            query_id, query_text = resolve_query_id(None, qid)
        except Exception as e:
            print(f"[query_{qid}] SKIP — {e}")
            summary_rows.append({"id": qid, "status": "skip", "error": str(e)})
            continue

        print(f"[{run_idx+1}/{len(query_ids)}] query_{query_id}: {query_text[:80]}...")

        deps = RegSearchDeps(
            supabase=supabase,
            embedding_fn=embed_regulation_query,
            jina_api_key=getattr(settings, "JINA_API_KEY", ""),
            use_reranker=args.rerank,
            score_threshold=args.score_threshold,
            rrf_min_score=args.rrf_min,
            _query_id=query_id,
        )

        row: dict = {"id": query_id, "status": "ok", "query": query_text[:60]}

        # ── Expander ──────────────────────────────────────────
        try:
            expander = create_expander_agent(
                prompt_key=prompt_key,
                thinking_effort=args.thinking,
                model_override=args.model,
            )
            user_message = build_expander_user_message(query_text, args.user_context)

            t0 = time.perf_counter()
            result = await expander.run(user_message, usage_limits=EXPANDER_LIMITS)
            exp_dur = time.perf_counter() - t0
            output = result.output
            eu = result.usage()

            batch_sectors = _parse_cli_sectors(args.sectors)
            row["exp_dur"] = round(exp_dur, 1)
            row["exp_queries"] = len(output.queries)
            row["exp_sectors"] = batch_sectors
            row["exp_tokens"] = f"{eu.input_tokens}in/{eu.output_tokens}out"

            print(f"  EXPANDER  {row['exp_dur']}s | {row['exp_queries']} queries | {row['exp_tokens']}")
            sectors_str = " | ".join(batch_sectors) if batch_sectors else "(none)"
            print(f"  Sectors:  {sectors_str}")

        except Exception as e:
            print(f"  EXPANDER FAILED: {e}")
            row["status"] = "exp_fail"
            row["error"] = str(e)[:120]
            summary_rows.append(row)
            continue

        # ── Search (per sub-query) ────────────────────────────
        # Sector filter comes from --sectors only (planner-equivalent override).
        filter_sectors: list[str] | None = list(batch_sectors) if batch_sectors else None

        search_details: list[dict] = []
        search_errors: list[str] = []

        # Batch-embed all sub-queries at once
        from agents.utils.embeddings import embed_regulation_queries_alibaba
        sub_embeddings = await embed_regulation_queries_alibaba(list(output.queries))

        t1 = time.perf_counter()
        for qi, (sq, sq_emb) in enumerate(zip(output.queries, sub_embeddings), 1):
            sq_deps = RegSearchDeps(
                supabase=supabase,
                embedding_fn=embed_regulation_query,
                score_threshold=args.score_threshold,
                rrf_min_score=args.rrf_min,
            )
            qt0 = time.perf_counter()
            raw_md = ""
            try:
                raw_md, count = await search_regulations_pipeline(
                    query=sq, deps=sq_deps, filter_sectors=filter_sectors,
                    unfold_mode=args.unfold,
                    precomputed_embedding=sq_emb,
                )
            except Exception as e:
                count = 0
                err_msg = f"q{qi} EXCEPTION: {e}"
                search_errors.append(err_msg)
                print(f"    q{qi:>2} FAIL — {e}")
            qt_dur = time.perf_counter() - qt0

            # Check fallback (sector filter returned 0, retried without)
            fallback = any("بدون تصفية" in ev.get("text", "") for ev in sq_deps._events)
            if fallback:
                print(f"    q{qi:>2} FALLBACK — sector filter gave 0 results, retried without filter")

            # Check and print errors inline as they happen
            errs = [ev["text"] for ev in sq_deps._events if ev.get("type") == "error"]
            for err in errs:
                print(f"    q{qi:>2} ERROR — {err}")
                search_errors.append(f"q{qi}: {err}")

            filter_label = "fallback" if fallback else ("yes" if filter_sectors else "no")
            detail = {"qi": qi, "query": sq[:55], "results": count,
                      "dur": round(qt_dur, 1), "filter": filter_label,
                      "raw_markdown": raw_md}
            search_details.append(detail)

            status_str = "OK  " if count > 0 else "MISS"
            print(f"    q{qi:>2} {status_str} {qt_dur:>5.1f}s {count:>3} res  [{filter_label:<8}] {sq[:55]}")

        total_search_dur = time.perf_counter() - t1
        total_results = sum(d["results"] for d in search_details)

        row["srch_dur"] = round(total_search_dur, 1)
        row["total_dur"] = round(exp_dur + total_search_dur, 1)
        row["results"] = total_results
        row["search_details"] = search_details
        row["errors"] = search_errors

        print(f"  SEARCH    {row['srch_dur']}s | {total_results} results | "
              f"{len(search_errors)} errors")
        print(f"  TOTAL     {row['total_dur']}s")

        # ── Save log ──────────────────────────────────────────
        log_id = make_log_id(query_id) + "_expand"
        create_run_dir(log_id)
        save_expander_md(
            log_id=log_id, round_num=1, prompt_key=prompt_key,
            system_prompt=get_expander_prompt(prompt_key),
            user_message=user_message, output=output,
            usage=eu, messages_json=result.all_messages_json(),
        )
        search_log = []
        for qi, (sq_text, detail) in enumerate(zip(output.queries, search_details), 1):
            sq_raw_md = detail.get("raw_markdown", "")
            rationale = output.rationales[qi - 1] if qi <= len(output.rationales) else ""
            save_search_query_md(
                log_id=log_id, round_num=1, query_index=qi,
                query=sq_text, raw_markdown=sq_raw_md,
                result_count=detail["results"],
                rationale=rationale,
            )
            search_log.append({
                "round": 1, "query": sq_text, "rationale": rationale,
                "result_count": detail["results"],
                "raw_markdown_length": len(sq_raw_md),
                "raw_markdown": sq_raw_md,
            })
        pending_result = RegSearchResult(
            quality="pending", summary_md="", citations=[],
            queries_used=list(output.queries), rounds_used=1,
            expander_prompt_key=prompt_key, aggregator_prompt_key="",
        )
        save_run_json(
            log_id=log_id, focus_instruction=query_text,
            user_context=args.user_context,
            expander_prompt_key=prompt_key, aggregator_prompt_key="",
            duration_s=row["total_dur"], result=pending_result,
            events=deps._events,
            round_summaries=[{"round": 1, "expander_queries": list(output.queries),
                              "search_queries": len(output.queries),
                              "search_total": total_results}],
            search_results_log=search_log,
            models=_resolve_models(args.model),
            thinking_effort=args.thinking,
        )
        row["log_id"] = log_id
        print(f"  Log:      {log_id}")
        # Errors already printed inline above — just show count in summary line
        if search_errors:
            print(f"  ({len(search_errors)} error(s) above)")

        summary_rows.append(row)

    # ── Summary table ─────────────────────────────────────────
    print(f"\n{'=' * 90}")
    print("BATCH SUMMARY")
    print(f"{'=' * 90}")
    print(f"{'ID':>3} {'Status':<8} {'Exp':>5} {'Srch':>6} {'Total':>6} "
          f"{'Q':>2} {'Res':>4} {'Err':>3} Sectors")
    print(f"{'─'*3} {'─'*8} {'─'*5} {'─'*6} {'─'*6} {'─'*2} {'─'*4} {'─'*3} {'─'*40}")

    total_time = 0.0
    total_results = 0
    total_errors = 0
    for r in summary_rows:
        if r["status"] not in ("ok",):
            print(f"{r['id']:>3} {r['status']:<8} {'—':>5} {'—':>6} {'—':>6} "
                  f"{'—':>2} {'—':>4} {'—':>3} {r.get('error', '')[:40]}")
            continue
        sectors = ", ".join(r.get("exp_sectors", [])[:2]) if r.get("exp_sectors") else "(none)"
        nerr = len(r.get("errors", []))
        print(f"{r['id']:>3} {'OK':<8} {r['exp_dur']:>4}s {r['srch_dur']:>5}s {r['total_dur']:>5}s "
              f"{r['exp_queries']:>2} {r['results']:>4} {nerr:>3} {sectors}")
        total_time += r["total_dur"]
        total_results += r["results"]
        total_errors += nerr

    ok_count = sum(1 for r in summary_rows if r["status"] == "ok")
    print(f"\n{ok_count}/{len(summary_rows)} succeeded | "
          f"{total_time:.0f}s total | {total_results} results | {total_errors} errors")


# ---------------------------------------------------------------------------
# Aggregate-only: load search results from log, run aggregator
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Replay-reranker: re-run reranker on existing search results
# ---------------------------------------------------------------------------


def _write_reranker_md_to_dir(
    out_dir: Path,
    round_num: int,
    query_idx: int,
    rqr,
) -> None:
    """Write a reranker result markdown directly to out_dir (no LOGS_DIR routing)."""
    import re as _re
    import unicodedata

    def _slugify(text: str, max_len: int = 20) -> str:
        cleaned = _re.sub(r"[^\w\s]", "", text, flags=_re.UNICODE)
        cleaned = cleaned.strip()[:max_len]
        return _re.sub(r"\s+", "_", cleaned) or "query"

    slug = _slugify(rqr.query)
    path = out_dir / f"round_{round_num}_q{query_idx}_{slug}.md"

    lines: list[str] = [
        f"# Reranker — Round {round_num}, Query {query_idx}",
        "",
        f"**Query:** {rqr.query}",
    ]
    if rqr.rationale:
        lines.append(f"**Rationale:** {rqr.rationale}")
    lines += [
        f"**Sufficient:** {rqr.sufficient}",
        f"**Results kept:** {len(rqr.results)}",
        f"**Dropped:** {rqr.dropped_count}",
        f"**Classification rounds:** {rqr.unfold_rounds}",
        f"**DB unfolds:** {rqr.total_unfolds}",
    ]
    if rqr.summary_note:
        lines.append(f"**Summary:** {rqr.summary_note}")
    lines.append("")

    if rqr.results:
        lines.append(f"## Kept Results ({len(rqr.results)})")
        lines.append("")
        for i, res in enumerate(rqr.results, 1):
            rel_label = "عالية" if res.relevance == "high" else "متوسطة"
            type_label = "مادة" if res.source_type == "article" else "باب/فصل"
            lines.append(f"### {i}. [{type_label}] {res.title} (صلة: {rel_label})")
            if res.regulation_title:
                lines.append(f"- **النظام:** {res.regulation_title}")
            if res.section_title and res.source_type == "article":
                lines.append(f"- **الباب:** {res.section_title}")
            if res.article_num:
                lines.append(f"- **رقم المادة:** {res.article_num}")
            if res.reasoning:
                lines.append(f"- **Reasoning:** {res.reasoning}")
            if res.content:
                lines.append(f"\n> {res.content[:500]}")
            lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


async def run_replay_reranker(path_str: str) -> None:
    """Re-run the reranker on existing search results and write back to the same folder."""
    import re
    import time

    from shared.db.client import get_supabase_client

    from .reranker import run_reranker_for_query

    # Resolve path — accept absolute or relative to LOGS_DIR
    target = Path(path_str)
    if not target.is_absolute():
        target = LOGS_DIR / path_str

    search_dir = target / "search"
    reranker_dir = target / "reranker"

    if not search_dir.exists():
        print(f"[replay-reranker] ERROR: search/ not found at {search_dir}")
        return

    search_files = sorted(search_dir.glob("round_1_q*.md"))
    if not search_files:
        print(f"[replay-reranker] ERROR: no round_1_q*.md files in {search_dir}")
        return

    print(f"[replay-reranker] {len(search_files)} queries in {target}")
    reranker_dir.mkdir(parents=True, exist_ok=True)

    # Remove stale reranker files so old results don't linger
    for old in reranker_dir.glob("round_1_q*.md"):
        old.unlink()
    for old_dir in reranker_dir.glob("q*_rounds"):
        import shutil
        shutil.rmtree(old_dir, ignore_errors=True)

    supabase = get_supabase_client()
    total_kept = total_dropped = 0

    for sf in search_files:
        m = re.match(r"round_(\d+)_q(\d+)_", sf.name)
        if not m:
            print(f"[replay-reranker] SKIP: cannot parse filename {sf.name}")
            continue
        round_num, query_idx = int(m.group(1)), int(m.group(2))

        text = sf.read_text(encoding="utf-8")
        query = rationale = ""
        for line in text.splitlines():
            if line.startswith("**Query:**"):
                query = line.removeprefix("**Query:**").strip()
            elif line.startswith("**Rationale:**"):
                rationale = line.removeprefix("**Rationale:**").strip()

        sep = "\n---\n"
        raw_md = text.split(sep, 1)[1].strip() if sep in text else ""

        if not query or not raw_md:
            print(f"[replay-reranker] SKIP q{query_idx}: missing query or content")
            continue

        print(f"  q{query_idx:>2}: {query[:65]}", end="", flush=True)
        t0 = time.perf_counter()

        try:
            rqr, usage_entries, decision_log = await run_reranker_for_query(
                query=query,
                rationale=rationale,
                raw_markdown=raw_md,
                supabase=supabase,
            )
            rqr._usage_entries = usage_entries
            rqr._decision_log = decision_log

            _write_reranker_md_to_dir(reranker_dir, round_num, query_idx, rqr)
            dur = time.perf_counter() - t0

            total_kept += len(rqr.results)
            total_dropped += rqr.dropped_count
            print(f"  kept={len(rqr.results)} dropped={rqr.dropped_count} "
                  f"unfolds={rqr.total_unfolds} [{dur:.1f}s]")

        except Exception as e:
            dur = time.perf_counter() - t0
            print(f"  FAILED [{dur:.1f}s]: {e}")

    print(f"\n[replay-reranker] Done — kept={total_kept} dropped={total_dropped}")
    print(f"[replay-reranker] Written to {reranker_dir}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    """Main CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()


    # -- Handle --list-models --
    if args.list_models:
        from agents.model_registry import MODEL_REGISTRY
        from agents.utils.agent_models import AGENT_MODELS

        default_exp = AGENT_MODELS.get("reg_search_expander", "?")
        print(f"\nDefaults: expander={default_exp}, aggregator={default_agg}")
        for label, choices in [("OpenRouter", OR_MODEL_CHOICES), ("Alibaba/Qwen", ALIBABA_MODEL_CHOICES)]:
            print(f"\n{label} models (--model):")
            print(f"  {'Key':<28} {'Model ID':<45} {'In':>6} {'Out':>6}")
            print(f"  {'─'*28} {'─'*45} {'─'*6} {'─'*6}")
            for key in choices:
                cfg = MODEL_REGISTRY.get(key)
                if cfg:
                    inp = f"${cfg.input_price:.2f}" if cfg.input_price else "?"
                    out = f"${cfg.output_price:.2f}" if cfg.output_price else "?"
                    print(f"  {key:<28} {cfg.model_id:<45} {inp:>6} {out:>6}")
        return

    # -- Handle --list-prompts --
    if args.list_prompts:
        from .prompts import EXPANDER_PROMPTS

        print("\nExpander prompts:")
        for key in sorted(EXPANDER_PROMPTS.keys()):
            preview = EXPANDER_PROMPTS[key][:80].replace("\n", " ")
            print(f"  {key}: {preview}...")
        return

    # -- Handle --list-logs --
    if args.list_logs:
        logs = list_logs()
        if not logs:
            print("No logs found.")
        else:
            print(f"\nRecent logs ({len(logs)}):")
            for log_id in logs:
                log = read_log(log_id)
                status = log.get("status", "?")
                duration = log.get("duration_seconds", "?")
                quality = log.get("result", {}).get("quality", "?")
                rounds = log.get("result", {}).get("rounds", "?")
                focus = log.get("input", {}).get("focus_instruction", "")[:60]
                prompt_keys = log.get("prompt_keys", {})
                exp_key = prompt_keys.get("expander", "?")
                print(
                    f"  {log_id}  [{status}]  {duration}s  "
                    f"quality={quality}  rounds={rounds}  "
                    f"prompts=({exp_key},{agg_key})  {focus}..."
                )
        return

    # -- Handle --read-log LOG_ID --
    if args.read_log:
        log = read_log(args.read_log)
        print(json.dumps(log, ensure_ascii=False, indent=2))
        return

    # -- Handle --replay-reranker PATH --
    if args.replay_reranker:
        await run_replay_reranker(args.replay_reranker)
        return

    # -- Handle --aggregate-only LOG_ID (no deps needed) --
    # -- Handle --batch IDS --
    if args.batch:
        await run_batch(args)
        return

    # -- Resolve query (from --query-id, positional arg, or random) --
    from .logger import resolve_query_id

    query_id, query = resolve_query_id(args.query, args.query_id)
    print(f"[query_{query_id}] {query[:100]}...")

    from agents.utils.embeddings import embed_regulation_query
    from shared.config import get_settings
    from shared.db.client import get_supabase_client

    from .models import RegSearchDeps

    settings = get_settings()
    supabase = get_supabase_client()

    mock_results = None
    if args.mock:
        mock_results = {
            "regulations": (
                "# نتائج وهمية للاختبار\n\n"
                "## المادة 74 من نظام العمل\n"
                "ينتهي عقد العمل في الحالات التالية...\n\n"
                "## المادة 77 من نظام العمل\n"
                "إذا أنهي العقد لسبب غير مشروع...\n"
            ),
        }

    deps = RegSearchDeps(
        supabase=supabase,
        embedding_fn=embed_regulation_query,
        jina_api_key=getattr(settings, "JINA_API_KEY", ""),
        use_reranker=args.rerank,
        score_threshold=args.score_threshold,
        rrf_min_score=args.rrf_min,
        mock_results=mock_results,
        _query_id=query_id,
    )

    # -- Handle --expand-only --
    if args.expand_only:
        await run_expand_only(args, query, deps)
        return

    # -- Full loop (default) --
    import time

    model_label = args.model or "(default)"
    print(f"\nRunning reg_search -- full loop...")
    print(f"Query: {query[:100]}...")
    print(f"Expander prompt: {args.expander_prompt}")
    print(f"Model: {model_label}")
    print(f"Unfold: {args.unfold}")
    print(f"Concurrency: {args.concurrency}")
    print(f"Jina reranker: {'ON' if args.rerank else 'OFF'}")
    print(f"LLM Reranker: {'OFF (skipped)' if args.skip_reranker else 'ON'}")
    print(f"Score threshold: {args.score_threshold}")
    print("Please wait...\n")

    from .loop import run_reg_search

    result = await run_reg_search(
        focus_instruction=query,
        user_context=args.user_context,
        deps=deps,
        expander_prompt_key=args.expander_prompt,
        thinking_effort=args.thinking,
        model_override=args.model,
        unfold_mode=args.unfold,
        concurrency=args.concurrency,
        skip_reranker=args.skip_reranker,
        sectors_override=_parse_cli_sectors(args.sectors),
    )

    # Print result (logging happens inside run_reg_search now)
    print(format_result(result))

    if args.verbose:
        print(f"\n{'~' * 40}")
        print(f"Events collected: {len(deps._events)}")
        for e in deps._events:
            print(f"  [{e.get('type', '?')}] {json.dumps(e, ensure_ascii=False)[:120]}")

    # Show log location
    log_id = deps._log_id
    print(f"\nLog dir: agents/deep_search_v3/reg_search/logs/{log_id}/")


if __name__ == "__main__":
    asyncio.run(main())
