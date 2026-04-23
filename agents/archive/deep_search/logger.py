"""Structured run logger for deep_search planner agent (v3).

Each request produces a folder under agents/logs/deep_search/{log_id}/:
  run.json              — basic JSON summary (metadata, usage, timing)
  planner.md            — planner agent trace (input, thinking, tool calls, output)
  reg_1.md, reg_2.md    — per search_regulations call
  cases_1.md            — per search_cases_courts call
  services_1.md         — per search_compliance call
  similarity_search/    — subfolder with per-query retrieval logs
    reg_1_query_1.md
    reg_1_query_2.md
    cases_1_query_1.md
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from agents.models import PlannerResult, TaskContinue, TaskEnd

logger = logging.getLogger(__name__)

LOGS_DIR = Path(__file__).resolve().parent.parent / "deep_search_v3" / "logs"

# -- Tool name to short prefix mapping ----------------------------------------

_TOOL_PREFIX = {
    "search_regulations": "reg",
    "search_cases_courts": "cases",
    "search_compliance": "services",
}


# -- Public API ---------------------------------------------------------------


def save_run_log(
    log_id: str,
    message: str,
    task_history: list[dict] | None,
    result: TaskContinue | TaskEnd,
    events: list[dict],
    duration_s: float,
    tool_logs: list[dict] | None = None,
    usage: dict | None = None,
    planner_output: PlannerResult | None = None,
    model_messages_json: bytes | None = None,
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

        think_steps = _extract_think_steps(model_messages) if model_messages else []
        tool_logs = tool_logs or []

        # 1. run.json — compact summary
        _write_run_json(
            run_dir, log_id, message, task_history, result, events,
            duration_s, usage, planner_output, error, tool_logs, think_steps,
        )

        # 2. planner.md — full planner trace
        _write_planner_md(
            run_dir, log_id, message, task_history, result,
            duration_s, usage, planner_output, error, tool_logs, think_steps,
        )

        # 3. Per-tool markdown files + similarity search subfolder
        _write_tool_files(run_dir, tool_logs)

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
    planner_output: PlannerResult | None,
    error: str | None,
    tool_logs: list[dict],
    think_steps: list[dict],
) -> None:
    """Write compact JSON summary (no model_messages — those are huge)."""
    data: dict = {
        "log_id": log_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": "deep_search_planner",
        "status": "error" if error else "success",
        "duration_seconds": round(duration_s, 2),
        "input": {"message": message, "task_history_turns": len(task_history) if task_history else 0},
    }

    if error:
        data["error"] = error
    if usage:
        data["usage"] = usage

    if planner_output:
        data["planner_result"] = {
            "task_done": planner_output.task_done,
            "end_reason": planner_output.end_reason,
            "answer_ar_preview": (planner_output.answer_ar or "")[:200],
            "search_summary": planner_output.search_summary,
        }

    if isinstance(result, TaskEnd):
        data["result"] = {"type": "TaskEnd", "reason": result.reason}
    elif isinstance(result, TaskContinue):
        data["result"] = {"type": "TaskContinue"}

    data["events_count"] = len(events)
    data["tool_calls_summary"] = [
        {"tool": t["tool"], "duration_s": t.get("duration_s"), "mock": t.get("mock")}
        for t in tool_logs if t.get("tool") != "think"
    ]
    data["think_steps_count"] = len(think_steps)

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
    planner_output: PlannerResult | None,
    error: str | None,
    tool_logs: list[dict],
    think_steps: list[dict],
) -> None:
    """Write planner.md — sequential trace of the planner agent."""
    lines: list[str] = []
    lines.append(f"# Deep Search Planner — {log_id}")
    lines.append("")
    lines.append(f"**Status:** {'ERROR' if error else 'Success'}")
    lines.append(f"**Duration:** {round(duration_s, 2)}s")
    if usage:
        lines.append(
            f"**Tokens:** {usage.get('input_tokens', '?')} in / "
            f"{usage.get('output_tokens', '?')} out / "
            f"{usage.get('total_tokens', '?')} total"
        )
    lines.append("")

    # -- Input
    lines.append("---")
    lines.append("## Input")
    lines.append("")
    lines.append("**User Query:**")
    lines.append(f"> {message}")
    lines.append("")
    if task_history:
        lines.append(f"**Task History:** {len(task_history)} previous turns")
    lines.append("")

    # -- Execution timeline (interleave thinks and tool calls in order)
    lines.append("---")
    lines.append("## Execution Timeline")
    lines.append("")

    # Build ordered timeline from tool_logs
    # tool_logs are already in chronological order (appended during execution)
    tool_counters: dict[str, int] = {}
    think_idx = 0
    step = 0

    for tlog in tool_logs:
        tool_name = tlog.get("tool", "")

        if tool_name == "think":
            step += 1
            lines.append(f"### Step {step} — Think")
            lines.append("")
            lines.append(f"> {tlog.get('thought', '')}")
            lines.append("")
            continue

        # Search tool
        prefix = _TOOL_PREFIX.get(tool_name, tool_name)
        tool_counters[tool_name] = tool_counters.get(tool_name, 0) + 1
        call_num = tool_counters[tool_name]
        step += 1

        lines.append(f"### Step {step} — {tool_name} (call #{call_num})")
        lines.append("")
        lines.append(f"**Query:** `{tlog.get('query', '')}`")
        lines.append(f"**Duration:** {tlog.get('duration_s', '?')}s")
        if tlog.get("mock"):
            lines.append("**Source:** Mock")
        if tlog.get("error"):
            lines.append(f"**Error:** {tlog['error']}")
        lines.append(f"**Output:** {tlog.get('output_length', '?')} chars")
        lines.append("")

        # Reference to per-tool file
        lines.append(f"*Full output: [{prefix}_{call_num}.md]({prefix}_{call_num}.md)*")
        lines.append("")

        # Similarity search references
        sim_logs = tlog.get("similarity_logs", [])
        if sim_logs:
            lines.append(f"**Similarity Searches:** {len(sim_logs)} queries")
            for qi, sl in enumerate(sim_logs, 1):
                lines.append(
                    f"  - Query {qi}: {sl.get('total_candidates', '?')} candidates "
                    f"-> {sl.get('unfolded_count', sl.get('top_n', '?'))} results "
                    f"({sl.get('duration_s', '?')}s)"
                )
            lines.append("")

    # -- Output
    lines.append("---")
    lines.append("## Output")
    lines.append("")

    if error:
        lines.append(f"**Error:** {error}")
    elif planner_output:
        lines.append(f"**Task Done:** {planner_output.task_done}")
        lines.append(f"**End Reason:** {planner_output.end_reason}")
        lines.append("")
        lines.append("**Answer (Arabic):**")
        lines.append(f"> {planner_output.answer_ar or '(empty)'}")
        lines.append("")
        if planner_output.search_summary:
            lines.append("**Search Summary:**")
            lines.append(f"> {planner_output.search_summary}")
            lines.append("")
        if planner_output.artifact_md:
            preview = planner_output.artifact_md[:300]
            lines.append(f"**Artifact Preview** ({len(planner_output.artifact_md)} chars):")
            lines.append("```")
            lines.append(preview)
            if len(planner_output.artifact_md) > 300:
                lines.append("...")
            lines.append("```")
    elif isinstance(result, TaskEnd):
        lines.append(f"**TaskEnd:** reason={result.reason}")
        lines.append(f"> {result.last_response or ''}")
    elif isinstance(result, TaskContinue):
        lines.append(f"**TaskContinue:**")
        lines.append(f"> {result.response or ''}")

    (run_dir / "planner.md").write_text("\n".join(lines), encoding="utf-8")


# -- Per-tool markdown files ---------------------------------------------------


def _write_tool_files(run_dir: Path, tool_logs: list[dict]) -> None:
    """Write per-tool markdown files and similarity_search/ subfolder."""
    tool_counters: dict[str, int] = {}

    for tlog in tool_logs:
        tool_name = tlog.get("tool", "")
        if tool_name == "think":
            continue  # think steps go in planner.md only

        prefix = _TOOL_PREFIX.get(tool_name, tool_name)
        tool_counters[tool_name] = tool_counters.get(tool_name, 0) + 1
        call_num = tool_counters[tool_name]

        # Write tool file (e.g. reg_1.md, cases_1.md, services_1.md)
        _write_single_tool_md(run_dir, prefix, call_num, tlog)

        # Write similarity search files
        sim_logs = tlog.get("similarity_logs", [])
        if sim_logs:
            sim_dir = run_dir / "similarity_search"
            sim_dir.mkdir(exist_ok=True)
            for qi, sl in enumerate(sim_logs, 1):
                _write_similarity_md(sim_dir, prefix, call_num, qi, sl)


def _write_single_tool_md(
    run_dir: Path, prefix: str, call_num: int, tlog: dict
) -> None:
    """Write a single tool call markdown file (e.g. reg_1.md)."""
    lines: list[str] = []
    tool_name = tlog.get("tool", prefix)

    lines.append(f"# {tool_name} — Call #{call_num}")
    lines.append("")
    lines.append(f"**Timestamp:** {tlog.get('timestamp', '?')}")
    lines.append(f"**Duration:** {tlog.get('duration_s', '?')}s")
    if tlog.get("mock"):
        lines.append("**Source:** Mock (hardcoded results)")
    lines.append("")

    # Input
    lines.append("---")
    lines.append("## Input")
    lines.append("")
    lines.append(f"> {tlog.get('query', '(no query)')}")
    lines.append("")

    # Error
    if tlog.get("error"):
        lines.append("---")
        lines.append("## Error")
        lines.append("")
        lines.append(f"```\n{tlog['error']}\n```")
        lines.append("")

    # Output
    lines.append("---")
    lines.append("## Output")
    lines.append("")
    lines.append(f"**Length:** {tlog.get('output_length', '?')} chars")
    lines.append("")
    preview = tlog.get("output_preview", "")
    if preview:
        lines.append("```markdown")
        lines.append(preview)
        lines.append("```")

    # Similarity search summary
    sim_logs = tlog.get("similarity_logs", [])
    if sim_logs:
        lines.append("")
        lines.append("---")
        lines.append("## Similarity Searches")
        lines.append("")
        for qi, sl in enumerate(sim_logs, 1):
            lines.append(
                f"- **Query {qi}:** `{sl.get('query', '')[:80]}` — "
                f"{sl.get('total_candidates', '?')} candidates, "
                f"{sl.get('duration_s', '?')}s "
                f"([detail](similarity_search/{prefix}_{call_num}_query_{qi}.md))"
            )

    filename = f"{prefix}_{call_num}.md"
    (run_dir / filename).write_text("\n".join(lines), encoding="utf-8")


def _write_similarity_md(
    sim_dir: Path, prefix: str, call_num: int, query_num: int, sl: dict
) -> None:
    """Write a similarity search markdown file."""
    lines: list[str] = []

    lines.append(f"# Similarity Search — {prefix}_{call_num} / Query {query_num}")
    lines.append("")
    lines.append(f"**Status:** {sl.get('status', '?')}")
    lines.append(f"**Duration:** {sl.get('duration_s', '?')}s")
    lines.append("")

    # Input
    lines.append("---")
    lines.append("## Input")
    lines.append("")
    lines.append(f"**Query:** {sl.get('query', '')}")
    lines.append(f"**Match Count:** {sl.get('match_count', '?')}")
    lines.append(f"**Top N:** {sl.get('top_n', '?')}")
    lines.append("")

    # Retrieval counts
    lines.append("---")
    lines.append("## Retrieval")
    lines.append("")
    lines.append(f"- **Articles:** {sl.get('articles_count', 0)} candidates")
    lines.append(f"- **Sections:** {sl.get('sections_count', 0)} candidates")
    lines.append(f"- **Regulations:** {sl.get('regulations_count', 0)} candidates")
    lines.append(f"- **Total Candidates:** {sl.get('total_candidates', 0)}")
    lines.append(f"- **After Unfolding:** {sl.get('unfolded_count', '?')} results")
    lines.append("")

    # Top results table
    top_results = sl.get("top_results", [])
    if top_results:
        lines.append("---")
        lines.append("## Top Results (After Reranking)")
        lines.append("")
        lines.append("| # | Type | Title | Distance | Reranker Score |")
        lines.append("|---|------|-------|----------|----------------|")
        for i, r in enumerate(top_results, 1):
            title = r.get("title", "—")[:60]
            lines.append(
                f"| {i} | {r.get('source_type', '?')} | {title} | "
                f"{r.get('distance', '—')} | {r.get('reranker_score', '—')} |"
            )
    lines.append("")

    filename = f"{prefix}_{call_num}_query_{query_num}.md"
    (sim_dir / filename).write_text("\n".join(lines), encoding="utf-8")


# -- Think step extraction (from model messages) ------------------------------


def _extract_think_steps(model_messages: list[dict]) -> list[dict]:
    """Extract think() tool calls from model messages into a readable timeline."""
    think_steps: list[dict] = []
    search_count = 0
    step = 0

    for msg in model_messages:
        parts = msg.get("parts", [])
        for part in parts:
            if part.get("part_kind") == "tool-call":
                tool_name = part.get("tool_name", "")
                if tool_name in ("search_regulations", "search_cases_courts", "search_compliance"):
                    search_count += 1
                elif tool_name == "think":
                    step += 1
                    round_num = max(1, min(search_count, 3))
                    args = part.get("args", {})
                    thought = args.get("thought", "") if isinstance(args, dict) else str(args)
                    think_steps.append({
                        "step": step,
                        "thought": thought,
                        "after_round": round_num,
                    })

    return think_steps
