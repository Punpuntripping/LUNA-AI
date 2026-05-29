"""Standalone debug CLI that runs the production planner-driven deep_search loop.

PURPOSE
-------
Production entry for deep_search is ``agents/orchestrator.py`` →
``_run_deep_search``. That function builds a :class:`PlannerDeps` and calls
:func:`handle_planner_turn` (phase 1 decide → phase 2 retrieve → phase 3
respond), then publishes the ``agent_search`` artifact via
``publish_search_result``.

This CLI is a faithful one-shot harness for that path. It builds the same
``PlannerDeps`` ``_run_deep_search`` builds and calls the **same**
``handle_planner_turn`` — the literal production loop — so the planner, mode
selection, and ``prompt_mode_*`` aggregator prompts are all exercised exactly
as in production. The CLI deliberately stops at ``handle_planner_turn`` and
does NOT run the ``publish_search_result`` artifact-persist step: a one-shot
test query has no real ``conversation_id`` to write a ``workspace_items`` row
against, and persisting test artifacts would pollute the workspace. The
decide→retrieve→respond pipeline itself is byte-identical to production.

Why not call ``_run_deep_search`` directly? Because it collapses the planner's
``PlannerTurnResult`` (which carries ``decision`` + ``agg_output`` —
mode/support/confidence/references/gaps/prompt_key) down to a ``SpecialistResult``
that drops those fields. The ``logfire-run-monitor`` parses them, so the CLI
must keep the ``PlannerTurnResult`` in hand. ``handle_planner_turn`` IS the
production loop — calling it directly is zero pipeline drift, just one layer in.

EXAMPLE INVOCATIONS
-------------------
    python -m agents.deep_search_v4.cli "شروط إثبات هجر الزوج"
    python -m agents.deep_search_v4.cli --query-id 42 "ما هي حقوق العامل في الفصل التعسفي؟"
    python -m agents.deep_search_v4.cli --model qwen "..."
    python -m agents.deep_search_v4.cli --output json "..."
    python -m agents.deep_search_v4.cli --show-events --output pretty "..."
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from typing import TYPE_CHECKING

from agents.utils.agent_models import OVERRIDE_TOKENS  # noqa: E402
from agents.utils.tracking import track_stage  # noqa: E402

# Load .env + configure logfire so the planner / orchestrator spans phone home
# (same wiring backend/app/main.py uses). The logfire-run-monitor depends on
# this telemetry export, so configure_logfire() MUST run on import.
try:
    from dotenv import load_dotenv as _load_dotenv  # type: ignore[import-not-found]
    _load_dotenv()
except ImportError:
    pass

try:
    from shared.observability import configure_logfire as _configure_logfire, get_logfire
    _configure_logfire()
    _logfire = get_logfire()
except Exception:  # pragma: no cover - telemetry is best-effort
    _logfire = None  # type: ignore[assignment]

if TYPE_CHECKING:
    # PlannerTurnResult fields used below (planner/runner.py):
    #   kind: Literal["completed", "paused"]
    #   response: PlannerResponse | None  — chat_summary_md / suggestion_md / suggested_action
    #   decision: PlannerDecision | None  — mode / support / sectors / rationale
    #   agg_output: AggregatorOutput | None  — synthesis_md / references / confidence /
    #                                          gaps / model_used / prompt_key / validation
    #   degraded: bool
    #   planner_result / deferred  — pause state (kind == "paused")
    from agents.deep_search_v4.planner.runner import PlannerTurnResult


# ─────────────────────────────────────────────────────────────────────────────
# Argument parser
# ─────────────────────────────────────────────────────────────────────────────


class _DeprecatedStoreTrue(argparse.Action):
    """Accept an obsolete flag but ignore it, printing a one-line deprecation note."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("nargs", 0)
        super().__init__(*args, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):  # noqa: D401
        print(
            f"[CLI] note: {option_string} is obsolete and ignored — the planner "
            f"now owns mode + aggregator-prompt selection.",
            file=sys.stderr,
        )


