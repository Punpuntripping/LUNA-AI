"""CLI for the aggregator (synthesizer) agent.

Unlike reg_search's CLI, this agent doesn't do expansion or retrieval — it
consumes reranker output from a previous reg_search run and produces a cited
Arabic synthesis. So every invocation needs a **source run** to load from.

Usage:
    # Replay a specific query (picks the latest timestamp automatically)
    python -m agents.deep_search_v3.aggregator.cli --query-id 19

    # Replay a specific (query, timestamp) pair
    python -m agents.deep_search_v3.aggregator.cli --query-id 19 --timestamp 20260416_144941

    # Pick a different prompt variant
    python -m agents.deep_search_v3.aggregator.cli --query-id 19 --prompt prompt_2

    # Force the Draft-Critique-Rewrite chain
    python -m agents.deep_search_v3.aggregator.cli --query-id 19 --dcr

    # Override models (defaults: qwen3.6-plus primary, gemini-3-flash fallback)
    python -m agents.deep_search_v3.aggregator.cli --query-id 19 --primary qwen3.5-plus

    # Parse only (no LLM call) — useful for sanity checking
    python -m agents.deep_search_v3.aggregator.cli --query-id 19 --dry-run

    # Run against the golden set (5, 12, 14, 19, 27) with prompt_1
    python -m agents.deep_search_v3.aggregator.cli --golden

    # Run against every logged query
    python -m agents.deep_search_v3.aggregator.cli --all

    # Utilities
    python -m agents.deep_search_v3.aggregator.cli --list-prompts
    python -m agents.deep_search_v3.aggregator.cli --list-models
    python -m agents.deep_search_v3.aggregator.cli --list-logs
    python -m agents.deep_search_v3.aggregator.cli --read-log query_19/20260416_144941

Aggregator reports are written to: agents/deep_search_v3/aggregator/reports/query_{N}/{TIMESTAMP}/
Source reranker reports are read from: agents/deep_search_v3/reg_search/reports/query_{N}/{TIMESTAMP}/
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .log_parser import discover_runs, load_aggregator_input_from_run
from .logger import AggregatorLogger, DEFAULT_BASE_LOGS_DIR, sanitize_variant
from .prompts import AGGREGATOR_PROMPTS


# Source of reranker runs this CLI reads from.
SOURCE_LOGS_DIR = (
    Path(__file__).resolve().parent.parent / "reg_search" / "reports"
)

# Default 5 golden queries (per the plan).
GOLDEN_QUERY_IDS = [5, 12, 14, 19, 27]

# Available model choices (same keys as reg_search CLI).
OR_MODEL_CHOICES = [
    "or-minimax-m2.7", "or-gemini-3.1-pro", "or-gemini-3.1-pro-tools",
    "or-gemini-2.5-pro", "or-gemini-2.5-flash", "or-deepseek-chat",
    "or-qwen3.5-397b", "or-deepseek-v3.2", "or-mimo-v2-pro",
    "or-glm-5-turbo", "or-gemma-4-31b",
]
ALIBABA_MODEL_CHOICES = [
    "qwen3.6-plus", "qwen3.5-plus", "qwen3.5-flash", "qwen3-max",
    "qwen3-coder-plus", "qwen3-coder-flash", "qwen3-vl-plus",
    "qwen3-vl-flash", "qwq-plus", "qvq-max", "qwen-plus",
    "qwen-long", "qwen-vl-ocr",
]
GOOGLE_MODEL_CHOICES = [
    "gemini-3.1-pro", "gemini-3-flash", "gemini-3.1-flash-lite",
    "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite",
]
ALL_MODEL_CHOICES = OR_MODEL_CHOICES + ALIBABA_MODEL_CHOICES + GOOGLE_MODEL_CHOICES


# ---------------------------------------------------------------------------
# Listing / reading helpers
# ---------------------------------------------------------------------------


def list_aggregator_logs(limit: int = 20) -> list[tuple[int, str]]:
    """Return recent aggregator runs as [(query_id, timestamp), ...] sorted new→old."""
    if not DEFAULT_BASE_LOGS_DIR.exists():
        return []
    out: list[tuple[int, str]] = []
    for qdir in DEFAULT_BASE_LOGS_DIR.glob("query_*"):
        try:
            qid = int(qdir.name.split("_", 1)[1])
        except (IndexError, ValueError):
            continue
        for ts_dir in qdir.iterdir():
            if ts_dir.is_dir() and (ts_dir / "run.md").exists():
                out.append((qid, ts_dir.name))
    out.sort(key=lambda t: t[1], reverse=True)
    return out[:limit]


def read_aggregator_log(log_id: str) -> dict:
    """Read a run's validation + metadata. log_id form: 'query_N/TIMESTAMP'."""
    run_dir = DEFAULT_BASE_LOGS_DIR / log_id
    if not run_dir.exists():
        return {"error": f"Log {log_id} not found at {run_dir}"}
    result: dict = {"log_id": log_id, "files": sorted(p.name for p in run_dir.iterdir())}
    val_path = run_dir / "validation.json"
    if val_path.exists():
        result["validation"] = json.loads(val_path.read_text(encoding="utf-8"))
    run_md = run_dir / "run.md"
    if run_md.exists():
        result["run_md"] = run_md.read_text(encoding="utf-8")
    return result


