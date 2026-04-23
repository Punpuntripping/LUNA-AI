"""Standalone debug CLI that simulates the router entry-point → full URA pipeline → output.

PURPOSE
-------
Production entry is ``agents/orchestrator.py`` (currently ~L368), which (after
the upcoming migration) will call ``agents.deep_search_v3.orchestrator.run_full_loop``.
This CLI replaces that path for local testing: give it an Arabic query string,
it builds the same ``FullLoopDeps`` the router would, invokes ``run_full_loop``,
and prints the final ``AggregatorOutput`` the way a downstream consumer would see it.

MIGRATION STATUS — IMPORTANT
-----------------------------
We are mid-migration.  Right now the entry point lives at
``agents/deep_search_v3/full_loop_runner.py``; after the rename commit it will
be at ``agents/deep_search_v3/orchestrator.py`` (same symbols).

This file **imports from the future path** (``agents.deep_search_v3.orchestrator``).
The CLI will not run until that rename commit lands — that is intentional and expected.

EXAMPLE INVOCATIONS
-------------------
    python -m agents.deep_search_v3.cli "شروط إثبات هجر الزوج"
    python -m agents.deep_search_v3.cli --query-id 42 "ما هي حقوق العامل في الفصل التعسفي؟"
    python -m agents.deep_search_v3.cli --expander-prompt prompt_2 --aggregator-prompt prompt_1 "..."
    python -m agents.deep_search_v3.cli --no-compliance "..."
    python -m agents.deep_search_v3.cli --use-reranker "..."
    python -m agents.deep_search_v3.cli --output json "..."
    python -m agents.deep_search_v3.cli --show-events --output pretty "..."
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from typing import TYPE_CHECKING

# ---------------------------------------------------------------------------
# Future-path import (will resolve once full_loop_runner.py is renamed to
# orchestrator.py — see migration note in module docstring above).
# ---------------------------------------------------------------------------
from agents.deep_search_v3.orchestrator import FullLoopDeps, run_full_loop  # noqa: E402

if TYPE_CHECKING:
    # AggregatorOutput fields used below (read from aggregator/models.py):
    #   synthesis_md: str
    #   references: list[Reference]  — each Reference has .n, .domain, .regulation_title,
    #                                   .article_num, .section_title, .title, .relevance
    #   confidence: Literal["high", "medium", "low"]
    #   gaps: list[str]
    #   disclaimer_ar: str
    #   prompt_key: str
    #   model_used: str
    #   validation: ValidationReport | None
    #   artifact: Artifact | None
    from agents.deep_search_v3.aggregator.models import AggregatorOutput


# ─────────────────────────────────────────────────────────────────────────────
# Argument parser
# ─────────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agents.deep_search_v3.cli",
        description=(
            "Simulate the router entry-point → full URA pipeline for a single Arabic query. "
            "Prints AggregatorOutput in pretty or JSON format."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "query",
        help="Arabic legal question to run through the pipeline.",
    )

    parser.add_argument(
        "--query-id",
        type=int,
        default=None,
        metavar="INT",
        help="Numeric query ID (default: auto-generated from timestamp).",
    )

    parser.add_argument(
        "--expander-prompt",
        choices=["prompt_1", "prompt_2"],
        default="prompt_1",
        dest="expander_prompt",
        help="Expander prompt variant (default: prompt_1).",
    )

    parser.add_argument(
        "--aggregator-prompt",
        choices=["prompt_1", "prompt_2"],
        default="prompt_1",
        dest="aggregator_prompt",
        help="Aggregator prompt variant forwarded to run_full_loop (default: prompt_1).",
    )

    parser.add_argument(
        "--reg-aggregator-prompt",
        choices=["prompt_1"],
        default="prompt_1",
        dest="reg_aggregator_prompt",
        help=(
            "Reg-search aggregator prompt variant (default: prompt_1). "
            "Currently unused if the reg aggregator is skipped; kept for future use."
        ),
    )

    parser.add_argument(
        "--no-compliance",
        action="store_true",
        dest="no_compliance",
        help="Disable the compliance search phase.",
    )

    parser.add_argument(
        "--use-reranker",
        action="store_true",
        dest="use_reranker",
        help="Enable the Jina cross-encoder reranker.",
    )

    parser.add_argument(
        "--unfold-mode",
        choices=["precise", "full"],
        default="precise",
        dest="unfold_mode",
        help="Unfold mode for the reg-search graph (default: precise).",
    )

    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        metavar="INT",
        help="Max concurrent search sub-queries (default: 10).",
    )

    parser.add_argument(
        "--model",
        default=None,
        metavar="OVERRIDE",
        dest="model_override",
        help="Model override string (e.g. 'qwen3.6-plus'). Optional.",
    )

    parser.add_argument(
        "--output",
        choices=["pretty", "json"],
        default="pretty",
        help="Output format (default: pretty).",
    )

    parser.add_argument(
        "--show-events",
        action="store_true",
        dest="show_events",
        help="Print every SSE event as it is collected (debugging).",
    )

    return parser


# ─────────────────────────────────────────────────────────────────────────────
# Deps builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_deps(args: argparse.Namespace) -> FullLoopDeps:
    """Construct FullLoopDeps from CLI flags + real shared infrastructure."""
    import httpx

    from agents.utils.embeddings import embed_regulation_query_alibaba as embed_text
    from shared.config import get_settings
    from shared.db.client import get_supabase_client

    settings = get_settings()
    supabase = get_supabase_client()

    jina_key: str = settings.JINA_RERANKER_API_KEY or ""

    http_client = httpx.AsyncClient(timeout=60.0)

    return FullLoopDeps(
        supabase=supabase,
        embedding_fn=embed_text,
        model_override=args.model_override,
        jina_api_key=jina_key,
        http_client=http_client,
        use_reranker=args.use_reranker,
        expander_prompt_key=args.expander_prompt,
        reg_aggregator_prompt_key=args.reg_aggregator_prompt,
        concurrency=args.concurrency,
        unfold_mode=args.unfold_mode,
        include_compliance=not args.no_compliance,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Output formatters
# ─────────────────────────────────────────────────────────────────────────────

_DIVIDER = "═" * 51


def _format_pretty(
    query: str,
    query_id: int,
    duration_s: float,
    agg_output: "AggregatorOutput",
    events: list[dict],
    show_events: bool,
) -> str:
    lines: list[str] = []
    lines.append("")
    lines.append(_DIVIDER)
    lines.append(f"Query      : {query}")
    lines.append(f"Query ID   : {query_id}")
    lines.append(f"Duration   : {duration_s:.2f}s")
    lines.append(f"Confidence : {agg_output.confidence}")
    lines.append(f"Model used : {agg_output.model_used or '(not recorded)'}")
    lines.append(f"Prompt key : {agg_output.prompt_key}")
    lines.append(f"Events     : {len(events)}")
    lines.append(_DIVIDER)

    lines.append("")
    lines.append("ANSWER (Arabic synthesis):")
    lines.append(agg_output.synthesis_md)

    refs = agg_output.references
    if refs:
        lines.append("")
        lines.append(f"REFERENCES ({len(refs)}):")
        for ref in refs:
            domain_tag = f"({ref.domain})"
            label = ref.render_label() if hasattr(ref, "render_label") else ref.title
            lines.append(f"  [{ref.n}] {domain_tag} {label}  [{ref.relevance}]")
    else:
        lines.append("")
        lines.append("REFERENCES: (none)")

    gaps = agg_output.gaps
    if gaps:
        lines.append("")
        lines.append("GAPS / UNANSWERED ASPECTS:")
        for gap in gaps:
            lines.append(f"  • {gap}")

    if agg_output.disclaimer_ar:
        lines.append("")
        lines.append("DISCLAIMER:")
        lines.append(agg_output.disclaimer_ar)

    # Validation report summary
    val = agg_output.validation
    if val is not None:
        lines.append("")
        lines.append(f"VALIDATION: passed={val.passed}  "
                     f"dangling={val.dangling_citations}  "
                     f"unused={val.unused_references}  "
                     f"coverage={val.sub_query_coverage:.0%}")

    # Event summary (counts by type)
    if events:
        lines.append("")
        counts = Counter(e.get("type", "unknown") for e in events)
        lines.append("EVENTS SUMMARY (by type):")
        for evt_type, count in sorted(counts.items()):
            lines.append(f"  {evt_type}: {count}")

    # Full event dump when --show-events
    if show_events and events:
        lines.append("")
        lines.append("ALL EVENTS:")
        for i, evt in enumerate(events):
            lines.append(f"  [{i:03d}] {json.dumps(evt, ensure_ascii=False)}")

    lines.append("")
    return "\n".join(lines)


def _format_json(
    query: str,
    query_id: int,
    duration_s: float,
    agg_output: "AggregatorOutput",
    events: list[dict],
    error: str | None,
) -> str:
    payload: dict = {
        "query": query,
        "query_id": query_id,
        "duration_s": round(duration_s, 3),
        "confidence": agg_output.confidence if agg_output else None,
        "answer": agg_output.synthesis_md if agg_output else None,
        "references": (
            [r.model_dump() for r in agg_output.references]
            if agg_output and agg_output.references
            else []
        ),
        "gaps": agg_output.gaps if agg_output else [],
        "model_used": agg_output.model_used if agg_output else None,
        "prompt_key": agg_output.prompt_key if agg_output else None,
        "events": events,
        "error": error,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

async def main(argv: list[str]) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    query: str = args.query
    query_id: int = args.query_id if args.query_id is not None else int(time.time())

    # ── Build deps ───────────────────────────────────────────────────────────
    try:
        deps = _build_deps(args)
    except Exception as exc:
        print(f"\n[CLI] ERROR building deps: {exc}", file=sys.stderr)
        sys.exit(1)

    # ── Note the starting event count so we can isolate this run's events ───
    events_start = len(deps._events)

    if args.output == "pretty":
        print(f"\n[CLI] Running full URA pipeline for query ID {query_id} …")
        print(f"[CLI] Compliance: {'ON' if not args.no_compliance else 'OFF'} | "
              f"Reranker: {'ON' if args.use_reranker else 'OFF'} | "
              f"Expander: {args.expander_prompt} | "
              f"Aggregator: {args.aggregator_prompt} | "
              f"Unfold: {args.unfold_mode} | "
              f"Concurrency: {args.concurrency}")

    # ── Invoke run_full_loop ─────────────────────────────────────────────────
    t0 = time.perf_counter()
    agg_output = None
    error_msg: str | None = None

    try:
        agg_output = await run_full_loop(
            query,
            query_id,
            deps,
            prompt_key=args.aggregator_prompt,
        )
    except Exception as exc:
        error_msg = str(exc)
        print(f"\n[CLI] PIPELINE ERROR: {exc}", file=sys.stderr)
        if args.output == "json":
            # Still emit valid JSON even on failure
            print(json.dumps(
                {
                    "query": query,
                    "query_id": query_id,
                    "duration_s": round(time.perf_counter() - t0, 3),
                    "confidence": None,
                    "answer": None,
                    "references": [],
                    "gaps": [],
                    "model_used": None,
                    "prompt_key": None,
                    "events": deps._events[events_start:],
                    "error": error_msg,
                },
                ensure_ascii=False,
                indent=2,
            ))
        sys.exit(1)
    finally:
        # Close the shared HTTP client
        if deps.http_client is not None:
            try:
                await deps.http_client.aclose()
            except Exception:
                pass

    duration_s = time.perf_counter() - t0
    run_events = deps._events[events_start:]

    # ── Format and print output ──────────────────────────────────────────────
    if args.output == "json":
        print(_format_json(query, query_id, duration_s, agg_output, run_events, error_msg))
    else:
        print(_format_pretty(
            query, query_id, duration_s, agg_output, run_events, args.show_events
        ))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
