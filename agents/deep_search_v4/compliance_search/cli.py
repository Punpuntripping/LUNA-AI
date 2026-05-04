"""CLI test runner for compliance_search.

Usage:
    python -m agents.deep_search_v4.compliance_search.cli "your Arabic query here"
    python -m agents.deep_search_v4.compliance_search.cli                          # uses default test query
    python -m agents.deep_search_v4.compliance_search.cli --rerank "query"         # enable Jina reranker
    python -m agents.deep_search_v4.compliance_search.cli --mock "query"           # use mock results
    python -m agents.deep_search_v4.compliance_search.cli --verbose "query"        # extra debug info
    python -m agents.deep_search_v4.compliance_search.cli --list-logs              # list recent run logs
    python -m agents.deep_search_v4.compliance_search.cli --read-log LOG_ID        # read a specific log
"""
from __future__ import annotations

import asyncio
import json
import sys
import time as _time
from pathlib import Path

from .logger import LOGS_DIR


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def format_result(result) -> str:
    """Format ComplianceSearchResult for terminal display."""
    quality_labels = {
        "strong": "قوية",
        "moderate": "متوسطة",
        "weak": "ضعيفة",
    }

    lines: list[str] = []
    lines.append(f"\n{'=' * 60}")
    lines.append(f"Quality: {result.quality} ({quality_labels.get(result.quality, '')})")
    lines.append(f"Rounds: {result.rounds_used}")
    lines.append(f"Queries: {len(result.queries_used)}")
    lines.append(f"Kept services: {len(result.kept_results)}")
    lines.append(f"{'=' * 60}\n")

    # Queries used
    lines.append("Queries used:")
    for i, q in enumerate(result.queries_used, 1):
        lines.append(f"  {i}. {q}")
    lines.append("")

    # Kept services
    if result.kept_results:
        lines.append(f"Kept services ({len(result.kept_results)}):")
        for i, row in enumerate(result.kept_results, 1):
            ref = row.get("service_ref", "?")
            name = row.get("service_name", row.get("name", "?"))
            relevance = row.get("_relevance", "")
            score = row.get("score", 0.0)
            lines.append(f"  {i}. [{relevance}] {name} -- ref:{ref} (score={score:.3f})")
        lines.append("")

    # Show service_context preview for top result
    if result.kept_results:
        top = result.kept_results[0]
        ctx = top.get("service_context", "")
        if ctx:
            lines.append(f"{'~' * 40}")
            lines.append(f"Top service context preview ({len(ctx)} chars):")
            lines.append(ctx[:600])
            if len(ctx) > 600:
                lines.append(f"... ({len(ctx) - 600} more chars)")

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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
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
                quality = log.get("result", {}).get("quality", "?")
                rounds = log.get("result", {}).get("rounds", "?")
                focus = log.get("input", {}).get("focus_instruction", "")[:60]
                print(
                    f"  {log_id}  [{status}]  {duration}s  "
                    f"quality={quality}  rounds={rounds}  {focus}..."
                )
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

    # Parse flags
    use_reranker = "--rerank" in sys.argv
    use_mock = "--mock" in sys.argv
    verbose = "--verbose" in sys.argv

    # Get query from args or use default
    query_parts = []
    skip_next = False
    for i, arg in enumerate(sys.argv[1:], 1):
        if skip_next:
            skip_next = False
            continue
        if arg in ("--list-logs", "--rerank", "--mock", "--verbose"):
            continue
        if arg == "--read-log":
            skip_next = True
            continue
        if not arg.startswith("--"):
            query_parts.append(arg)

    query = " ".join(query_parts) if query_parts else ""

    if not query:
        query = "كيف أنقل كفالة عامل عبر منصة قوى؟"
        print(f"Using default query: {query}")

    print(f"\nRunning compliance_search -- government services search loop...")
    print(f"Query: {query[:100]}...")
    print(f"Jina reranker: {'ON' if use_reranker else 'OFF'}")
    print(f"Mock results: {'ON' if use_mock else 'OFF'}")
    print("Please wait...\n")

    # Build deps
    from agents.utils.embeddings import embed_regulation_query_alibaba as embed_text
    from shared.config import get_settings
    from shared.db.client import get_supabase_client

    from .models import ComplianceSearchDeps

    settings = get_settings()
    supabase = get_supabase_client()

    mock_results = None
    if use_mock:
        mock_results = {
            "compliance": (
                "# نتائج وهمية للاختبار\n\n"
                "## خدمة نقل خدمات عامل -- منصة قوى\n"
                "**المنصة:** قوى\n"
                "**الجهة:** وزارة الموارد البشرية\n"
                "**الرابط:** https://www.qiwa.sa\n\n"
                "الخطوات:\n"
                "1. الدخول على منصة قوى\n"
                "2. اختيار خدمة نقل الخدمات\n"
                "3. إدخال بيانات العامل\n"
                "4. تقديم الطلب\n"
            ),
        }

    deps = ComplianceSearchDeps(
        supabase=supabase,
        embedding_fn=embed_text,
        jina_api_key=getattr(settings, "JINA_API_KEY", ""),
        use_reranker=use_reranker,
        mock_results=mock_results,
    )

    # Run the search loop
    from .loop import run_compliance_search

    t0 = _time.perf_counter()
    result = await run_compliance_search(
        focus_instruction=query,
        user_context="",
        deps=deps,
    )
    duration = _time.perf_counter() - t0

    # Print result
    print(format_result(result))
    print(f"\nTotal duration: {duration:.1f}s")

    if verbose:
        print(f"\n{'~' * 40}")
        print(f"Events collected: {len(deps._events)}")
        for e in deps._events:
            print(f"  [{e.get('type', '?')}] {json.dumps(e, ensure_ascii=False)[:120]}")

    # Show log location
    logs = list_logs(1)
    if logs:
        print(f"\nFull log: agents/deep_search_v3/compliance_search/logs/{logs[0]}.json")


if __name__ == "__main__":
    asyncio.run(main())
