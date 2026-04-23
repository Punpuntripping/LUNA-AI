"""CLI test runner for deep_search planner agent.

Usage:
    python -m agents.deep_search.cli "your Arabic query here"
    python -m agents.deep_search.cli                          # uses default test query
    python -m agents.deep_search.cli --prompt concise "query" # use a named prompt
    python -m agents.deep_search.cli --list-prompts           # show available prompts
    python -m agents.deep_search.cli --list-logs              # list recent run logs
    python -m agents.deep_search.cli --read-log LOG_ID        # read a specific log
    python -m agents.deep_search.cli --cross-feed             # run 10 random queries once each
    python -m agents.deep_search.cli --cross-feed --n 5       # run N random queries
"""
from __future__ import annotations

import asyncio
import io
import json
import random
import sys
from pathlib import Path

# Force UTF-8 output on Windows to handle Arabic text
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from .logger import LOGS_DIR

_TEST_QUERIES_PATH = Path(__file__).parent.parent / "test_queries.json"


def format_result(result, events: list[dict]) -> str:
    """Format the run result for terminal display."""
    lines = []
    lines.append(f"\n{'=' * 60}")
    lines.append(f"Result type: {type(result).__name__}")
    lines.append(f"{'=' * 60}\n")

    if hasattr(result, "response"):
        lines.append(f"Response:\n{result.response}\n")
    elif hasattr(result, "last_response"):
        lines.append(f"Last response:\n{result.last_response}\n")
        lines.append(f"Reason: {result.reason}")
        lines.append(f"Summary:\n{result.summary}\n")

    if hasattr(result, "artifact") and result.artifact:
        lines.append(f"{'~' * 40}")
        lines.append(f"Artifact ({len(result.artifact)} chars):")
        lines.append(result.artifact[:500])
        if len(result.artifact) > 500:
            lines.append(f"... ({len(result.artifact) - 500} more chars)")

    if events:
        lines.append(f"\n{'~' * 40}")
        lines.append(f"SSE Events ({len(events)}):")
        for e in events:
            etype = e.get("type", "?")
            detail = e.get("text", e.get("artifact_id", e.get("question", "")))
            lines.append(f"  [{etype}] {detail}")

    return "\n".join(lines)


def list_logs(limit: int = 20) -> list[str]:
    """List recent log IDs (folders or legacy JSON files)."""
    if not LOGS_DIR.exists():
        return []
    # Folders (new format) — sorted by name descending (timestamp-based)
    folders = sorted(
        [d for d in LOGS_DIR.iterdir() if d.is_dir()],
        key=lambda d: d.name,
        reverse=True,
    )
    # Legacy JSON files (old format)
    files = sorted(LOGS_DIR.glob("*.json"), reverse=True)

    ids = [f.name for f in folders] + [f.stem for f in files]
    return ids[:limit]


def read_log(log_id: str) -> dict:
    """Read a log entry (folder or legacy JSON file)."""
    # Try folder format first
    folder = LOGS_DIR / log_id
    if folder.is_dir():
        run_json = folder / "run.json"
        if run_json.exists():
            data = json.loads(run_json.read_text(encoding="utf-8"))
            # List all files in the folder for context
            data["_files"] = [f.name for f in sorted(folder.rglob("*")) if f.is_file()]
            return data
        return {"log_id": log_id, "_files": [f.name for f in sorted(folder.rglob("*")) if f.is_file()]}

    # Legacy: single JSON file
    log_file = LOGS_DIR / f"{log_id}.json"
    if log_file.exists():
        return json.loads(log_file.read_text(encoding="utf-8"))
    return {"error": f"Log {log_id} not found"}


def _parse_args() -> tuple[str | None, str]:
    """Parse CLI arguments, return (prompt_name, query)."""
    args = sys.argv[1:]
    prompt_name = None
    query_parts = []

    i = 0
    while i < len(args):
        if args[i] == "--prompt" and i + 1 < len(args):
            prompt_name = args[i + 1]
            i += 2
        elif args[i].startswith("--"):
            i += 1  # skip other flags (handled in main)
        else:
            query_parts.append(args[i])
            i += 1

    query = " ".join(query_parts) if query_parts else ""
    return prompt_name, query


def _load_queries(n: int = 10) -> list[dict]:
    """Load N random queries from test_queries.json, flattening sub_queries."""
    data = json.loads(_TEST_QUERIES_PATH.read_text(encoding="utf-8"))
    flat = []
    for q in data["queries"]:
        if "text" in q:
            flat.append({"id": q["id"], "category": q["category"], "text": q["text"]})
        elif "sub_queries" in q:
            for i, sq in enumerate(q["sub_queries"]):
                flat.append({"id": f"{q['id']}.{i+1}", "category": q["category"], "text": sq["text"]})
    sample = random.sample(flat, min(n, len(flat)))
    return sample


def _load_mock_results() -> dict:
    """Merge both mock result files into one dict keyed by query ID."""
    from .mock_results_1_15 import MOCK_RESULTS_1_15
    from .mock_results_16_30 import MOCK_RESULTS_16_30
    return {**MOCK_RESULTS_1_15, **MOCK_RESULTS_16_30}


