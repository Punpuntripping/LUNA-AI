"""Replay reg_search logs against the aggregator — test harness / CLI.

Usage:
    python -m agents.deep_search_v4.aggregator.replay --all
    python -m agents.deep_search_v4.aggregator.replay --query 19
    python -m agents.deep_search_v4.aggregator.replay --query 19 --prompt prompt_2
    python -m agents.deep_search_v4.aggregator.replay --golden-only
    python -m agents.deep_search_v4.aggregator.replay --query 19 --dry-run
    python -m agents.deep_search_v4.aggregator.replay --limit 3

No upstream API cost — we reuse the already-logged reranker output and just
invoke the aggregator against it.
"""
from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path
from typing import Any

from agents.deep_search_v4.aggregator.log_parser import (
    discover_runs,
    load_aggregator_input_from_run,
)
from agents.deep_search_v4.aggregator.models import AggregatorInput


# Golden-query set — the curated evaluation slice.
GOLDEN_QUERIES = {5, 12, 14, 19, 27}


# Default logs root; resolved relative to this file so it works regardless
# of the cwd a user happens to invoke -m from.
_THIS_DIR = Path(__file__).resolve().parent
DEFAULT_LOGS_DIR = (_THIS_DIR.parent / "reg_search" / "reports").resolve()


# ---------------------------------------------------------------------------
# Lazy aggregator import — may not exist yet while the agent is being built
# in parallel. Falls back cleanly for --dry-run.
# ---------------------------------------------------------------------------


def _try_import_runner():
    try:
        from agents.deep_search_v4.aggregator import handle_aggregator_turn  # type: ignore
        from agents.deep_search_v4.aggregator import build_aggregator_deps  # type: ignore
    except ImportError:
        return None, None
    if handle_aggregator_turn is None:
        return None, build_aggregator_deps
    return handle_aggregator_turn, build_aggregator_deps


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------


def _count_refs(agg_input: AggregatorInput) -> int:
    return sum(len(sq.results) for sq in agg_input.sub_queries)


def _truncate(s: str, n: int = 60) -> str:
    s = s.replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _print_dry_run_line(query_id: int, timestamp: str, agg_input: AggregatorInput) -> None:
    print(
        f"query_{query_id}  {timestamp}  "
        f"{len(agg_input.sub_queries)} sub-queries, "
        f"{_count_refs(agg_input)} refs, "
        f'orig: "{_truncate(agg_input.original_query)}"'
    )


# ---------------------------------------------------------------------------
# Single-run executor
# ---------------------------------------------------------------------------


