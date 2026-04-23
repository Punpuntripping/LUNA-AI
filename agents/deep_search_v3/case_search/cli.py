"""CLI test runner for case_search.

Usage:
    python -m agents.deep_search_v3.case_search.cli
    python -m agents.deep_search_v3.case_search.cli --query-id 23
    python -m agents.deep_search_v3.case_search.cli "your query"
    python -m agents.deep_search_v3.case_search.cli --query-id 23 --model or-gemma-4-31b
    python -m agents.deep_search_v3.case_search.cli --query-id 5 --expand-only
    python -m agents.deep_search_v3.case_search.cli --batch 1,5,9,14 --delay 5
    python -m agents.deep_search_v3.case_search.cli --list-prompts
    python -m agents.deep_search_v3.case_search.cli --list-models
    python -m agents.deep_search_v3.case_search.cli --list-logs
    python -m agents.deep_search_v3.case_search.cli --read-log query_23/20260405_140000
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Force UTF-8 output on Windows (Arabic text)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from .logger import LOGS_DIR
from .prompts import DEFAULT_EXPANDER_PROMPT, EXPANDER_PROMPTS


def _get_expander_model_id(model_override: str | None = None) -> str:
    if model_override:
        from agents.model_registry import MODEL_REGISTRY
        config = MODEL_REGISTRY.get(model_override)
        return config.model_id if config else model_override
    from .expander import get_expander_model_id
    return get_expander_model_id()


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

_VALID_CHANNELS = ("principle", "facts", "basis")


def _parse_channels(raw: str | None) -> list[str] | None:
    """Parse --channels flag; returns the subset of valid channels or None."""
    if not raw:
        return None
    picked = [c.strip().lower() for c in raw.split(",") if c.strip()]
    unknown = [c for c in picked if c not in _VALID_CHANNELS]
    if unknown:
        print(f"[WARN] ignoring unknown channels: {unknown}; valid={list(_VALID_CHANNELS)}")
    valid = [c for c in picked if c in _VALID_CHANNELS]
    return valid or None


def _parse_sector_override(raw: str | None):
    """Parse --sectors flag.

    Returns:
        None  — no override (use expander's pick)
        []    — explicit "no filter"
        [...] — concrete list to use (not yet canonicalized)
    """
    if raw is None:
        return None
    if raw.strip().lower() == "none":
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def format_result(result) -> str:
    """Format CaseSearchResult for terminal display."""
    lines: list[str] = []
    lines.append(f"\n{'=' * 60}")
    lines.append(f"Rounds: {result.rounds_used}")
    lines.append(f"Queries used: {len(result.queries_used)}")
    lines.append(f"Reranker results: {len(result.reranker_results)}")
    total_kept = sum(len(r.results) for r in result.reranker_results)
    total_dropped = sum(r.dropped_count for r in result.reranker_results)
    lines.append(f"Total kept: {total_kept}  |  Total dropped: {total_dropped}")
    lines.append(f"Expander prompt: {result.expander_prompt_key}")
    lines.append(f"{'=' * 60}\n")

    lines.append("Queries used:")
    for i, q in enumerate(result.queries_used, 1):
        lines.append(f"  {i}. {q}")
    lines.append("")

    if result.reranker_results:
        lines.append(f"Reranker summary ({len(result.reranker_results)} queries):")
        for i, rr in enumerate(result.reranker_results, 1):
            suf = "sufficient" if rr.sufficient else "insufficient"
            lines.append(f"  {i}. [{suf}] kept={len(rr.results)} dropped={rr.dropped_count}")
            lines.append(f"     query: {rr.query[:80]}")
            if rr.summary_note:
                lines.append(f"     note: {rr.summary_note}")
        lines.append("")

    return "\n".join(lines)


def list_logs(limit: int = 20) -> list[str]:
    if not LOGS_DIR.exists():
        return []
    run_files = sorted(LOGS_DIR.glob("**/run.json"), reverse=True)
    return [str(f.parent.relative_to(LOGS_DIR)) for f in run_files[:limit]]


def read_log(log_id: str) -> dict:
    path = LOGS_DIR / log_id / "run.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    old_path = LOGS_DIR / f"{log_id}.json"
    if old_path.exists():
        return json.loads(old_path.read_text(encoding="utf-8"))
    return {"error": f"Log {log_id} not found"}


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CLI test runner for case_search agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            '  python -m agents.deep_search_v3.case_search.cli "سوابق قضائية في الفصل التعسفي"\n'
            '  python -m agents.deep_search_v3.case_search.cli --query-id 23 --model or-gemma-4-31b\n'
            "  python -m agents.deep_search_v3.case_search.cli --list-prompts\n"
            "  python -m agents.deep_search_v3.case_search.cli --list-models\n"
            "  python -m agents.deep_search_v3.case_search.cli --list-logs\n"
            "  python -m agents.deep_search_v3.case_search.cli --batch 1,5,9 --delay 5\n"
        ),
    )

    parser.add_argument(
        "query",
        nargs="?",
        default=None,
        help="Focus instruction / search query in Arabic",
    )
    parser.add_argument(
        "--query-id",
        type=int,
        default=None,
        help="Use a specific query ID from test_queries.json",
    )
    parser.add_argument(
        "--user-context",
        default="",
        help="User context string (Arabic)",
    )
    parser.add_argument(
        "--expander-prompt",
        default=DEFAULT_EXPANDER_PROMPT,
        choices=sorted(EXPANDER_PROMPTS.keys()),
        help=(
            f"Expander prompt variant (default: {DEFAULT_EXPANDER_PROMPT}). "
            "prompt_1: multi-axis fact description. "
            "prompt_2: judicial-principle reasoning (direct / step-back / decomposition)."
        ),
    )
    parser.add_argument(
        "--expand-only",
        action="store_true",
        help="Run expander + search only (no reranker). Prints queries and raw results.",
    )
    parser.add_argument(
        "--score-threshold",
        type=float,
        default=0.005,
        help="Minimum RRF score to include a result (default: 0.005)",
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
        help="Run expand-only on multiple query IDs. Comma-separated (e.g. --batch 5,9,14).",
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
        help="Reasoning effort for the expander agent",
    )
    parser.add_argument(
        "--model",
        choices=ALL_MODEL_CHOICES,
        default=None,
        help="Override the model for expander and reranker",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="Max concurrent search pipelines (default: 10)",
    )
    parser.add_argument(
        "--sectioned",
        action="store_true",
        help=(
            "Force the sectioned pipeline (per-channel search + RRF fusion). "
            "Also implicit when --expander-prompt is a sectioned prompt (e.g. prompt_3)."
        ),
    )
    parser.add_argument(
        "--channels",
        default=None,
        help=(
            "Restrict which channels the sectioned pipeline dispatches. "
            "Comma-separated subset of: principle,facts,basis. "
            "Drops any typed queries whose channel is not in the set."
        ),
    )
    parser.add_argument(
        "--sectors",
        default=None,
        help=(
            "Override the expander's legal_sectors pick. Comma-separated sector "
            "names from case_search/sector_vocab.VALID_SECTORS. Use 'none' to "
            "force no filter."
        ),
    )
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="Print available models, then exit",
    )
    parser.add_argument(
        "--list-prompts",
        action="store_true",
        help="Print available prompt keys for expander and reranker, then exit",
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
# Expand-only: expander + search, no reranker
# ---------------------------------------------------------------------------

async def run_expand_only(args, query: str, deps) -> None:
    """Run expander agent + search pipeline. Skip reranker."""
    import time

    from .expander import EXPANDER_LIMITS, create_expander_agent
    from .prompts import build_expander_user_message, get_expander_prompt
    from .search import search_cases_pipeline

    model_id = _get_expander_model_id(args.model)
    print(f"[expand-only] Expander prompt: {args.expander_prompt}")
    print(f"[expand-only] Model: {model_id}")
    if args.thinking:
        print(f"[expand-only] Thinking: {args.thinking}")
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

    eu = result.usage()
    print(f"[expand-only] Expander usage: {eu.input_tokens} in / {eu.output_tokens} out / {eu.requests} req")
    print(f"\n[expand-only] Running search for {len(output.queries)} queries...")

    from agents.utils.embeddings import embed_regulation_queries_alibaba
    embeddings = await embed_regulation_queries_alibaba(output.queries)

    t1 = time.perf_counter()
    sem = asyncio.Semaphore(args.concurrency)
    tasks = [
        search_cases_pipeline(q, deps, precomputed_embedding=emb, semaphore=sem)
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

    from .logger import (
        create_run_dir,
        make_log_id,
        save_expander_md,
        save_run_json,
        save_run_overview_md,
        save_search_query_md,
    )
    from .models import CaseSearchResult

    log_id = make_log_id(deps._query_id) + "_expand" if deps._query_id else datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_expand"
    create_run_dir(log_id)

    save_expander_md(
        log_id=log_id, round_num=1, prompt_key=args.expander_prompt,
        system_prompt=get_expander_prompt(args.expander_prompt),
        user_message=user_message, output=output,
        usage=eu, messages_json=result.all_messages_json(),
    )

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
            "result_count": count, "raw_markdown_length": len(raw_md),
            "raw_markdown": raw_md,
        })

    pending_result = CaseSearchResult(
        reranker_results=[],
        queries_used=list(output.queries),
        rounds_used=1,
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
        model_name=model_id,
        thinking_effort=args.thinking,
    )
    print(f"\nLog dir: agents/deep_search_v3/case_search/reports/{log_id}/")


# ---------------------------------------------------------------------------
# Batch mode
# ---------------------------------------------------------------------------

async def run_batch(args) -> None:
    """Run expand-only on multiple query IDs sequentially."""
    import time

    ids = [int(x.strip()) for x in args.batch.split(",")]
    print(f"[batch] Running {len(ids)} queries: {ids}")
    print(f"[batch] Delay between queries: {args.delay}s")
    print(f"[batch] Expander prompt: {args.expander_prompt}")
    if args.model:
        print(f"[batch] Model: {args.model}")
    print()

    from agents.utils.embeddings import embed_regulation_query
    from shared.db.client import get_supabase_client

    from .models import CaseSearchDeps

    supabase = get_supabase_client()

    for i, qid in enumerate(ids):
        if i > 0:
            print(f"\n[batch] Waiting {args.delay}s...")
            await asyncio.sleep(args.delay)

        from .logger import resolve_query_id
        try:
            query_id, query = resolve_query_id(None, qid)
        except ValueError as e:
            print(f"[batch] Skip query {qid}: {e}")
            continue

        print(f"\n{'=' * 60}")
        print(f"[batch] Query {i + 1}/{len(ids)} -- ID {query_id}: {query[:80]}...")

        deps = CaseSearchDeps(
            supabase=supabase,
            embedding_fn=embed_regulation_query,
            score_threshold=args.score_threshold,
            _query_id=query_id,
        )

        try:
            await run_expand_only(args, query, deps)
        except Exception as e:
            print(f"[batch] Error on query {qid}: {e}")

    print(f"\n[batch] Done -- {len(ids)} queries processed.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.list_prompts:
        from .prompts import EXPANDER_PROMPTS, RERANKER_PROMPTS

        print("\nExpander prompts:")
        for key in sorted(EXPANDER_PROMPTS.keys()):
            preview = EXPANDER_PROMPTS[key][:80].replace("\n", " ")
            print(f"  {key}: {preview}...")

        print("\nReranker prompts:")
        for key in sorted(RERANKER_PROMPTS.keys()):
            preview = RERANKER_PROMPTS[key][:80].replace("\n", " ")
            print(f"  {key}: {preview}...")
        return

    if args.list_models:
        from agents.model_registry import MODEL_REGISTRY

        print("\nAvailable models:")
        print(f"\n{'Model Key':<25} {'Provider':<12} {'Model ID'}")
        print(f"{'=' * 25} {'=' * 12} {'=' * 40}")
        for key in ALL_MODEL_CHOICES:
            config = MODEL_REGISTRY.get(key)
            if config:
                print(f"  {key:<23} {config.provider:<10} {config.model_id}")
            else:
                print(f"  {key:<23} (not found)")
        return

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
                rounds = log.get("result", {}).get("rounds", "?")
                total_kept = log.get("result", {}).get("total_kept", "?")
                focus = log.get("input", {}).get("focus_instruction", "")[:60]
                exp_key = log.get("prompt_keys", {}).get("expander", "?")
                model = log.get("model", "?")
                print(
                    f"  {log_id}  [{status}]  {duration}s  "
                    f"rounds={rounds}  kept={total_kept}  "
                    f"model={model}  expander={exp_key}  {focus}..."
                )
        return

    if args.read_log:
        log = read_log(args.read_log)
        print(json.dumps(log, ensure_ascii=False, indent=2))
        return

    if args.batch:
        await run_batch(args)
        return

    from .logger import resolve_query_id

    query_id, query = resolve_query_id(args.query, args.query_id)
    print(f"[query_{query_id}] {query[:100]}...")

    from agents.utils.embeddings import embed_regulation_query
    from shared.db.client import get_supabase_client

    from .models import CaseSearchDeps

    supabase = get_supabase_client()

    mock_results = None
    if args.mock:
        mock_results = {
            "cases": (
                "# نتائج وهمية للاختبار\n\n"
                "## حكم محكمة الاستئناف العمالية\n"
                "قضت المحكمة بإلزام صاحب العمل بدفع تعويض...\n\n"
                "## حكم المحكمة العمالية الابتدائية\n"
                "ثبت للمحكمة أن الفصل كان تعسفياً...\n"
            ),
        }

    deps = CaseSearchDeps(
        supabase=supabase,
        embedding_fn=embed_regulation_query,
        score_threshold=args.score_threshold,
        mock_results=mock_results,
        _query_id=query_id,
    )

    if args.expand_only:
        await run_expand_only(args, query, deps)
        return

    import time

    model_id = _get_expander_model_id(args.model)

    print(f"\nRunning case_search...")
    print(f"Query: {query[:100]}...")
    print(f"Model: {model_id}")
    print(f"Expander prompt: {args.expander_prompt}")
    if args.thinking:
        print(f"Thinking: {args.thinking}")
    print(f"Score threshold: {args.score_threshold}")
    print(f"Concurrency: {args.concurrency}")
    print("Please wait...\n")

    from .loop import run_case_search
    from .prompts import is_sectioned_prompt

    sectioned_flag: bool | None = True if args.sectioned else None
    # Let the prompt key auto-opt-in too
    use_sectioned = args.sectioned or is_sectioned_prompt(args.expander_prompt)
    if use_sectioned:
        print(f"Pipeline: sectioned (per-channel + RRF fusion)")
        if args.channels:
            print(f"Channel filter: {args.channels}")
        if args.sectors:
            print(f"Sector override: {args.sectors}")

    # Attach CLI overrides to deps so the sectioned nodes can pick them up.
    # Keeps run_case_search's signature stable — overrides travel via deps.
    if use_sectioned:
        deps.cli_channels = _parse_channels(args.channels)
        deps.cli_sectors = _parse_sector_override(args.sectors)

    result = await run_case_search(
        focus_instruction=query,
        user_context=args.user_context,
        deps=deps,
        expander_prompt_key=args.expander_prompt,
        thinking_effort=args.thinking,
        model_override=args.model,
        concurrency=args.concurrency,
        sectioned=sectioned_flag,
    )

    print(format_result(result))

    if args.verbose:
        print(f"\n{'~' * 40}")
        print(f"Events collected: {len(deps._events)}")
        for e in deps._events:
            print(f"  [{e.get('type', '?')}] {json.dumps(e, ensure_ascii=False)[:120]}")

    log_id = deps._log_id
    print(f"\nLog dir: agents/deep_search_v3/case_search/reports/{log_id}/")


if __name__ == "__main__":
    asyncio.run(main())
