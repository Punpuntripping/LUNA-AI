"""Structured run logger for deep_search_v2 agent.

Each request produces a folder under agents/deep_search_v2/logs/{log_id}/:
  run.json                     — compact JSON summary (metadata, usage, timing)
  planner.md                   — PlanAgent reasoning, tool calls, decisions
  query_expansion.md           — ExpanderNode output per round (queries + rationale)
  aggregator.md                — AggregatorNode round 1 (coverage, synthesis, citations)
  aggregator_2.md              — AggregatorNode round 2 (if loop iterated)
  similarity_search/           — subfolder with per-query raw search results
    reg_query_1.md             — regulations search result
    reg_query_2.md
    cases_query_1.md
    services_query_1.md
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from agents.models import PlannerResult, TaskContinue, TaskEnd

logger = logging.getLogger(__name__)

LOGS_DIR = Path(__file__).resolve().parent / "logs"

# Tool name to prefix mapping for similarity search files
_TOOL_PREFIX = {
    "regulations": "reg",
    "cases": "cases",
    "compliance": "services",
}


# -- Public API ---------------------------------------------------------------


def save_run_log(
    log_id: str,
    message: str,
    task_history: list[dict] | None,
    result: TaskContinue | TaskEnd,
    events: list[dict],
    duration_s: float,
    usage: dict | None = None,
    agent_output: PlannerResult | None = None,
    model_messages_json: bytes | None = None,
    loop_results: list[dict] | None = None,
    error: str | None = None,
) -> Path | None:
    """Save a structured run log — folder with JSON + markdown files.

    Returns the run directory Path, or None on failure.
    """
    try:
        run_dir = LOGS_DIR / log_id
        run_dir.mkdir(parents=True, exist_ok=True)

        # Parse model messages once
        model_messages = None
        if model_messages_json:
            try:
                model_messages = json.loads(model_messages_json)
            except Exception:
                pass

        loop_results = loop_results or []
        cost = _build_cost_summary(usage, loop_results)

        # 1. run.json
        _write_run_json(
            run_dir, log_id, message, task_history, result, events,
            duration_s, usage, agent_output, error, loop_results, cost,
        )

        # 2. planner.md
        _write_planner_md(
            run_dir, log_id, message, task_history, result,
            duration_s, usage, agent_output, error, loop_results,
            model_messages, cost,
        )

        # 3. query_expansion.md
        _write_query_expansion_md(run_dir, loop_results, model_messages)

        # 4. aggregator.md (per round)
        _write_aggregator_mds(run_dir, loop_results, model_messages)

        # 5. similarity_search/ subfolder
        _write_similarity_search(run_dir, loop_results)

        logger.info("Run logged -> %s", run_dir)
        return run_dir

    except Exception as e:
        logger.warning("Failed to save run log %s: %s", log_id, e)
        return None


# -- run.json -----------------------------------------------------------------


def _write_run_json(
    run_dir: Path,
    log_id: str,
    message: str,
    task_history: list[dict] | None,
    result: TaskContinue | TaskEnd,
    events: list[dict],
    duration_s: float,
    usage: dict | None,
    agent_output: PlannerResult | None,
    error: str | None,
    loop_results: list[dict],
    cost: dict,
) -> None:
    """Write compact JSON summary."""
    data: dict = {
        "log_id": log_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": "deep_search_v2",
        "status": "error" if error else "success",
        "duration_seconds": round(duration_s, 2),
        "input": {
            "message": message,
            "task_history_turns": len(task_history) if task_history else 0,
        },
    }

    if error:
        data["error"] = error
    if usage:
        data["usage"] = usage

    data["cost_summary"] = cost

    if agent_output:
        data["planner_result"] = {
            "task_done": agent_output.task_done,
            "end_reason": agent_output.end_reason,
            "answer_ar_preview": (agent_output.answer_ar or "")[:200],
            "search_summary": agent_output.search_summary,
        }

    if isinstance(result, TaskEnd):
        data["result"] = {"type": "TaskEnd", "reason": result.reason}
    elif isinstance(result, TaskContinue):
        data["result"] = {"type": "TaskContinue"}

    data["events_count"] = len(events)
    data["loops_count"] = len(loop_results)
    data["loops_summary"] = [
        {
            "sub_question": lr.get("sub_question", "")[:80],
            "rounds_used": lr.get("rounds_used", 0),
            "queries_count": sum(
                len(eq.get("queries", []))
                for eq in lr.get("expander_queries", [])
            ),
        }
        for lr in loop_results
    ]

    (run_dir / "run.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
    )


# -- planner.md ---------------------------------------------------------------


def _write_planner_md(
    run_dir: Path,
    log_id: str,
    message: str,
    task_history: list[dict] | None,
    result: TaskContinue | TaskEnd,
    duration_s: float,
    usage: dict | None,
    agent_output: PlannerResult | None,
    error: str | None,
    loop_results: list[dict],
    model_messages: list[dict] | None,
    cost: dict,
) -> None:
    """Write planner.md — PlanAgent's sequential trace."""
    lines: list[str] = []
    lines.append(f"# Deep Search v2 — Planner Log")
    lines.append("")
    lines.append(f"**Run ID:** {log_id}")
    lines.append(f"**Status:** {'ERROR' if error else 'Success'}")
    lines.append(f"**Duration:** {round(duration_s, 2)}s")
    lines.append(f"**Search Loops:** {len(loop_results)}")

    totals = cost.get("totals", {})
    if totals:
        lines.append(
            f"**Total Tokens:** {totals.get('total_tokens', '?')} "
            f"({totals.get('total_input_tokens', '?')} in / "
            f"{totals.get('total_output_tokens', '?')} out)"
        )
    lines.append("")

    # Input
    lines.append("---")
    lines.append("## Input")
    lines.append("")
    lines.append("**User Query:**")
    lines.append(f"> {message}")
    if task_history:
        lines.append(f"\n**Task History:** {len(task_history)} previous turns")
    lines.append("")

    # PlanAgent thinking (from model_messages)
    if model_messages:
        thinking_blocks = _extract_thinking_from_messages(model_messages)
        if thinking_blocks:
            lines.append("---")
            lines.append("## PlanAgent Reasoning")
            lines.append("")
            for i, block in enumerate(thinking_blocks, 1):
                lines.append(f"### Request {block['request']}")
                lines.append("")
                # Truncate very long thinking to keep planner.md readable
                content = block["content"]
                if len(content) > 800:
                    lines.append(f"> {content[:800]}...")
                else:
                    lines.append(f"> {content}")
                lines.append("")

    # Loop invocations
    lines.append("---")
    lines.append("## Execution Timeline")
    lines.append("")

    for i, lr in enumerate(loop_results, 1):
        sub_q = lr.get("sub_question", "")
        rounds = lr.get("rounds_used", 0)
        answer = lr.get("answer_summary", "")

        lines.append(f"### Loop {i} — `{sub_q[:80]}`")
        lines.append("")
        lines.append(f"**Rounds:** {rounds}")

        # Show expander queries summary
        for eq in lr.get("expander_queries", []):
            round_num = eq.get("round", "?")
            queries = eq.get("queries", [])
            lines.append(f"**Round {round_num} queries:** {len(queries)}")
            for q in queries:
                lines.append(f"  - [{q.get('tool', '?')}] `{q.get('query', '')[:60]}`")

        if answer:
            lines.append(f"**Answer:** {answer[:200]}")
        lines.append("")
        lines.append(f"*Details: [query_expansion.md](query_expansion.md), "
                      f"[aggregator.md](aggregator.md)*")
        lines.append("")

    # Output
    lines.append("---")
    lines.append("## Output")
    lines.append("")

    if error:
        lines.append(f"**Error:** {error}")
    elif agent_output:
        lines.append(f"**Task Done:** {agent_output.task_done}")
        lines.append(f"**End Reason:** {agent_output.end_reason}")
        lines.append("")
        lines.append("**Answer:**")
        lines.append(f"> {(agent_output.answer_ar or '(empty)')[:300]}")
        if agent_output.artifact_md:
            lines.append(f"\n**Artifact:** {len(agent_output.artifact_md)} chars")

    (run_dir / "planner.md").write_text("\n".join(lines), encoding="utf-8")


