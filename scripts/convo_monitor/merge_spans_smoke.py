"""Smoke-test helper: merge saved Logfire query result dumps into a single
spans JSON the extractor can consume.

This is only used by the convo_accbc49c smoke test. The saved MCP tool-result
files contain a `{columns, rows}` envelope (or a plain row list); we union the
rows by ``span_id`` and emit a sorted JSON array.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_OUT = REPO_ROOT / "agents_reports" / "convo_accbc49c" / "raw_data" / "_logfire_spans.json"

DEFAULT_SOURCES = [
    Path(r"C:\Users\mhfal\.claude\projects\C--Programming-LUNA-AI\c9847793-38e8-4a12-a064-7e9c44786a90\tool-results\mcp-logfire-query_run-1779802679936.txt"),
    Path(r"C:\Users\mhfal\.claude\projects\C--Programming-LUNA-AI\c9847793-38e8-4a12-a064-7e9c44786a90\tool-results\mcp-logfire-query_run-1779802753354.txt"),
    Path(r"C:\Users\mhfal\.claude\projects\C--Programming-LUNA-AI\c9847793-38e8-4a12-a064-7e9c44786a90\tool-results\mcp-logfire-query_run-1779802761401.txt"),
]


def load(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "rows" in data:
        return data["rows"]
    if isinstance(data, list):
        return data
    return []


def main(argv: list[str] | None = None) -> int:
    sources = [Path(p) for p in (argv or [])] or DEFAULT_SOURCES
    out_path = DEFAULT_OUT
    out_path.parent.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    merged: list[dict] = []
    for src in sources:
        if not src.exists():
            sys.stderr.write(f"WARN: missing {src}\n")
            continue
        for row in load(src):
            sid = row.get("span_id")
            if not sid or sid in seen:
                continue
            seen.add(sid)
            merged.append(row)
    merged.sort(key=lambda r: r.get("start_timestamp") or "")
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    sys.stdout.write(f"wrote {len(merged)} unique spans -> {out_path}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
