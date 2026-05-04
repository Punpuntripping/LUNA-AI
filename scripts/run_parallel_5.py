"""One-shot parallel runner — 5 deep_search_v4 queries via asyncio.gather.

Same call path as agents/deep_search_v4/cli.py (run_full_loop with planner on),
just five concurrent FullLoopDeps. Logfire is configured once at process start
so every span lands under the same trace tree in the dashboard.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

# Ensure project root is on sys.path when invoked as a script.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv()

from shared.observability import configure_logfire
configure_logfire()

import httpx

from agents.deep_search_v4.orchestrator import FullLoopDeps, run_full_loop
from agents.utils.embeddings import embed_regulation_query_alibaba as embed_text
from shared.config import get_settings
from shared.db.client import get_supabase_client


QUERY_IDS = [16, 18]


def _load_queries() -> dict[int, str]:
    data = json.loads(Path("agents/test_queries.json").read_text(encoding="utf-8"))
    return {q["id"]: q["text"] for q in data["queries"] if q["id"] in QUERY_IDS}


def _build_deps(http_client: httpx.AsyncClient) -> FullLoopDeps:
    settings = get_settings()
    return FullLoopDeps(
        supabase=get_supabase_client(),
        embedding_fn=embed_text,
        jina_api_key=settings.JINA_RERANKER_API_KEY or "",
        http_client=http_client,
        use_reranker=False,
        expander_prompt_key="prompt_1",
        case_expander_prompt_key="prompt_3",
        concurrency=10,
        unfold_mode="precise",
        include_compliance=True,
        enable_planner=True,
    )


async def _run_one(query_id: int, query_text: str, http_client: httpx.AsyncClient) -> dict:
    deps = _build_deps(http_client)
    t0 = time.perf_counter()
    err: str | None = None
    output = None
    try:
        output = await run_full_loop(query_text, query_id, deps, prompt_key="prompt_1")
    except Exception as exc:
        err = repr(exc)
    duration = time.perf_counter() - t0
    return {
        "query_id": query_id,
        "duration_s": round(duration, 1),
        "confidence": getattr(output, "confidence", None),
        "model_used": getattr(output, "model_used", None),
        "ref_count": len(getattr(output, "references", []) or []),
        "validation_passed": (
            getattr(getattr(output, "validation", None), "passed", None)
            if output else None
        ),
        "error": err,
    }


async def main() -> None:
    queries = _load_queries()
    print(f"[parallel-5] launching {len(queries)} queries: {sorted(queries.keys())}")
    async with httpx.AsyncClient(timeout=60.0) as http_client:
        results = await asyncio.gather(
            *(_run_one(qid, qtext, http_client) for qid, qtext in queries.items()),
            return_exceptions=False,
        )
    print("\n[parallel-5] === RESULTS ===")
    print(f"{'qid':>4} {'dur_s':>7} {'conf':>8} {'refs':>5} {'pass':>6}  model")
    for r in sorted(results, key=lambda r: r["query_id"]):
        print(
            f"{r['query_id']:>4} "
            f"{r['duration_s']:>7.1f} "
            f"{(r['confidence'] or '-'):>8} "
            f"{r['ref_count']:>5} "
            f"{str(r['validation_passed']):>6}  "
            f"{r['model_used'] or '-'}"
        )
        if r["error"]:
            print(f"     ERROR: {r['error']}")


if __name__ == "__main__":
    asyncio.run(main())
