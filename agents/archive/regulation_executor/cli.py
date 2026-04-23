"""CLI test runner for regulation_executor agent.

Usage:
    python -m agents.regulation_executor.cli "your Arabic query here"
    python -m agents.regulation_executor.cli                          # uses default test query
    python -m agents.regulation_executor.cli --list-logs              # list recent run logs
    python -m agents.regulation_executor.cli --read-log LOG_ID        # read a specific log
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from .logger import LOGS_DIR


def format_result(result_md: str) -> str:
    """Format the run result for terminal display."""
    lines = []
    lines.append(f"\n{'=' * 60}")
    lines.append("Regulation Executor Result")
    lines.append(f"{'=' * 60}\n")
    lines.append(result_md)
    lines.append(f"\n{'=' * 60}")
    lines.append(f"Result length: {len(result_md)} chars")
    return "\n".join(lines)


def list_logs(limit: int = 20) -> list[str]:
    """List recent log IDs."""
    if not LOGS_DIR.exists():
        return []
    files = sorted(LOGS_DIR.glob("*.json"), reverse=True)
    return [f.stem for f in files[:limit]]


def read_log(log_id: str) -> dict:
    """Read a log entry."""
    log_file = LOGS_DIR / f"{log_id}.json"
    if log_file.exists():
        return json.loads(log_file.read_text(encoding="utf-8"))
    return {"error": f"Log {log_id} not found"}


async def main():
    """Main CLI entry point."""
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
                query = log.get("input", {}).get("query", "")[:60]
                quality = log.get("agent_output", {}).get("quality", "?") if isinstance(log.get("agent_output"), dict) else "?"
                print(f"  {log_id}  [{status}]  {duration}s  quality={quality}  {query}...")
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

    # Get query from args or use default
    if len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        query = " ".join(sys.argv[1:])
    else:
        query = "ما هي حقوق العامل في حالة الفصل التعسفي؟"
        print(f"Using default query: {query}")

    print(f"\nRunning regulation_executor...")
    print(f"Query: {query[:100]}...")
    print("Please wait...\n")

    # Build real deps
    import httpx

    from shared.config import get_settings
    from shared.db.client import get_supabase_client
    from agents.utils.embeddings import embed_regulation_query

    settings = get_settings()
    supabase = get_supabase_client()

    from .deps import RegulationSearchDeps

    deps = RegulationSearchDeps(
        supabase=supabase,
        embedding_fn=embed_regulation_query,
        jina_api_key=settings.JINA_RERANKER_API_KEY or "",
        http_client=httpx.AsyncClient(timeout=10.0),
    )

    # Run the agent
    from .runner import run_regulation_search

    result_md = await run_regulation_search(
        query=query,
        deps=deps,
    )

    print(format_result(result_md))

    # Show log location
    logs = list_logs(1)
    if logs:
        print(f"\nFull log: agents/logs/regulation_executor/{logs[0]}.json")

    # Clean up httpx client
    await deps.http_client.aclose()


if __name__ == "__main__":
    import os
    # Ensure UTF-8 output on Windows
    if sys.platform == "win32":
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    asyncio.run(main())
