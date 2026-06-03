"""One-shot parallel runner — 5 queries through the REDESIGNED planner loop.

Drives the new planner-owned two-phase loop directly:
    build_planner_deps  ->  handle_planner_turn  (decide -> retrieve -> respond)

Each turn is wrapped in a Logfire span ``planner.run5.turn`` so the whole batch
lands under one identifiable trace tree. Run from the project root:

    python scripts/run_planner_5.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

# UTF-8 stdout — the queries + summaries are Arabic.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:
    pass

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv()

from shared.observability import configure_logfire, get_logfire
configure_logfire()

import httpx

from agents.deep_search_v4.planner import build_planner_deps, handle_planner_turn
from agents.utils.embeddings import embed_regulation_query_alibaba as embed_text
from shared.config import get_settings
from shared.db.client import get_supabase_client

_logfire = get_logfire()

# Diverse spread — should exercise reg_led / compliance_led / full.
# Override from argv: `python scripts/run_planner_5.py 7 13 22`
QUERY_IDS = [int(a) for a in sys.argv[1:]] or [7, 12, 13, 16, 22]


def _load_queries() -> dict[int, str]:
    data = json.loads((_ROOT / "agents" / "test_queries.json").read_text(encoding="utf-8"))
    return {q["id"]: q["text"] for q in data["queries"] if q["id"] in QUERY_IDS}


async def _run_one(query_id: int, query_text: str, http_client: httpx.AsyncClient) -> dict:
    settings = get_settings()
    deps = build_planner_deps(
        supabase=get_supabase_client(),
        embedding_fn=embed_text,
        http_client=http_client,
        jina_api_key=settings.JINA_RERANKER_API_KEY or "",
        detail_level="medium",
        query_id=query_id,
    )
    t0 = time.perf_counter()
    err: str | None = None
    turn = None
    with _logfire.span("planner.run5.turn", query_id=query_id, query=query_text[:160]) as span:
        try:
            turn = await handle_planner_turn(query_text, deps)
        except Exception as exc:  # handle_planner_turn never raises, but guard anyway
            err = repr(exc)
        try:
            if turn is not None:
                span.set_attribute("kind", turn.kind)
                if turn.decision is not None:
                    span.set_attribute("mode", turn.decision.mode)
                    span.set_attribute("support", turn.decision.support)
                span.set_attribute("degraded", turn.degraded)
        except Exception:
            pass

    duration = time.perf_counter() - t0
    decision = getattr(turn, "decision", None)
    agg = getattr(turn, "agg_output", None)
    resp = getattr(turn, "response", None)
    return {
        "query_id": query_id,
        "duration_s": round(duration, 1),
        "kind": getattr(turn, "kind", None),
        "mode": getattr(decision, "mode", None),
        "support": getattr(decision, "support", None),
        "sectors": getattr(decision, "sectors", None),
        "degraded": getattr(turn, "degraded", None),
        "confidence": getattr(agg, "confidence", None),
        "ref_count": len(getattr(agg, "references", []) or []),
        "key_findings": len(getattr(agg, "key_findings", []) or []),
        "gaps": len(getattr(agg, "gaps", []) or []),
        "suggested_action": getattr(resp, "suggested_action", None),
        "chat_summary": (getattr(resp, "chat_summary_md", "") or "")[:280],
        "suggestion": (getattr(resp, "suggestion_md", "") or "")[:180],
        "events": [e.get("event") for e in getattr(deps, "_events", []) or []],
        # 2026-06: ``fallback_triggered`` was replaced by ``correction_triggered``
        # when the aggregator switched from prompt+model swap to self-correction
        # with message_history. Each entry holds the failing gates + notes.
        "correction_events": [
            {
                "failing_gates": e.get("failing_gates"),
                "notes": e.get("notes"),
            }
            for e in getattr(deps, "_events", []) or []
            if e.get("event") == "correction_triggered"
        ],
        "error": err,
    }


async def main() -> None:
    queries = _load_queries()
    print(f"[planner-5] launching {len(queries)} queries through the planner loop: "
          f"{sorted(queries.keys())}")
    with _logfire.span("planner.run5.batch", query_ids=sorted(queries.keys())):
        async with httpx.AsyncClient(timeout=90.0) as http_client:
            results = await asyncio.gather(
                *(_run_one(qid, qtext, http_client) for qid, qtext in queries.items()),
                return_exceptions=False,
            )

    print("\n[planner-5] === RESULTS ===")
    for r in sorted(results, key=lambda r: r["query_id"]):
        print("\n" + "=" * 78)
        print(f"Q{r['query_id']}  kind={r['kind']}  mode={r['mode']}  support={r['support']}  "
              f"degraded={r['degraded']}  dur={r['duration_s']}s")
        print(f"  sectors={r['sectors']}")
        print(f"  confidence={r['confidence']}  refs={r['ref_count']}  "
              f"key_findings={r['key_findings']}  gaps={r['gaps']}  "
              f"suggested_action={r['suggested_action']}")
        print(f"  events={r['events']}")
        if r["correction_events"]:
            print(f"  >> AGGREGATOR correction_triggered — failing gates + notes:")
            for ev in r["correction_events"]:
                print(f"     gates={ev['failing_gates']}  notes={ev['notes']}")
        if r["error"]:
            print(f"  ERROR: {r['error']}")
        print(f"  chat_summary: {r['chat_summary']}")
        if r["suggestion"]:
            print(f"  suggestion:   {r['suggestion']}")

    print("\n" + "=" * 78)
    print(f"{'qid':>4} {'kind':>10} {'mode':>16} {'sup':>5} {'conf':>8} {'refs':>5} {'degr':>6}")
    for r in sorted(results, key=lambda r: r["query_id"]):
        print(f"{r['query_id']:>4} {str(r['kind']):>10} {str(r['mode']):>16} "
              f"{str(r['support']):>5} {str(r['confidence']):>8} {r['ref_count']:>5} "
              f"{str(r['degraded']):>6}")


if __name__ == "__main__":
    asyncio.run(main())