def _find_run_dir(query_id: int, timestamp: str | None) -> Path | None:
    """Resolve (query_id, optional timestamp) to an absolute reranker run dir."""
    q_dir = SOURCE_LOGS_DIR / f"query_{query_id}"
    if not q_dir.exists():
        return None
    if timestamp:
        cand = q_dir / timestamp
        return cand if cand.exists() else None
    # No explicit timestamp — pick the latest one that has a reranker/ subdir.
    candidates = [
        p for p in q_dir.iterdir()
        if p.is_dir() and (p / "reranker").exists() and not p.name.endswith("_expand")
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.name, reverse=True)
    return candidates[0]


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------


async def _run_one(
    query_id: int,
    run_dir: Path,
    args: argparse.Namespace,
    primary: str | None = None,
    prompt: str | None = None,
    variant: str | None = None,
) -> dict:
    """Parse a reranker run and (unless --dry-run) call the aggregator.

    When `primary`/`prompt`/`variant` are supplied, they override the
    corresponding args fields for this call — used by matrix mode.
    """
    from .deps import build_aggregator_deps
    from .runner import handle_aggregator_turn

    prompt_key = prompt or args.prompt
    primary_model = primary or args.primary
    # Fallback selection: in matrix mode we want each cell to stand on its own,
    # so fallback_model == primary_model (a soft retry) unless the user gave
    # an explicit --fallback.
    fallback_model = args.fallback or primary_model

    agg_input = load_aggregator_input_from_run(run_dir, query_id=query_id)
    agg_input.prompt_key = prompt_key
    agg_input.enable_dcr = bool(args.dcr) or prompt_key.startswith("prompt_3")

    header = (
        f"query_{query_id} / {run_dir.name}  "
        f"{len(agg_input.sub_queries)} sub-queries, "
        f"prompt={prompt_key}  model={primary_model or '(default)'}"
    )
    print(header)
    if args.verbose:
        print(f"  original: {agg_input.original_query[:90]}…")

    if args.dry_run:
        from .preprocessor import preprocess_references
        refs, _ = preprocess_references(agg_input)
        print(f"  dry-run: {len(refs)} unique refs after dedup")
        return {
            "query_id": query_id, "timestamp": run_dir.name,
            "status": "dry", "variant": variant,
            "prompt": prompt_key, "model": primary_model,
        }

    import time
    agg_logger = AggregatorLogger(
        query_id=query_id,
        log_id=run_dir.name,
        variant=variant,
    )
    deps = build_aggregator_deps(
        primary_model=primary_model,
        fallback_model=fallback_model,
        temperature=args.temperature,
        build_artifact=not args.no_artifact,
        logger=agg_logger,
    )

    t0 = time.monotonic()
    try:
        output = await handle_aggregator_turn(agg_input, deps)
    except Exception as exc:  # noqa: BLE001
        duration = time.monotonic() - t0
        print(f"  EXCEPTION: {exc!r}  duration={duration:.1f}s")
        return {
            "query_id": query_id, "timestamp": run_dir.name,
            "status": "EXCEPTION", "duration": duration,
            "variant": variant, "prompt": prompt_key, "model": primary_model,
            "error": repr(exc)[:200],
        }
    duration = time.monotonic() - t0

    validation = output.validation
    passed = bool(validation.passed) if validation else False
    cited = len(validation.cited_numbers) if validation else 0
    status = "PASS" if passed else "FAIL"
    fallback_fired = (
        output.model_used and primary_model
        and output.model_used != primary_model
    )
    flags = "  [FALLBACK]" if fallback_fired else ""
    print(
        f"  {status}  refs={len(output.references)} cited={cited} "
        f"model={output.model_used} confidence={output.confidence} "
        f"duration={duration:.1f}s{flags}"
    )

    return {
        "query_id": query_id,
        "timestamp": run_dir.name,
        "status": status,
        "duration": duration,
        "model": output.model_used,
        "configured_model": primary_model,
        "prompt": prompt_key,
        "variant": variant,
        "cited": cited,
        "refs": len(output.references),
        "confidence": output.confidence,
        "fallback_fired": bool(fallback_fired),
        "synthesis_preview": (output.synthesis_md or "")[:500],
        "gaps_count": len(output.gaps or []),
    }


