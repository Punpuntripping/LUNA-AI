"""Test runner for the full URA pipeline (reg → compliance → aggregator).

Usage:
    python -m agents.deep_search_v4.test_full_loop
    python -m agents.deep_search_v4.test_full_loop --query-ids 1 5 10 28
    python -m agents.deep_search_v4.test_full_loop --count 5
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
from pathlib import Path

QUERIES_PATH = Path(__file__).resolve().parent.parent.parent / "test_queries.json"
LOGS_DIR = Path(__file__).resolve().parent.parent / "tests_ura_reports"


def _load_queries() -> list[dict]:
    data = json.loads(QUERIES_PATH.read_text(encoding="utf-8"))
    return [q for q in data["queries"] if q.get("text")]


def _truncate(s: str, n: int = 80) -> str:
    s = s.replace("\n", " ")
    return s[:n] + "..." if len(s) > n else s


async def run_one(query_id: int, query_text: str, deps) -> dict:
    from agents.deep_search_v4.orchestrator import run_full_loop

    t0 = time.perf_counter()
    result = {
        "query_id": query_id,
        "query": query_text,
        "status": "error",
        "confidence": None,
        "references": 0,
        "reg_refs": 0,
        "compliance_refs": 0,
        "gaps": 0,
        "synthesis_preview": "",
        "duration": 0.0,
        "error": None,
    }
    try:
        output = await run_full_loop(
            query=query_text,
            query_id=query_id,
            deps=deps,
        )
        result["status"] = "ok"
        result["confidence"] = output.confidence
        result["references"] = len(output.references)
        result["reg_refs"] = sum(1 for r in output.references if r.domain == "regulations")
        result["compliance_refs"] = sum(1 for r in output.references if r.domain == "compliance")
        result["gaps"] = len(output.gaps)
        result["synthesis_preview"] = _truncate(output.synthesis_md, 200)
    except Exception as e:
        result["error"] = str(e)
    result["duration"] = time.perf_counter() - t0
    return result


def _print_result(r: dict, idx: int, total: int) -> None:
    status_icon = "✓" if r["status"] == "ok" else "✗"
    print(f"\n{'─'*70}")
    print(f"[{idx}/{total}] #{r['query_id']} {status_icon}  {_truncate(r['query'], 70)}")
    print(f"{'─'*70}")
    if r["status"] == "ok":
        print(f"  confidence : {r['confidence']}")
        print(f"  references : {r['references']} total  ({r['reg_refs']} reg + {r['compliance_refs']} compliance)")
        print(f"  gaps       : {r['gaps']}")
        print(f"  duration   : {r['duration']:.1f}s")
        print(f"  synthesis  :\n    {r['synthesis_preview']}")
    else:
        print(f"  ERROR: {r['error']}")
        print(f"  duration   : {r['duration']:.1f}s")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query-ids", type=int, nargs="+")
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--no-compliance", action="store_true")
    args = parser.parse_args()

    from shared.config import get_settings
    from shared.db.client import get_supabase_client
    from agents.utils.embeddings import embed_regulation_query
    from agents.deep_search_v4.orchestrator import FullLoopDeps

    get_settings()  # validate env vars early
    supabase = get_supabase_client()

    deps = FullLoopDeps(
        supabase=supabase,
        embedding_fn=embed_regulation_query,
        include_compliance=not args.no_compliance,
        max_reg_hits=8,
    )

    all_queries = _load_queries()
    id_map = {q["id"]: q for q in all_queries}

    if args.query_ids:
        selected = [id_map[qid] for qid in args.query_ids if qid in id_map]
    else:
        # Pick varied sample: avoid repeating categories
        random.seed(42)
        selected = random.sample(all_queries, min(args.count, len(all_queries)))

    print(f"\n{'='*70}")
    print(f"  Full Loop Test  —  {len(selected)} queries  (compliance={'off' if args.no_compliance else 'on'})")
    print(f"{'='*70}")
    for q in selected:
        print(f"  #{q['id']:3d}  [{q['category']}]  {_truncate(q.get('text',''), 55)}")

    results = []
    for i, q in enumerate(selected, 1):
        print(f"\n[{i}/{len(selected)}] Running #{q['id']}...")
        r = await run_one(q["id"], q["text"], deps)
        results.append(r)
        _print_result(r, i, len(selected))

    # Summary table
    passed = [r for r in results if r["status"] == "ok"]
    print(f"\n{'='*70}")
    print(f"  SUMMARY  {len(passed)}/{len(results)} passed")
    print(f"{'='*70}")
    print(f"  {'#':>3}  {'Status':<6}  {'Conf':<8}  {'Refs':>4}  {'Reg':>4}  {'Comp':>5}  {'Gaps':>5}  {'Time':>6}  Query")
    print(f"  {'─'*3}  {'─'*6}  {'─'*8}  {'─'*4}  {'─'*4}  {'─'*5}  {'─'*5}  {'─'*6}  {'─'*40}")
    for r in results:
        print(
            f"  #{r['query_id']:>2}  "
            f"{'ok' if r['status']=='ok' else 'ERR':<6}  "
            f"{str(r['confidence'] or '-'):<8}  "
            f"{r['references']:>4}  "
            f"{r['reg_refs']:>4}  "
            f"{r['compliance_refs']:>5}  "
            f"{r['gaps']:>5}  "
            f"{r['duration']:>5.0f}s  "
            f"{_truncate(r['query'], 40)}"
        )
    if passed:
        avg_dur = sum(r["duration"] for r in passed) / len(passed)
        avg_refs = sum(r["references"] for r in passed) / len(passed)
        avg_comp = sum(r["compliance_refs"] for r in passed) / len(passed)
        print(f"\n  avg duration  : {avg_dur:.0f}s")
        print(f"  avg refs      : {avg_refs:.1f}  ({avg_comp:.1f} compliance avg)")


if __name__ == "__main__":
    asyncio.run(main())
