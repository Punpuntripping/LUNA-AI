"""Run logger for case_search agent.

Per-run directory:
    logs/{log_id}/
        run.json
        run.md
        expander/
            round_1.md
        search/
            round_1_q1_{slug}.md
        reranker/
            q1_{slug}.md
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pydantic_ai._usage import RunUsage

    from .models import CaseSearchResult, ExpanderOutput, RerankerQueryResult

logger = logging.getLogger(__name__)

import os as _os
_default_logs = Path(__file__).resolve().parent / "reports"
_logs_override = _os.environ.get("LUNA_DEEP_SEARCH_LOGS_DIR")
LOGS_DIR = (Path(_logs_override) / "v4_case_search") if _logs_override else _default_logs
TEST_QUERIES_PATH = Path(__file__).resolve().parent.parent.parent / "test_queries.json"


# ---------------------------------------------------------------------------
# Query resolution
# ---------------------------------------------------------------------------

def _load_test_queries() -> dict:
    if TEST_QUERIES_PATH.exists():
        return json.loads(TEST_QUERIES_PATH.read_text(encoding="utf-8"))
    return {"metadata": {}, "queries": []}


def _extract_query_text(q: dict) -> str:
    """Extract query text, handling both 'text' and 'sub_queries' formats."""
    if "text" in q:
        return q["text"]
    if "sub_queries" in q:
        return " ".join(sq["text"] for sq in q["sub_queries"] if sq.get("text"))
    return ""


def resolve_query_id(query_text: str | None, query_id: int | None = None) -> tuple[int, str]:
    """Resolve a query to its ID and text."""
    import random

    data = _load_test_queries()
    queries = data.get("queries", [])

    if query_id is not None:
        for q in queries:
            if q.get("id") == query_id:
                return query_id, _extract_query_text(q)
        raise ValueError(f"Query ID {query_id} not found in test_queries.json")

    if not query_text:
        if not queries:
            raise ValueError("No queries in test_queries.json and no query text provided")
        pick = random.choice(queries)
        return pick["id"], _extract_query_text(pick)

    for q in queries:
        if _extract_query_text(q).strip() == query_text.strip():
            return q["id"], _extract_query_text(q)

    max_id = max((q.get("id", 0) for q in queries), default=0)
    new_id = max_id + 1
    new_entry = {"id": new_id, "category": "ad-hoc", "text": query_text.strip()}
    queries.append(new_entry)
    data["queries"] = queries
    data.setdefault("metadata", {})["total_queries"] = len(queries)

    TEST_QUERIES_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Appended query id=%d to test_queries.json", new_id)

    return new_id, query_text.strip()


def make_log_id(query_id: int) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"query_{query_id}/{ts}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(text: str, max_len: int = 20) -> str:
    cleaned = re.sub(r"[^\w\s]", "", text, flags=re.UNICODE)
    cleaned = cleaned.strip()[:max_len]
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned or "query"


def _format_usage(usage: RunUsage) -> str:
    return (
        f"| Requests | Input tokens | Output tokens | Total tokens |\n"
        f"|----------|-------------|---------------|-------------|\n"
        f"| {usage.requests} | {usage.input_tokens:,} | {usage.output_tokens:,} | {usage.total_tokens:,} |"
    )


def _usage_dict(usage: RunUsage) -> dict:
    return {
        "requests": usage.requests,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
    }


# ---------------------------------------------------------------------------
# Directory setup
# ---------------------------------------------------------------------------

def create_run_dir(log_id: str) -> Path:
    """Create the base directory for a run."""
    run_dir = LOGS_DIR / log_id
    (run_dir / "search").mkdir(parents=True, exist_ok=True)
    return run_dir


# ---------------------------------------------------------------------------
# Per-node markdown writers
# ---------------------------------------------------------------------------

def save_expander_md(
    log_id: str,
    round_num: int,
    prompt_key: str,
    system_prompt: str,
    user_message: str,
    output: ExpanderOutput,
    usage: RunUsage,
    messages_json: bytes | None = None,
) -> None:
    """Save expander round markdown."""
    run_dir = LOGS_DIR / log_id
    path = run_dir / f"expander_{prompt_key}" / f"round_{round_num}.md"

    lines: list[str] = []
    lines.append(f"# Expander -- Round {round_num}")
    lines.append(f"**Prompt key:** `{prompt_key}`")
    lines.append("")
    lines.append("## Usage")
    lines.append(_format_usage(usage))
    lines.append("")
    lines.append("## System Prompt")
    lines.append(f"```\n{system_prompt}\n```")
    lines.append("")
    lines.append("## User Message")
    lines.append(user_message)
    lines.append("")
    lines.append("## Output")
    lines.append(f"**Queries ({len(output.queries)}):**")
    for i, q in enumerate(output.queries, 1):
        rationale = output.rationales[i - 1] if i <= len(output.rationales) else ""
        lines.append(f"{i}. {q}")
        if rationale:
            lines.append(f"   > {rationale}")
    lines.append("")

    if messages_json:
        lines.append("## Model Messages (raw)")
        lines.append("```json")
        try:
            parsed = json.loads(messages_json)
            lines.append(json.dumps(parsed, ensure_ascii=False, indent=2))
        except Exception:
            lines.append(messages_json.decode("utf-8", errors="replace"))
        lines.append("```")
        lines.append("")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Expander MD saved -> %s", path)
    except Exception as e:
        logger.warning("Failed to save expander MD: %s", e)


def save_search_query_md(
    log_id: str,
    round_num: int,
    query_index: int,
    query: str,
    raw_markdown: str,
    result_count: int,
    rationale: str = "",
) -> None:
    """Save individual search query results markdown."""
    slug = _slugify(query)
    run_dir = LOGS_DIR / log_id
    filename = f"round_{round_num}_q{query_index}_{slug}.md"
    path = run_dir / "search" / filename

    lines: list[str] = []
    lines.append(f"# Search -- Round {round_num}, Query {query_index}")
    lines.append("")
    lines.append(f"**Query:** {query}")
    if rationale:
        lines.append(f"**Rationale:** {rationale}")
    lines.append(f"**Results:** {result_count}")
    lines.append(f"**Markdown length:** {len(raw_markdown):,} chars")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(raw_markdown)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Search MD saved -> %s", path)
    except Exception as e:
        logger.warning("Failed to save search MD: %s", e)


def save_reranker_query_md(
    log_id: str,
    query_index: int,
    query: str,
    reranker_result: RerankerQueryResult,
    decision_log: list[dict] | None = None,
) -> None:
    """Save per-query reranker output markdown, plus a companion input file."""
    slug = _slugify(query)
    run_dir = LOGS_DIR / log_id
    path = run_dir / "reranker" / f"q{query_index}_{slug}.md"

    lines: list[str] = []
    lines.append(f"# Reranker -- Query {query_index}")
    lines.append("")
    lines.append(f"**Query:** {query}")
    if reranker_result.rationale:
        lines.append(f"**Rationale:** {reranker_result.rationale}")
    lines.append(f"**Kept:** {len(reranker_result.results)}")
    lines.append(f"**Dropped:** {reranker_result.dropped_count}")
    lines.append(f"**Sufficient:** {reranker_result.sufficient}")
    if reranker_result.summary_note:
        lines.append(f"**Summary:** {reranker_result.summary_note}")
    lines.append("")

    if decision_log:
        lines.append("## Decisions")
        lines.append("")
        lines.append("| Position | RRF | Action | Relevance |")
        lines.append("|----------|-----|--------|-----------|")
        for d in decision_log:
            lines.append(
                f"| {d.get('position', '?')} | {d.get('rrf', 0.0):.4f} | "
                f"{d.get('action', '?')} | {d.get('relevance', '-')} |"
            )
        lines.append("")

    if reranker_result.results:
        lines.append(f"## Kept Results ({len(reranker_result.results)})")
        lines.append("")
        for i, res in enumerate(reranker_result.results, 1):
            court_info = res.court or ""
            if res.city:
                court_info += f" — {res.city}"
            lines.append(f"{i}. **{court_info}** ({res.relevance})")
            if res.case_number:
                lines.append(f"   Case: {res.case_number}")
            if res.reasoning:
                lines.append(f"   > {res.reasoning}")
        lines.append("")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Reranker MD saved -> %s", path)
    except Exception as e:
        logger.warning("Failed to save reranker MD: %s", e)

    # Companion input file — the exact user_msg sent to the LLM
    round_trace: list[dict] = getattr(reranker_result, "_round_trace", None) or []
    if round_trace:
        try:
            inp_path = run_dir / "reranker" / f"q{query_index}_{slug}_input.md"
            rt = round_trace[0]
            inp_lines = [
                f"# Reranker Input — Query {query_index}",
                "",
                f"**query**: {query}",
                "",
                "## Full user message sent to LLM",
                "",
                "```",
                rt.get("user_msg", ""),
                "```",
            ]
            inp_path.write_text("\n".join(inp_lines), encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to save reranker input MD: %s", e)


# ---------------------------------------------------------------------------
# Run overview + JSON
# ---------------------------------------------------------------------------

_QUALITY_LABELS_AR = {
    "strong": "قوية",
    "moderate": "متوسطة",
    "weak": "ضعيفة",
    "pending": "قيد التقييم",
}


def save_run_overview_md(
    log_id: str,
    focus_instruction: str,
    user_context: str,
    expander_prompt_key: str,
    duration_s: float,
    result: CaseSearchResult,
    round_summaries: list[dict],
    aggregator_prompt_key: str | None = None,
    quality: str = "pending",
    citations_count: int = 0,
) -> None:
    """Save the run overview markdown.

    Schema matches the canonical template at `reports/query_test/20260416_test/run.md`:
    Duration · Quality (with Arabic label) · Rounds · Queries · Citations ·
    Expander prompt · Aggregator prompt.

    case_search has no local aggregator (synthesis happens in the shared
    deep_search_v3/aggregator/), so `quality`, `citations_count`, and
    `aggregator_prompt_key` default to "pending" / 0 / None — the downstream
    caller can enrich them if it knows better.
    """
    run_dir = LOGS_DIR / log_id
    path = run_dir / "run.md"

    total_kept = sum(len(r.results) for r in result.reranker_results) if result.reranker_results else 0
    quality_label = _QUALITY_LABELS_AR.get(quality, quality)
    agg_key_repr = f"`{aggregator_prompt_key}`" if aggregator_prompt_key else "—"

    lines: list[str] = []
    lines.append(f"# case_search -- {log_id}")
    lines.append("")
    lines.append(f"| | |")
    lines.append(f"|---|---|")
    lines.append(f"| **Duration** | {duration_s:.1f}s |")
    lines.append(f"| **Quality** | {quality} ({quality_label}) |")
    lines.append(f"| **Rounds** | {result.rounds_used} |")
    lines.append(f"| **Queries** | {len(result.queries_used)} |")
    lines.append(f"| **Citations** | {citations_count} |")
    lines.append(f"| **Reranker queries** | {len(result.reranker_results)} |")
    lines.append(f"| **Total kept** | {total_kept} |")
    lines.append(f"| **Expander prompt** | `{expander_prompt_key}` |")
    lines.append(f"| **Aggregator prompt** | {agg_key_repr} |")
    lines.append("")
    lines.append("## Focus")
    lines.append(f"> {focus_instruction}")
    if user_context:
        lines.append(f">\n> **Context:** {user_context}")
    lines.append("")
    lines.append("## Timeline")
    lines.append("")
    for rs in round_summaries:
        rn = rs.get("round", "?")
        lines.append(f"### Round {rn}")
        if rs.get("expander_queries"):
            lines.append(f"- **Expander:** {len(rs['expander_queries'])} queries")
            for i, q in enumerate(rs["expander_queries"], 1):
                lines.append(f"  {i}. {q}")
        if rs.get("search_total"):
            lines.append(f"- **Search:** {rs['search_total']} results from {rs.get('search_queries', '?')} queries")
        if rs.get("reranker_kept") is not None:
            lines.append(f"- **Reranker:** {rs['reranker_kept']} kept, {rs.get('reranker_dropped', 0)} dropped")
        lines.append("")
    lines.append("## All Queries")
    lines.append("")
    for i, q in enumerate(result.queries_used, 1):
        lines.append(f"{i}. {q}")
    lines.append("")
    lines.append("## Files")
    lines.append("")
    run_dir_path = LOGS_DIR / log_id
    if run_dir_path.exists():
        for sd in sorted(run_dir_path.iterdir()):
            if sd.is_dir():
                for f in sorted(sd.iterdir()):
                    if f.suffix == ".md":
                        lines.append(f"- [{sd.name}/{f.name}]({sd.name}/{f.name})")
    lines.append("")

    try:
        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Run overview saved -> %s", path)
    except Exception as e:
        logger.warning("Failed to save run overview: %s", e)


def save_run_json(
    log_id: str,
    focus_instruction: str,
    user_context: str,
    expander_prompt_key: str,
    duration_s: float,
    result: CaseSearchResult,
    events: list[dict],
    round_summaries: list[dict],
    search_results_log: list[dict] | None = None,
    inner_usage: list[dict] | None = None,
    error: str | None = None,
    query_id: int = 0,
    model_name: str = "",
    thinking_effort: str | None = None,
    aggregator_prompt_key: str | None = None,
    quality: str = "pending",
    citations_count: int = 0,
    summary_md: str = "",
) -> None:
    """Save the enriched JSON log.

    Schema matches `reports/query_test/20260416_test/run.json`:
    `prompt_keys.aggregator`, `result.quality`, `result.citations_count`,
    `result.summary_md_length`, `result.summary_md` are all present.

    case_search has no local aggregator so synthesis-shaped fields default
    to `quality="pending"`, `citations_count=0`, `summary_md=""`. The
    shared deep_search_v3/aggregator/ is expected to enrich the downstream
    artifact if needed — this file captures only case_search's own output.
    """
    run_dir = LOGS_DIR / log_id
    path = run_dir / "run.json"

    ts = datetime.now(timezone.utc).isoformat()

    total_in = sum(u.get("input_tokens", 0) for u in (inner_usage or []))
    total_out = sum(u.get("output_tokens", 0) for u in (inner_usage or []))
    total_kept = sum(len(r.results) for r in result.reranker_results) if result.reranker_results else 0

    log_data: dict[str, Any] = {
        "log_id": log_id,
        "query_id": query_id,
        "timestamp": ts,
        "agent": "case_search",
        "status": "error" if error else "success",
        "duration_seconds": round(duration_s, 2),
        "model": model_name,
        "thinking_effort": thinking_effort,
        "input": {
            "focus_instruction": focus_instruction,
            "user_context": user_context,
        },
        "prompt_keys": {
            "expander": expander_prompt_key,
            "aggregator": aggregator_prompt_key,
        },
        "result": {
            "quality": quality,
            "rounds": result.rounds_used,
            "queries": result.queries_used,
            "citations_count": citations_count,
            "reranker_queries": len(result.reranker_results),
            "total_kept": total_kept,
            "summary_md_length": len(summary_md),
            "summary_md": summary_md,
        },
        "cost": {
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
            "total_tokens": total_in + total_out,
            "per_agent": inner_usage or [],
        },
        "rounds": round_summaries,
        "search_results_log": search_results_log or [],
        "events": events,
    }

    if error:
        log_data["error"] = error

    try:
        path.write_text(
            json.dumps(log_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Run JSON saved -> %s", path)
    except Exception as e:
        logger.warning("Failed to save run JSON: %s", e)