# -- query_expansion.md -------------------------------------------------------


def _write_query_expansion_md(
    run_dir: Path,
    loop_results: list[dict],
    model_messages: list[dict] | None,
) -> None:
    """Write query_expansion.md — all ExpanderNode outputs across loops and rounds."""
    has_data = any(lr.get("expander_queries") for lr in loop_results)
    if not has_data:
        return

    lines: list[str] = []
    lines.append("# Query Expansion — ExpanderNode")
    lines.append("")

    for loop_idx, lr in enumerate(loop_results, 1):
        sub_q = lr.get("sub_question", "")
        lines.append(f"## Loop {loop_idx} — `{sub_q[:80]}`")
        lines.append("")

        # Expander thinking (from inner_thinking)
        for entry in lr.get("inner_thinking", []):
            if entry.get("agent") == "expander":
                round_num = entry.get("round", "?")
                lines.append(f"### Expander Thinking — Round {round_num}")
                lines.append("")
                for t in entry.get("thinking", []):
                    lines.append(f"> {t[:500]}")
                lines.append("")

        # Expander queries
        for eq in lr.get("expander_queries", []):
            round_num = eq.get("round", "?")
            status_msg = eq.get("status_message", "")

            lines.append(f"### Round {round_num} — Generated Queries")
            lines.append("")
            if status_msg:
                lines.append(f"*{status_msg}*")
                lines.append("")

            lines.append("| # | Tool | Query | Rationale |")
            lines.append("|---|------|-------|-----------|")
            for qi, q in enumerate(eq.get("queries", []), 1):
                tool = q.get("tool", "?")
                query = q.get("query", "")[:80]
                rationale = q.get("rationale", "")[:80]
                lines.append(f"| {qi} | {tool} | {query} | {rationale} |")
            lines.append("")

    (run_dir / "query_expansion.md").write_text("\n".join(lines), encoding="utf-8")


