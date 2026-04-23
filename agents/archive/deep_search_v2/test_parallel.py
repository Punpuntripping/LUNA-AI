"""Run 5 test queries in parallel (no mock).

Usage:
    cd C:\Programming\LUNA_AI
    python -m agents.deep_search_v2.test_parallel
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
import time
from pathlib import Path

# Force UTF-8 output on Windows
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


QUERIES_FILE = Path(__file__).parent.parent / "test_queries.json"

# Pick 5 diverse query IDs
SELECTED_IDS = [1, 5, 14, 23, 27]


async def run_one(query_id: int, query_text: str) -> dict:
    """Run a single deep search query and return summary."""
    from shared.db.client import get_supabase_client
    from .graph import build_search_deps, handle_deep_search_turn

    start = time.time()
    print(f"[Q{query_id}] Starting...")

    supabase = get_supabase_client()
    deps = await build_search_deps(
        user_id=f"test-user-q{query_id}",
        conversation_id=f"test-conv-q{query_id}",
        case_id=None,
        supabase=supabase,
    )

    try:
        result, events = await handle_deep_search_turn(
            message=query_text,
            deps=deps,
        )
        elapsed = time.time() - start

        # Extract key info
        if hasattr(result, "last_response"):
            response = result.last_response or ""
            artifact = result.artifact or ""
            reason = result.reason
        else:
            response = result.response or ""
            artifact = result.artifact or ""
            reason = "continue"

        print(f"[Q{query_id}] Done in {elapsed:.1f}s -- {len(events)} events, "
              f"artifact={len(artifact)} chars, reason={reason}")

        return {
            "query_id": query_id,
            "status": "ok",
            "elapsed_s": round(elapsed, 1),
            "response_preview": response[:200],
            "artifact_chars": len(artifact),
            "events_count": len(events),
            "event_types": [e.get("type") for e in events],
            "reason": reason,
        }

    except Exception as e:
        elapsed = time.time() - start
        print(f"[Q{query_id}] ERROR in {elapsed:.1f}s: {e}")
        return {
            "query_id": query_id,
            "status": "error",
            "elapsed_s": round(elapsed, 1),
            "error": str(e),
        }


async def main():
    # Load queries
    data = json.loads(QUERIES_FILE.read_text(encoding="utf-8"))
    queries = {q["id"]: q for q in data["queries"]}

    # Build tasks
    tasks = []
    for qid in SELECTED_IDS:
        q = queries[qid]
        # Handle sub_queries format
        if "sub_queries" in q:
            text = "\n".join(sq["text"] for sq in q["sub_queries"])
        else:
            text = q["text"]
        print(f"[Q{qid}] {q['category']}")
        print(f"       {text[:80]}...\n")
        tasks.append(run_one(qid, text))

    print(f"\n{'=' * 60}")
    print(f"Running {len(tasks)} queries in parallel (no mock)...")
    print(f"{'=' * 60}\n")

    total_start = time.time()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    total_elapsed = time.time() - total_start

    # Summary
    print(f"\n{'=' * 60}")
    print(f"ALL DONE in {total_elapsed:.1f}s")
    print(f"{'=' * 60}\n")

    for r in results:
        if isinstance(r, Exception):
            print(f"  EXCEPTION: {r}")
        else:
            qid = r["query_id"]
            status = r["status"]
            elapsed = r["elapsed_s"]
            if status == "ok":
                print(f"  Q{qid}: OK  {elapsed}s  artifact={r['artifact_chars']}chars  "
                      f"events={r['events_count']}")
                print(f"       Response: {r['response_preview'][:120]}...")
            else:
                print(f"  Q{qid}: ERROR  {elapsed}s  {r.get('error', '?')}")

    print()


if __name__ == "__main__":
    asyncio.run(main())