class _DeprecatedStoreValue(argparse.Action):
    """Accept an obsolete value-taking flag but ignore it, printing a deprecation note."""

    def __call__(self, parser, namespace, values, option_string=None):  # noqa: D401
        print(
            f"[CLI] note: {option_string} is obsolete and ignored — the planner "
            f"now owns mode + aggregator-prompt selection.",
            file=sys.stderr,
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agents.deep_search_v4.cli",
        description=(
            "Run the production planner-driven deep_search_v4 loop for a single "
            "Arabic query — the same handle_planner_turn path that "
            "orchestrator._run_deep_search uses. Prints the planner chat summary "
            "+ aggregator artifact in pretty or JSON."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "query",
        help="Arabic legal question to run through the planner-driven pipeline.",
    )

    parser.add_argument(
        "--query-id",
        type=int,
        default=None,
        metavar="INT",
        help="Numeric query ID (default: auto-generated from timestamp).",
    )

    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        metavar="INT",
        help="Max concurrent search sub-queries (default: 10).",
    )

    parser.add_argument(
        "--unfold-mode",
        choices=["precise", "full"],
        default="precise",
        dest="unfold_mode",
        help="Unfold mode for the reg-search graph (default: precise).",
    )

    parser.add_argument(
        "--detail-level",
        choices=["low", "medium", "high"],
        default="medium",
        dest="detail_level",
        help="Detail level forwarded to the planner deps (default: medium).",
    )

    parser.add_argument(
        "--model",
        default=None,
        choices=list(OVERRIDE_TOKENS),
        dest="model_override",
        help="Tier override token applied to every agent (planner + executors): "
             "'qwen'/'deepseek' pick the primary model family, "
             "'alibaba'/'openrouter' pick the primary provider. Tier stays fixed. "
             "Honored by the planner via build_planner_deps(model_override=...).",
    )

    # ── Obsolete flags — accepted-but-ignored so old invocations don't crash ──
    # The planner now owns mode + aggregator-prompt selection (mode → prompt_mode_*),
    # and the loop is always planner-driven. These flags would silently mislead.
    parser.add_argument(
        "--aggregator-prompt",
        action=_DeprecatedStoreValue,
        dest="_deprecated_aggregator_prompt",
        default=argparse.SUPPRESS,
        metavar="PROMPT",
        help="(obsolete, ignored) The planner picks the aggregator prompt via "
             "mode → prompt_mode_*.",
    )
    parser.add_argument(
        "--enable-planner",
        action=_DeprecatedStoreTrue,
        dest="_deprecated_enable_planner",
        default=argparse.SUPPRESS,
        help="(obsolete, ignored) The loop is always planner-driven now.",
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
# Deps builder — mirrors _run_deep_search's build_planner_deps call
# ─────────────────────────────────────────────────────────────────────────────


def _build_deps(args: argparse.Namespace, query_id: int):
    """Construct PlannerDeps from CLI flags + real shared infrastructure.

    Mirrors agents/orchestrator.py::_run_deep_search — same embedding fn, same
    JINA key source, same builder. The CLI additionally threads its own
    ``query_id`` through. Returns (deps, http_client) — the caller owns closing
    the http_client.
    """
    import httpx

    from agents.deep_search_v4.planner import build_planner_deps
    from agents.utils.embeddings import embed_regulation_query_alibaba
    from shared.config import get_settings
    from shared.db.client import get_supabase_client

    settings = get_settings()
    supabase = get_supabase_client()

    http_client = httpx.AsyncClient(timeout=30.0)

    deps = build_planner_deps(
        supabase=supabase,
        embedding_fn=embed_regulation_query_alibaba,
        http_client=http_client,
        jina_api_key=settings.JINA_RERANKER_API_KEY or "",
        model_override=args.model_override,
        detail_level=args.detail_level,
        query_id=query_id,
        concurrency=args.concurrency,
        unfold_mode=args.unfold_mode,
    )
    return deps, http_client


# ─────────────────────────────────────────────────────────────────────────────
# Output formatters
# ─────────────────────────────────────────────────────────────────────────────

_DIVIDER = "═" * 51


def _extract_pause_question(turn: "PlannerTurnResult") -> str:
    """Best-effort pull of the clarifying question text from a paused turn."""
    deferred = getattr(turn, "deferred", None)
    try:
        calls = getattr(deferred, "calls", None) or []
        if calls:
            args = calls[0].args_as_dict()
            return str(args.get("question", "")) or ""
    except Exception:
        pass
    return ""


def _format_pretty(
    query: str,
    query_id: int,
    duration_s: float,
    turn: "PlannerTurnResult",
    events: list[dict],
    show_events: bool,
) -> str:
    decision = turn.decision
    agg = turn.agg_output
    response = turn.response

    lines: list[str] = []
    lines.append("")
    lines.append(_DIVIDER)
    lines.append(f"Query      : {query}")
    lines.append(f"Query ID   : {query_id}")
    lines.append(f"Duration   : {duration_s:.2f}s")
    lines.append(f"Turn kind  : {turn.kind}")
    lines.append(f"Mode       : {decision.mode if decision else '(none)'}")
    lines.append(f"Support    : {decision.support if decision else '(none)'}")

    if turn.kind == "paused":
        # Phase-1 ask_user pause — print the clarifying question and stop.
        question = _extract_pause_question(turn)
        lines.append(_DIVIDER)
        lines.append("")
        lines.append("PLANNER PAUSED — clarifying question (phase 1 ask_user):")
        lines.append(question or "(no question text recovered)")
        lines.append("")
        lines.append("The CLI is one-shot / non-interactive — exiting cleanly.")
        lines.append("")
        return "\n".join(lines)

    # ── completed turn ───────────────────────────────────────────────────────
    lines.append(f"Confidence : {agg.confidence if agg else '(n/a)'}")
    lines.append(f"Model used : {(agg.model_used if agg else None) or '(not recorded)'}")
    lines.append(f"Prompt key : {agg.prompt_key if agg else '(n/a)'}")
    lines.append(f"Degraded   : {turn.degraded}")
    lines.append(f"Events     : {len(events)}")
    lines.append(_DIVIDER)

    lines.append("")
    lines.append("CHAT SUMMARY (planner phase 3, Arabic):")
    lines.append((getattr(response, "chat_summary_md", "") or "").strip()
                  or "(no chat summary)")

    suggestion = (getattr(response, "suggestion_md", "") or "").strip()
    if suggestion:
        lines.append("")
        lines.append("SUGGESTION (planner next-step):")
        lines.append(suggestion)
        lines.append(f"  → suggested_action: {getattr(response, 'suggested_action', 'none')}")

    if agg is not None:
        lines.append("")
        lines.append("ANSWER (aggregator artifact synthesis):")
        lines.append(agg.synthesis_md)

        refs = agg.references
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

        if agg.gaps:
            lines.append("")
            lines.append("GAPS / UNANSWERED ASPECTS:")
            for gap in agg.gaps:
                lines.append(f"  • {gap}")

        if agg.disclaimer_ar:
            lines.append("")
            lines.append("DISCLAIMER:")
            lines.append(agg.disclaimer_ar)

        val = agg.validation
        if val is not None:
            lines.append("")
            lines.append(f"VALIDATION: passed={val.passed}  "
                         f"dangling={val.dangling_citations}  "
                         f"unused={val.unused_references}  "
                         f"coverage={val.sub_query_coverage:.0%}")
    else:
        lines.append("")
        lines.append("(no aggregator output — degraded turn)")

    # Event summary (counts by type)
    if events:
        lines.append("")
        counts = Counter(e.get("type", e.get("event", "unknown")) for e in events)
        lines.append("EVENTS SUMMARY (by type):")
        for evt_type, count in sorted(counts.items()):
            lines.append(f"  {evt_type}: {count}")

    if show_events and events:
        lines.append("")
        lines.append("ALL EVENTS:")
        for i, evt in enumerate(events):
            lines.append(f"  [{i:03d}] {json.dumps(evt, ensure_ascii=False)}")

    lines.append("")
    return "\n".join(lines)


def _build_json_payload(
    query: str,
    query_id: int,
    duration_s: float,
    turn: "PlannerTurnResult | None",
    events: list[dict],
    error: str | None,
) -> dict:
    """Assemble the --output json payload.

    Backward-compatible keys (logfire-run-monitor parses these — MUST stay
    present): query, query_id, duration_s, confidence, answer, references,
    gaps, model_used, prompt_key, events, error.

    New planner-loop keys: mode, support, kind, suggestion, suggested_action.
    """
    decision = getattr(turn, "decision", None) if turn else None
    agg = getattr(turn, "agg_output", None) if turn else None
    response = getattr(turn, "response", None) if turn else None
    kind = getattr(turn, "kind", None) if turn else None

    if kind == "paused":
        # Paused turn — answer carries the clarifying question; agg fields null.
        answer = _extract_pause_question(turn) if turn else None
        confidence = None
        references: list = []
        gaps: list = []
        model_used = None
        prompt_key = None
    else:
        # completed turn (or hard harness error → turn is None)
        # answer = the planner's phase-3 chat summary (what production streams).
        answer = (getattr(response, "chat_summary_md", None) if response else None)
        confidence = agg.confidence if agg else None
        references = (
            [r.model_dump() for r in agg.references]
            if agg and agg.references
            else []
        )
        gaps = agg.gaps if agg else []
        model_used = agg.model_used if agg else None
        prompt_key = agg.prompt_key if agg else None

    return {
        # ── backward-compatible keys (monitor depends on these) ──────────────
        "query": query,
        "query_id": query_id,
        "duration_s": round(duration_s, 3),
        "confidence": confidence,
        "answer": answer,
        "references": references,
        "gaps": gaps,
        "model_used": model_used,
        "prompt_key": prompt_key,
        "events": events,
        "error": error,
        # ── new planner-loop keys ────────────────────────────────────────────
        "mode": decision.mode if decision else None,
        "support": decision.support if decision else None,
        "kind": kind,
        "suggestion": (getattr(response, "suggestion_md", None) if response else None),
        "suggested_action": (
            getattr(response, "suggested_action", None) if response else None
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point — mirrors _run_deep_search's structure
# ─────────────────────────────────────────────────────────────────────────────


async def main(argv: list[str]) -> None:
    from agents.deep_search_v4.planner import handle_planner_turn

    parser = _build_parser()
    args = parser.parse_args(argv)

    query: str = args.query
    query_id: int = args.query_id if args.query_id is not None else int(time.time())

    # ── Build deps ───────────────────────────────────────────────────────────
    try:
        deps, http_client = _build_deps(args, query_id)
    except Exception as exc:
        print(f"\n[CLI] ERROR building deps: {exc}", file=sys.stderr)
        sys.exit(1)

    # Note the starting event count so we can isolate this run's events.
    events_start = len(deps._events)

    if args.output == "pretty":
        print(f"\n[CLI] Running planner-driven deep_search_v4 loop for query ID {query_id} …")
        print(f"[CLI] Detail: {args.detail_level} | "
              f"Unfold: {args.unfold_mode} | "
              f"Concurrency: {args.concurrency} | "
              f"Model override: {args.model_override or '(none)'}")
        print("[CLI] (planner chooses mode + aggregator prompt)")

    # ── Invoke handle_planner_turn inside a Logfire span ─────────────────────
    # run_retrieval inside emits deep_search.run_full_loop + phase spans, so the
    # monitor gets a clean trace tree rooted at deep_search.cli.turn.
    t0 = time.perf_counter()
    turn = None
    error_msg: str | None = None

    try:
        if _logfire is not None:
            with track_stage(
                "deep_search.cli.turn",
                agent_family="deep_search",
                query_id=query_id,
            ) as span:
                turn = await handle_planner_turn(query, deps)
                try:
                    span.set(kind=turn.kind)
                    span.set(
                        mode=turn.decision.mode if turn.decision else "(none)"
                    )
                except Exception:
                    pass
        else:
            turn = await handle_planner_turn(query, deps)
    except Exception as exc:
        # handle_planner_turn is contracted never to raise — a raise here is a
        # genuine harness failure. Still emit valid JSON.
        error_msg = str(exc)
        print(f"\n[CLI] PIPELINE ERROR: {exc}", file=sys.stderr)
        if args.output == "json":
            payload = _build_json_payload(
                query, query_id, time.perf_counter() - t0, None,
                deps._events[events_start:], error_msg,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        sys.exit(1)
    finally:
        # Close the shared HTTP client.
        if http_client is not None:
            try:
                await http_client.aclose()
            except Exception:
                pass

    duration_s = time.perf_counter() - t0
    run_events = deps._events[events_start:]

    # ── Format and print output ──────────────────────────────────────────────
    if args.output == "json":
        payload = _build_json_payload(
            query, query_id, duration_s, turn, run_events, error_msg
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(_format_pretty(
            query, query_id, duration_s, turn, run_events, args.show_events
        ))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