# -- aggregator.md (per round) ------------------------------------------------


def _write_aggregator_mds(
    run_dir: Path,
    loop_results: list[dict],
    model_messages: list[dict] | None,
) -> None:
    """Write aggregator.md (and aggregator_2.md, etc.) per aggregation round."""
    # Collect all aggregator entries across loops
    agg_entries: list[dict] = []

    for loop_idx, lr in enumerate(loop_results, 1):
        # Aggregator thinking
        for entry in lr.get("inner_thinking", []):
            if entry.get("agent") == "aggregator":
                agg_entries.append({
                    "loop": loop_idx,
                    "round": entry.get("round", 1),
                    "thinking": entry.get("thinking", []),
                    "sub_question": lr.get("sub_question", ""),
                    "report_md": lr.get("report_md", ""),
                    "answer_summary": lr.get("answer_summary", ""),
                    "citations": lr.get("citations", []),
                })

    # If no thinking entries, still write one per loop with available data
    if not agg_entries:
        for loop_idx, lr in enumerate(loop_results, 1):
            if lr.get("report_md") or lr.get("answer_summary"):
                agg_entries.append({
                    "loop": loop_idx,
                    "round": 1,
                    "thinking": [],
                    "sub_question": lr.get("sub_question", ""),
                    "report_md": lr.get("report_md", ""),
                    "answer_summary": lr.get("answer_summary", ""),
                    "citations": lr.get("citations", []),
                })

    if not agg_entries:
        return

    # Group by round number for file naming
    for idx, entry in enumerate(agg_entries):
        suffix = f"_{idx + 1}" if idx > 0 else ""
        filename = f"aggregator{suffix}.md"

        lines: list[str] = []
        lines.append(f"# Aggregator — Loop {entry['loop']}, Round {entry['round']}")
        lines.append("")
        lines.append(f"**Sub-question:** {entry['sub_question']}")
        lines.append("")

        # Thinking
        if entry["thinking"]:
            lines.append("---")
            lines.append("## Thinking")
            lines.append("")
            for t in entry["thinking"]:
                if len(t) > 1000:
                    lines.append(f"> {t[:1000]}...")
                else:
                    lines.append(f"> {t}")
                lines.append("")

        # Answer summary
        if entry["answer_summary"]:
            lines.append("---")
            lines.append("## Answer Summary")
            lines.append("")
            lines.append(entry["answer_summary"])
            lines.append("")

        # Report
        if entry["report_md"]:
            lines.append("---")
            lines.append("## Report")
            lines.append("")
            lines.append(entry["report_md"])
            lines.append("")

        # Citations
        if entry["citations"]:
            lines.append("---")
            lines.append(f"## Citations ({len(entry['citations'])})")
            lines.append("")
            for ci, cite in enumerate(entry["citations"], 1):
                ref = cite.get("ref", "")
                title = cite.get("title", "")
                source_type = cite.get("source_type", "")
                regulation = cite.get("regulation_title", "")
                parts = [f"**{ref}**" if ref else f"#{ci}"]
                if title:
                    parts.append(title)
                if regulation:
                    parts.append(f"({regulation})")
                lines.append(f"- {' — '.join(parts)}")
            lines.append("")

        (run_dir / filename).write_text("\n".join(lines), encoding="utf-8")