async def _run_one(
    query_id: int,
    run_dir: Path,
    prompt_key: str | None,
    dry_run: bool,
    runner: Any,
    deps_builder: Any,
) -> dict:
    """Replay a single run. Returns a summary dict for aggregate stats."""
    agg_input = load_aggregator_input_from_run(run_dir, query_id=query_id)
    if prompt_key:
        agg_input.prompt_key = prompt_key

    if dry_run:
        _print_dry_run_line(query_id, run_dir.name, agg_input)
        return {
            "query_id": query_id,
            "timestamp": run_dir.name,
            "refs": _count_refs(agg_input),
            "sub_queries": len(agg_input.sub_queries),
            "status": "dry",
            "duration": 0.0,
        }

    if runner is None:
        # No runner yet — degrade to parse-only.
        _print_dry_run_line(query_id, run_dir.name, agg_input)
        return {
            "query_id": query_id,
            "timestamp": run_dir.name,
            "refs": _count_refs(agg_input),
            "sub_queries": len(agg_input.sub_queries),
            "status": "parse_only",
            "duration": 0.0,
        }

    # Build deps + call runner. handle_aggregator_turn is expected to be async.
    from agents.deep_search_v4.aggregator.logger import AggregatorLogger

    t0 = time.monotonic()
    status = "FAIL"
    raw_refs = _count_refs(agg_input)
    unique_refs = 0
    cited_count = 0
    confidence = "?"
    validation_passed = False
    try:
        agg_logger = AggregatorLogger(query_id=query_id, log_id=run_dir.name)
        deps = deps_builder(logger=agg_logger) if deps_builder else None
        output = await runner(agg_input, deps)
        unique_refs = len(getattr(output, "references", []) or [])
        validation = getattr(output, "validation", None)
        if validation is not None:
            cited_count = len(getattr(validation, "cited_numbers", []) or [])
            validation_passed = bool(getattr(validation, "passed", False))
        confidence = getattr(output, "confidence", "?")
        status = "PASS" if validation_passed else "FAIL(validation)"
    except Exception as e:
        status = f"FAIL ({type(e).__name__}: {_truncate(str(e), 40)})"

    duration = time.monotonic() - t0
    print(
        f"query_{query_id}  {run_dir.name}  {status}  "
        f"raw={raw_refs} refs={unique_refs} cited={cited_count} "
        f"confidence={confidence} duration={duration:.1f}s"
    )
    return {
        "query_id": query_id,
        "timestamp": run_dir.name,
        "refs": unique_refs,
        "raw": raw_refs,
        "cited": cited_count,
        "status": status,
        "duration": duration,
    }


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agents.deep_search_v4.aggregator.replay",
        description="Replay reg_search logs against the aggregator agent.",
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument("--all", action="store_true", help="Replay every run.")
    g.add_argument("--query", type=int, help="Replay only query_N.")
    g.add_argument(
        "--golden-only",
        action="store_true",
        help=f"Replay only golden queries {sorted(GOLDEN_QUERIES)}.",
    )
    p.add_argument(
        "--prompt",
        default=None,
        help="Override aggregator prompt_key (e.g. prompt_2).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse inputs and print summary only; do not call the LLM.",
    )
    p.add_argument("--limit", type=int, default=None, help="Stop after N runs.")
    p.add_argument(
        "--logs-dir",
        type=Path,
        default=DEFAULT_LOGS_DIR,
        help=f"Root logs dir (default: {DEFAULT_LOGS_DIR}).",
    )
    return p


def _select_runs(
    logs_dir: Path,
    args: argparse.Namespace,
) -> list[tuple[int, Path]]:
    if args.query is not None:
        runs = discover_runs(logs_dir, query_filter=args.query)
    else:
        runs = discover_runs(logs_dir)
        if args.golden_only:
            runs = [(q, d) for q, d in runs if q in GOLDEN_QUERIES]
    # --all is effectively the default when no filter is set; treat absence
    # of any filter as "all runs" for ergonomics.
    if args.limit is not None:
        runs = runs[: args.limit]
    return runs


async def _run_async(args: argparse.Namespace) -> int:
    runs = _select_runs(args.logs_dir, args)
    if not runs:
        print(f"No runs found under {args.logs_dir} matching filter.")
        return 1

    runner, deps_builder = _try_import_runner()
    if runner is None and not args.dry_run:
        print("aggregator.runner not yet available — parsing only")

    summaries: list[dict] = []
    for query_id, run_dir in runs:
        s = await _run_one(
            query_id=query_id,
            run_dir=run_dir,
            prompt_key=args.prompt,
            dry_run=args.dry_run,
            runner=runner,
            deps_builder=deps_builder,
        )
        summaries.append(s)

    # Aggregate stats
    total = len(summaries)
    passed = sum(1 for s in summaries if s.get("status") == "PASS")
    failed = sum(1 for s in summaries if str(s.get("status", "")).startswith("FAIL"))
    durations = [s["duration"] for s in summaries if s.get("duration")]
    avg_duration = sum(durations) / len(durations) if durations else 0.0
    ref_counts = [s["refs"] for s in summaries if "refs" in s]
    avg_refs = sum(ref_counts) / len(ref_counts) if ref_counts else 0.0

    print()
    print(f"--- aggregate ---")
    print(f"runs:   {total}")
    if not args.dry_run and runner is not None:
        print(f"pass:   {passed}")
        print(f"fail:   {failed}")
        print(f"avg duration: {avg_duration:.2f}s")
    print(f"avg refs/run: {avg_refs:.1f}")
    return 0 if failed == 0 else 2


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return asyncio.run(_run_async(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