def _write_comparison_report(
    query_id: int,
    run_dir: Path,
    results: list[dict],
) -> Path:
    """Write a `_comparison.md` under the aggregator run dir summarizing
    every variant side-by-side. Safe to re-run — overwrites in place."""
    comparison_dir = DEFAULT_BASE_LOGS_DIR / f"query_{query_id}" / run_dir.name
    comparison_dir.mkdir(parents=True, exist_ok=True)
    path = comparison_dir / "_comparison.md"

    lines: list[str] = []
    lines.append(f"# A/B comparison — query_{query_id} / {run_dir.name}")
    lines.append("")
    lines.append(f"Source reranker: `{run_dir}`")
    lines.append(f"Variants run: {len(results)}")
    lines.append("")

    lines.append("## Summary table")
    lines.append("")
    lines.append("| Variant | Status | Model | Cited | Refs | Confidence | Gaps | Duration | Fallback |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in results:
        status = r.get("status", "?")
        model = r.get("model") or r.get("configured_model") or "?"
        dur = r.get("duration", 0.0)
        fb = "yes" if r.get("fallback_fired") else "no"
        lines.append(
            f"| {r.get('variant', '?')} "
            f"| {status} | {model} | {r.get('cited', 0)} "
            f"| {r.get('refs', 0)} | {r.get('confidence', '?')} "
            f"| {r.get('gaps_count', 0)} | {dur:.1f}s | {fb} |"
        )
    lines.append("")

    # Per-variant synthesis preview + link to the full file
    for r in results:
        v = r.get("variant", "?")
        lines.append(f"## {v}")
        lines.append("")
        lines.append(f"- status: {r.get('status', '?')}")
        lines.append(f"- model: {r.get('model') or r.get('configured_model')}")
        lines.append(f"- prompt: {r.get('prompt', '?')}")
        lines.append(f"- cited / refs: {r.get('cited', 0)} / {r.get('refs', 0)}")
        lines.append(f"- confidence: {r.get('confidence', '?')}  gaps: {r.get('gaps_count', 0)}")
        lines.append(f"- duration: {r.get('duration', 0):.1f}s")
        if r.get("fallback_fired"):
            lines.append(f"- fallback fired: model_used={r.get('model')}")
        lines.append(f"- full: [{v}/synthesis.md]({v}/synthesis.md)")
        lines.append("")
        preview = (r.get("synthesis_preview") or "").strip()
        if preview:
            lines.append("### Synthesis preview (first 500 chars)")
            lines.append("")
            lines.append("```")
            lines.append(preview)
            lines.append("```")
            lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


async def _run_matrix(
    query_ids: list[int],
    models: list[str],
    prompts: list[str],
    args: argparse.Namespace,
    concurrency: int = 4,
) -> None:
    """Run the cartesian product (models × prompts) against each query's reranker snapshot.

    Concurrency is bounded per query — we kick off up to `concurrency` variant
    runs in parallel for a single query, then move to the next query. This
    keeps API rate-limit pressure predictable.
    """
    combos = [(m, p) for m in models for p in prompts]
    print(f"Matrix: {len(combos)} variants per query × {len(query_ids)} queries = "
          f"{len(combos) * len(query_ids)} runs (concurrency={concurrency})")
    print(f"Models:  {models}")
    print(f"Prompts: {prompts}")
    print()

    grand_stats: list[dict] = []

    for qid in query_ids:
        run_dir = _find_run_dir(qid, args.timestamp)
        if run_dir is None:
            print(f"query_{qid}: no reranker run found — skipping")
            continue
        print(f"=== query_{qid} / {run_dir.name} ({len(combos)} variants) ===")

        sem = asyncio.Semaphore(concurrency)

        async def _run_variant(model: str, prompt: str) -> dict:
            async with sem:
                variant = sanitize_variant(f"{model}__{prompt}")
                return await _run_one(
                    query_id=qid, run_dir=run_dir, args=args,
                    primary=model, prompt=prompt, variant=variant,
                )

        tasks = [_run_variant(m, p) for (m, p) in combos]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        if not args.dry_run:
            report_path = _write_comparison_report(qid, run_dir, results)
            print(f"  comparison: {report_path}")
        grand_stats.extend(results)
        print()

    if args.dry_run or not grand_stats:
        return

    # Grand-totals across all queries
    passed = sum(1 for s in grand_stats if s.get("status") == "PASS")
    failed = sum(1 for s in grand_stats if s.get("status") == "FAIL")
    excs = sum(1 for s in grand_stats if s.get("status") == "EXCEPTION")
    fallbacks = sum(1 for s in grand_stats if s.get("fallback_fired"))
    durations = [s["duration"] for s in grand_stats if "duration" in s]
    print(f"--- MATRIX SUMMARY ---")
    print(f"runs:     {len(grand_stats)}")
    print(f"pass:     {passed}")
    print(f"fail:     {failed}")
    print(f"errors:   {excs}")
    print(f"fallbacks:{fallbacks}")
    if durations:
        print(f"avg dur:  {sum(durations)/len(durations):.1f}s")


async def _run_many(query_ids: list[int], args: argparse.Namespace) -> None:
    """Replay a list of query IDs sequentially (one LLM call at a time).

    We don't parallelize here to keep API-rate-limit impact predictable; use
    the background-task harness (replay.py) if you want parallel runs.
    """
    stats: list[dict] = []
    for qid in query_ids:
        run_dir = _find_run_dir(qid, args.timestamp)
        if run_dir is None:
            print(f"query_{qid}: no reranker run found — skipping")
            continue
        try:
            stats.append(await _run_one(qid, run_dir, args))
        except Exception as exc:  # noqa: BLE001
            print(f"query_{qid}: EXCEPTION {exc!r}")

    if not stats or args.dry_run:
        return

    passed = sum(1 for s in stats if s.get("status") == "PASS")
    failed = sum(1 for s in stats if s.get("status") == "FAIL")
    durations = [s["duration"] for s in stats if "duration" in s]
    print(f"\n--- summary ---")
    print(f"runs:   {len(stats)}")
    print(f"pass:   {passed}")
    print(f"fail:   {failed}")
    if durations:
        print(f"avg duration: {sum(durations)/len(durations):.1f}s")


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CLI for the Luna aggregator/synthesizer agent.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python -m agents.deep_search_v3.aggregator.cli --query-id 19\n"
            "  python -m agents.deep_search_v3.aggregator.cli --query-id 19 --prompt prompt_2\n"
            "  python -m agents.deep_search_v3.aggregator.cli --query-id 19 --dcr\n"
            "  python -m agents.deep_search_v3.aggregator.cli --golden\n"
            "  python -m agents.deep_search_v3.aggregator.cli --list-logs\n"
            "  python -m agents.deep_search_v3.aggregator.cli --read-log query_19/20260416_144941\n"
        ),
    )

    # Source selection (mutually exclusive ways to pick runs to replay)
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--query-id", type=int, default=None,
                     help="Replay this query's latest reranker run (or --timestamp).")
    src.add_argument("--golden", action="store_true",
                     help=f"Replay the golden set {GOLDEN_QUERY_IDS}.")
    src.add_argument("--all", action="store_true",
                     help="Replay every query with a logged reranker run.")

    parser.add_argument("--timestamp", default=None,
                        help="Specific TIMESTAMP dir (default: latest).")
    parser.add_argument("--prompt", default="prompt_1",
                        choices=sorted(AGGREGATOR_PROMPTS.keys()),
                        help="Aggregator prompt variant (default: prompt_1 = CRAC).")
    parser.add_argument("--dcr", action="store_true",
                        help="Force Draft-Critique-Rewrite chain (3 LLM calls).")
    parser.add_argument("--primary", default=None, choices=ALL_MODEL_CHOICES,
                        help="Primary model (default: qwen3.6-plus).")
    parser.add_argument("--fallback", default=None, choices=ALL_MODEL_CHOICES,
                        help="Fallback model on validation failure (default: gemini-3-flash).")
    parser.add_argument("--temperature", type=float, default=None,
                        help="Override LLM temperature (default: 0.2).")
    parser.add_argument("--no-artifact", action="store_true",
                        help="Skip frontend artifact building.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse + preprocess only; no LLM call, no logs written.")
    parser.add_argument("--verbose", action="store_true",
                        help="Extra debug output.")

    # Matrix mode — cartesian product of models × prompts on every selected query.
    parser.add_argument(
        "--matrix",
        action="store_true",
        help="Run cartesian(--models, --prompts) against each selected query. "
             "Writes one sub-folder per (model, prompt) under the reranker timestamp "
             "dir and a _comparison.md side-by-side report.",
    )
    parser.add_argument(
        "--models",
        default=None,
        help="Comma-separated model keys for --matrix. Default: qwen3.6-plus,gemini-3-flash",
    )
    parser.add_argument(
        "--prompts",
        default=None,
        help="Comma-separated prompt keys for --matrix. Default: prompt_1,prompt_2,prompt_3,prompt_4",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Max concurrent variant runs per query in --matrix mode (default: 4).",
    )
    parser.add_argument(
        "--variant",
        default=None,
        help="Explicit variant sub-folder name for a single-run call (bypasses auto-naming).",
    )

    # Utilities
    parser.add_argument("--list-prompts", action="store_true",
                        help="List prompt variants and exit.")
    parser.add_argument("--list-models", action="store_true",
                        help="List available model keys and exit.")
    parser.add_argument("--list-logs", action="store_true",
                        help="List recent aggregator logs and exit.")
    parser.add_argument("--read-log", metavar="LOG_ID", default=None,
                        help="Read a specific log (format: query_N/TIMESTAMP).")

    return parser


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # -- Utilities --
    if args.list_prompts:
        print("\nAggregator prompts:")
        for key in sorted(AGGREGATOR_PROMPTS.keys()):
            preview = AGGREGATOR_PROMPTS[key][:90].replace("\n", " ")
            print(f"  {key}: {preview}…")
        return

    if args.list_models:
        from agents.model_registry import MODEL_REGISTRY
        from .deps import PRIMARY_MODEL_DEFAULT, FALLBACK_MODEL_DEFAULT
        print(f"\nDefaults: primary={PRIMARY_MODEL_DEFAULT}, fallback={FALLBACK_MODEL_DEFAULT}")
        for label, choices in [
            ("Alibaba / Qwen", ALIBABA_MODEL_CHOICES),
            ("Google", GOOGLE_MODEL_CHOICES),
            ("OpenRouter", OR_MODEL_CHOICES),
        ]:
            print(f"\n{label}:")
            print(f"  {'Key':<28} {'Model ID':<45} {'In':>6} {'Out':>6}")
            for key in choices:
                cfg = MODEL_REGISTRY.get(key)
                if not cfg:
                    continue
                inp = f"${cfg.input_price:.2f}" if cfg.input_price else "?"
                out = f"${cfg.output_price:.2f}" if cfg.output_price else "?"
                print(f"  {key:<28} {cfg.model_id:<45} {inp:>6} {out:>6}")
        return

    if args.list_logs:
        rows = list_aggregator_logs()
        if not rows:
            print("No aggregator logs yet.")
            return
        print(f"\nRecent aggregator runs ({len(rows)}):")
        for qid, ts in rows:
            log = read_aggregator_log(f"query_{qid}/{ts}")
            v = (log.get("validation") or {}).get("report") or {}
            prompt_key = (log.get("validation") or {}).get("prompt_key", "?")
            model = (log.get("validation") or {}).get("model_used", "?")
            passed = "PASS" if v.get("passed") else "FAIL" if v else "?"
            cited = len(v.get("cited_numbers") or [])
            print(
                f"  query_{qid}/{ts}  [{passed}]  prompt={prompt_key}  "
                f"model={model}  cited={cited}"
            )
        return

    if args.read_log:
        log = read_aggregator_log(args.read_log)
        if "error" in log:
            print(log["error"])
            sys.exit(1)
        if "run_md" in log:
            print(log["run_md"])
            print("\n--- validation.json ---")
        if "validation" in log:
            print(json.dumps(log["validation"], ensure_ascii=False, indent=2))
        return

    # -- Resolve the set of query IDs to run over --
    if args.all:
        pairs = [(qid, rd) for qid, rd in discover_runs(SOURCE_LOGS_DIR)]
        target_ids = sorted({qid for qid, _ in pairs})
        if not target_ids:
            print("No reranker runs found — nothing to replay.")
            return
    elif args.golden:
        target_ids = GOLDEN_QUERY_IDS
    elif args.query_id is not None:
        target_ids = [args.query_id]
    else:
        parser.print_help()
        print("\nError: supply --query-id N, --golden, or --all.")
        sys.exit(2)

    if args.matrix:
        models = (
            [m.strip() for m in args.models.split(",") if m.strip()]
            if args.models else ["qwen3.6-plus", "gemini-3-flash"]
        )
        prompts = (
            [p.strip() for p in args.prompts.split(",") if p.strip()]
            if args.prompts else ["prompt_1", "prompt_2", "prompt_3", "prompt_4"]
        )
        # prompt_3 → DCR chain (draft+critique+rewrite under the hood).
        # We normalize it to prompt_3_rewrite so the agg_input dispatches
        # through _run_dcr_chain.
        prompts = [
            ("prompt_3_rewrite" if p == "prompt_3" else p)
            for p in prompts
        ]
        await _run_matrix(
            query_ids=target_ids,
            models=models,
            prompts=prompts,
            args=args,
            concurrency=args.concurrency,
        )
        return

    await _run_many(target_ids, args)


if __name__ == "__main__":
    asyncio.run(main())
