"""CLI test runner for deep_search_v2 (revised) agent.

Usage:
    python -m agents.deep_search_v2.cli "your Arabic query here"
    python -m agents.deep_search_v2.cli                             # uses default test query
    python -m agents.deep_search_v2.cli --mock "test query"         # uses mock results for all tools
    python -m agents.deep_search_v2.cli --rerank "query"            # enable Jina reranker (off by default)
    python -m agents.deep_search_v2.cli --list-logs                 # list recent run logs
    python -m agents.deep_search_v2.cli --read-log LOG_ID           # read a specific log
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path

# Force UTF-8 output on Windows to handle Arabic text
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from .logger import LOGS_DIR


# -- Mock results for --mock mode ----------------------------------------------

MOCK_ALL_RESULTS = {
    "regulations": """\
## نتائج البحث في الأنظمة واللوائح (تجريبي)
**الجودة: جيدة**

### 1. المادة 77 من نظام العمل
**النظام:** نظام العمل السعودي
**المادة:** 77
**النص:** إذا أُنهي العقد لسبب غير مشروع كان للطرف الذي أصابه ضرر من هذا الإنهاء \
الحق في تعويض تقدره هيئة تسوية الخلافات العمالية.
**المرجع:** ART-LABOR-77

### 2. المادة 80 من نظام العمل
**النظام:** نظام العمل السعودي
**المادة:** 80
**النص:** لا يجوز لصاحب العمل فسخ العقد دون مكافأة أو إشعار العامل أو تعويضه \
إلا في حالات محددة على سبيل الحصر.
**المرجع:** ART-LABOR-80

---
**المصادر:**
- ART-LABOR-77 | المادة 77 | نظام العمل السعودي
- ART-LABOR-80 | المادة 80 | نظام العمل السعودي
""",
    "cases": """\
## نتائج البحث في السوابق القضائية (تجريبي)
**الجودة: متوسطة**

### 1. حكم محكمة العمل - القضية رقم 1445/3/2001
**المحكمة:** المحكمة العمالية بالرياض
**الملخص:** قضت المحكمة بأحقية العامل في تعويض عن الفصل التعسفي.
**المرجع:** CASE-1445-3-2001
""",
    "compliance": """\
## نتائج البحث في الخدمات الحكومية (تجريبي)
**الجودة: متوسطة**

### 1. خدمة تسوية الخلافات العمالية - ودّي
**الجهة:** وزارة الموارد البشرية
**المرجع:** SVC-WADDI-001
""",
}


# -- CLI helpers ---------------------------------------------------------------


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


# -- Main ----------------------------------------------------------------------


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
                loops = log.get("usage", {}).get("search_loops", "?")
                query = log.get("input", {}).get("message", "")[:60]
                print(f"  {log_id}  [{status}]  {duration}s  loops={loops}  {query}...")
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

    # Check for flags
    use_mock = "--mock" in sys.argv
    use_reranker = "--rerank" in sys.argv

    # Get query from args
    query_parts = []
    skip_next = False
    for i, arg in enumerate(sys.argv[1:], 1):
        if skip_next:
            skip_next = False
            continue
        if arg in ("--mock", "--list-logs", "--rerank"):
            continue
        if arg == "--read-log":
            skip_next = True
            continue
        if not arg.startswith("--"):
            query_parts.append(arg)

    query = " ".join(query_parts) if query_parts else ""

    if not query:
        query = "ما هي حقوق العامل في حالة الفصل التعسفي؟"
        print(f"Using default query: {query}")

    print(f"\nRunning deep_search_v2 (revised) -- hybrid search (BM25 + semantic RRF)...")
    print(f"Query: {query[:100]}...")
    print(f"Mock mode: {'ON' if use_mock else 'OFF'}")
    print(f"Jina reranker: {'ON' if use_reranker else 'OFF'}")
    print("Please wait...\n")

    # Build real deps
    from shared.db.client import get_supabase_client

    from .graph import build_search_deps, handle_deep_search_turn

    supabase = get_supabase_client()
    deps = await build_search_deps(
        user_id="cli-test-user",
        conversation_id="cli-test-conv",
        case_id=None,
        supabase=supabase,
    )

    # Set reranker flag
    deps.use_reranker = use_reranker

    # Inject mock results if --mock
    if use_mock:
        deps.mock_results = MOCK_ALL_RESULTS
        print("Mock results injected for all tools.\n")

    # Run the agent
    result, events = await handle_deep_search_turn(
        message=query,
        deps=deps,
    )

    print(format_result(result, events))

    # Show log location
    logs = list_logs(1)
    if logs:
        print(f"\nFull log: agents/deep_search_v2/logs/{logs[0]}.json")


if __name__ == "__main__":
    asyncio.run(main())