async def run_cross_feed(n: int = 10) -> None:
    """Run N random queries from test_queries.json, one turn each (round 1 only)."""
    queries = _load_queries(n)
    mock_db = _load_mock_results()

    print(f"\n{'=' * 60}")
    print(f"Cross-feed: {len(queries)} queries, 1 round each (mock tools)")
    print(f"{'=' * 60}\n")

    from shared.db.client import get_supabase_client
    from .deps import build_search_deps
    from .runner import handle_deep_search_turn

    supabase = get_supabase_client()
    passed = 0
    failed = 0

    for idx, q in enumerate(queries, 1):
        qid = q["id"]
        category = q["category"]
        text = q["text"]
        # Resolve integer key (sub-queries like 24.1 fall back to parent id)
        base_id = int(str(qid).split(".")[0])
        mock_results = mock_db.get(base_id)

        print(f"[{idx}/{len(queries)}] id={qid}  {category}")
        print(f"  Query: {text[:80]}{'...' if len(text) > 80 else ''}")
        print(f"  Mock: {'injected' if mock_results else 'none (using defaults)'}")

        deps = await build_search_deps(
            user_id=f"cross-feed-{idx}",
            conversation_id=f"cross-feed-conv-{idx}",
            case_id=None,
            supabase=supabase,
        )
        deps.mock_results = mock_results

        try:
            result, events = await handle_deep_search_turn(message=text, deps=deps)
            rtype = type(result).__name__
            if hasattr(result, "response"):
                preview = result.response[:120]
            elif hasattr(result, "last_response"):
                preview = result.last_response[:120]
            else:
                preview = str(result)[:120]
            print(f"  -> {rtype}: {preview}")
            print(f"  -> SSE events: {len(events)}")
            passed += 1
        except Exception as e:
            print(f"  -> ERROR: {e}")
            failed += 1
        print()

    print(f"{'=' * 60}")
    print(f"Done: {passed} passed, {failed} failed / {len(queries)} total")
    print(f"{'=' * 60}\n")


async def main():
    """Main CLI entry point."""
    # Handle --cross-feed
    if "--cross-feed" in sys.argv:
        n = 10
        if "--n" in sys.argv:
            idx = sys.argv.index("--n")
            if idx + 1 < len(sys.argv):
                try:
                    n = int(sys.argv[idx + 1])
                except ValueError:
                    pass
        await run_cross_feed(n)
        return

    # Handle --list-prompts
    if "--list-prompts" in sys.argv:
        from .prompts import list_prompts
        print("\nAvailable prompts:")
        for name, preview in list_prompts():
            print(f"  {name:20s} {preview}")
        return

    # Handle --list-logs
    if "--list-logs" in sys.argv:
        logs = list_logs()
        if not logs:
            print("No logs found.")
        else:
            print(f"\nRecent logs ({len(logs)}):")
            for log_id in logs:
                log = read_log(log_id)
                status = log.get("status", "?")
                duration = log.get("duration_seconds", "?")
                # Handle both old (message) and new (message or query) formats
                inp = log.get("input", {})
                query = inp.get("message", inp.get("query", ""))
                if isinstance(query, str):
                    query = query[:60]
                else:
                    query = ""
                done = "?"
                pr = log.get("planner_result")
                if isinstance(pr, dict):
                    done = pr.get("task_done", "?")
                files = log.get("_files", [])
                files_str = f"  files={len(files)}" if files else ""
                print(f"  {log_id}  [{status}]  {duration}s  done={done}{files_str}  {query}...")
        return

    # Handle --read-log LOG_ID
    if "--read-log" in sys.argv:
        idx = sys.argv.index("--read-log")
        if idx + 1 < len(sys.argv):
            log = read_log(sys.argv[idx + 1])
            print(json.dumps(log, ensure_ascii=False, indent=2))
        else:
            print("Usage: --read-log LOG_ID")
        return

    # Parse --prompt and query
    prompt_name, query = _parse_args()

    if not query:
        query = "ما هي حقوق العامل في حالة الفصل التعسفي؟"
        print(f"Using default query: {query}")

    # Build the agent (default or prompt-specific)
    agent = None
    if prompt_name:
        from .agent import create_planner_agent
        agent = create_planner_agent(prompt_name)
        print(f"Using prompt: {prompt_name}")
    else:
        print("Using prompt: default")

    print(f"\nRunning deep_search planner...")
    print(f"Query: {query[:100]}...")
    print("Please wait...\n")

    # Build real deps
    from shared.db.client import get_supabase_client
    from .deps import build_search_deps

    supabase = get_supabase_client()
    deps = await build_search_deps(
        user_id="cli-test-user",
        conversation_id="cli-test-conv",
        case_id=None,
        supabase=supabase,
    )

    # Run the agent
    from .runner import handle_deep_search_turn

    result, events = await handle_deep_search_turn(
        message=query,
        deps=deps,
        agent=agent,
    )

    print(format_result(result, events))

    # Show log location
    logs = list_logs(1)
    if logs:
        print(f"\nFull log: agents/logs/deep_search/{logs[0]}.json")


if __name__ == "__main__":
    asyncio.run(main())