# -- similarity_search/ -------------------------------------------------------


def _write_similarity_search(
    run_dir: Path,
    loop_results: list[dict],
) -> None:
    """Write per-query search result files into similarity_search/ subfolder."""
    has_results = any(lr.get("search_results_log") for lr in loop_results)
    if not has_results:
        return

    sim_dir = run_dir / "similarity_search"
    sim_dir.mkdir(exist_ok=True)

    # Track query counts per tool for naming (reg_query_1, reg_query_2, etc.)
    tool_counters: dict[str, int] = {}

    for loop_idx, lr in enumerate(loop_results, 1):
        for sr in lr.get("search_results_log", []):
            tool = sr.get("tool", "unknown")
            round_num = sr.get("round", 1)
            query = sr.get("query", "")
            result_count = sr.get("result_count", 0)
            is_mock = sr.get("is_mock", False)
            raw_md = sr.get("raw_markdown", "")

            prefix = _TOOL_PREFIX.get(tool, tool)
            tool_counters[tool] = tool_counters.get(tool, 0) + 1
            query_num = tool_counters[tool]

            lines: list[str] = []
            lines.append(f"# Similarity Search — {tool} / Query {query_num}")
            lines.append("")
            lines.append(f"**Loop:** {loop_idx}")
            lines.append(f"**Round:** {round_num}")
            lines.append(f"**Results:** {result_count}")
            if is_mock:
                lines.append("**Source:** Mock")
            lines.append("")

            lines.append("---")
            lines.append("## Query")
            lines.append("")
            lines.append(f"> {query}")
            lines.append("")

            lines.append("---")
            lines.append("## Raw Results")
            lines.append("")
            lines.append(raw_md if raw_md else "(empty)")
            lines.append("")

            filename = f"{prefix}_query_{query_num}.md"
            (sim_dir / filename).write_text("\n".join(lines), encoding="utf-8")


# -- Helpers -------------------------------------------------------------------


def _build_cost_summary(
    usage: dict | None,
    loop_results: list[dict],
) -> dict:
    """Build detailed cost breakdown across all agents."""
    cost: dict = {
        "plan_agent": {},
        "inner_agents": [],
        "totals": {
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_tokens": 0,
            "total_requests": 0,
        },
    }

    if usage:
        pa_in = usage.get("plan_agent_input_tokens", 0) or 0
        pa_out = usage.get("plan_agent_output_tokens", 0) or 0
        pa_total = usage.get("plan_agent_total_tokens", 0) or 0
        pa_reqs = usage.get("plan_agent_requests", 0) or 0
        cost["plan_agent"] = {
            "requests": pa_reqs,
            "input_tokens": pa_in,
            "output_tokens": pa_out,
            "total_tokens": pa_total,
        }
        cost["totals"]["total_input_tokens"] += pa_in
        cost["totals"]["total_output_tokens"] += pa_out
        cost["totals"]["total_tokens"] += pa_total
        cost["totals"]["total_requests"] += pa_reqs

    for lr in loop_results:
        for iu in lr.get("inner_usage", []):
            cost["inner_agents"].append(iu)
            cost["totals"]["total_input_tokens"] += iu.get("input_tokens", 0) or 0
            cost["totals"]["total_output_tokens"] += iu.get("output_tokens", 0) or 0
            cost["totals"]["total_tokens"] += iu.get("total_tokens", 0) or 0
            cost["totals"]["total_requests"] += iu.get("requests", 0) or 0

    return cost


def _extract_thinking_from_messages(model_messages: list[dict]) -> list[dict]:
    """Extract thinking blocks from PlanAgent model messages."""
    blocks: list[dict] = []
    request_num = 0

    for msg in model_messages:
        kind = msg.get("kind", "")
        if kind == "request":
            request_num += 1

        for part in msg.get("parts", []):
            if part.get("part_kind") == "thinking" and part.get("content"):
                blocks.append({
                    "request": request_num,
                    "content": part["content"],
                })

    return blocks
