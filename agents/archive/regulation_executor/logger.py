"""Structured run logger for regulation_executor agent (v2).

Each request produces a folder under agents/logs/regulation_executor/{log_id}/:
  run.json                — basic JSON summary (metadata, usage, timing)
  planner.md              — executor agent trace (input, tool calls, output)
  aggregator_gemini.md    — Gemini model synthesis
  aggregator_minimax.md   — MiniMax model synthesis
  aggregator_fallback.md  — fallback model synthesis (if triggered)
  similarity_search/      — subfolder with per-query retrieval logs
    query_1.md            — initial retrieval pipeline
    query_2.md            — re-search via search_and_retrieve tool
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs" / "regulation_executor"


# -- Public API ---------------------------------------------------------------


def save_run_log(
    log_id: str,
    query: str,
    duration_s: float,
    usage: dict | None = None,
    agent_output: object | None = None,
    model_messages_json: bytes | None = None,
    formatted_result: str | None = None,
    error: str | None = None,
    model_used: str | None = None,
    retrieval_logs: list[dict] | None = None,
    run_dir: Path | None = None,
) -> Path | None:
    """Save a structured run log for regulation_executor.

    If run_dir is provided, writes into that folder (shared across models).
    Otherwise creates a new folder from log_id.

    Returns the run directory Path, or None on failure.
    """
    try:
        if run_dir is None:
            run_dir = LOGS_DIR / log_id
        run_dir.mkdir(parents=True, exist_ok=True)

        model_name = model_used or "unknown"
        retrieval_logs = retrieval_logs or []

        # Parse model messages
        model_messages = None
        if model_messages_json:
            try:
                model_messages = json.loads(model_messages_json)
            except Exception:
                pass

        # 1. run.json — update/merge (multiple models write to same folder)
        _update_run_json(
            run_dir, log_id, query, duration_s, usage,
            agent_output, error, model_name, retrieval_logs,
        )

        # 2. Aggregator file per model
        _write_aggregator_md(
            run_dir, model_name, query, duration_s, usage,
            agent_output, formatted_result, error, model_messages,
        )

        # 3. Similarity search files (only write once, from first call)
        if retrieval_logs:
            sim_dir = run_dir / "similarity_search"
            sim_dir.mkdir(exist_ok=True)
            for qi, rl in enumerate(retrieval_logs, 1):
                sim_file = sim_dir / f"query_{qi}.md"
                if not sim_file.exists():  # Don't overwrite from second model
                    _write_similarity_md(sim_file, qi, rl)

        logger.info("Run logged -> %s (%s)", run_dir, model_name)
        return run_dir

    except Exception as e:
        logger.warning("Failed to save run log %s: %s", log_id, e)
        return None


def save_planner_md(
    run_dir: Path,
    log_id: str,
    query: str,
    retrieval_duration_s: float,
    retrieval_logs: list[dict],
    models_summary: list[dict],
    total_duration_s: float,
    chosen_model: str,
    error: str | None = None,
) -> None:
    """Write planner.md — the orchestration trace for the full run."""
    try:
        lines: list[str] = []
        lines.append(f"# Regulation Executor — Planner Log")
        lines.append("")
        lines.append(f"**Run ID:** {log_id}")
        lines.append(f"**Status:** {'ERROR' if error else 'Success'}")
        lines.append(f"**Total Duration:** {round(total_duration_s, 2)}s")
        lines.append("")

        # Input
        lines.append("---")
        lines.append("## Input")
        lines.append("")
        lines.append(f"> {query}")
        lines.append("")

        # Phase 1: Retrieval
        lines.append("---")
        lines.append("## Phase 1 — Mechanical Retrieval")
        lines.append("")
        lines.append(f"**Duration:** {round(retrieval_duration_s, 2)}s")
        lines.append(f"**Queries:** {len(retrieval_logs)}")
        lines.append("")

        for qi, rl in enumerate(retrieval_logs, 1):
            lines.append(
                f"- **Query {qi}:** `{rl.get('query', '')[:80]}` — "
                f"{rl.get('total_candidates', '?')} candidates, "
                f"{rl.get('unfolded_count', '?')} unfolded "
                f"([detail](similarity_search/query_{qi}.md))"
            )
        lines.append("")

        # Phase 2: Dual-model synthesis
        lines.append("---")
        lines.append("## Phase 2 — Dual-Model Synthesis")
        lines.append("")

        for ms in models_summary:
            model = ms.get("model", "?")
            status = "Success" if not ms.get("error") else "Failed"
            dur = ms.get("duration_s", "?")
            quality = ms.get("quality", "?")
            tokens = ms.get("total_tokens", "?")
            lines.append(f"### {model}")
            lines.append(f"- **Status:** {status}")
            lines.append(f"- **Duration:** {dur}s")
            lines.append(f"- **Quality:** {quality}")
            lines.append(f"- **Tokens:** {tokens}")
            if ms.get("error"):
                lines.append(f"- **Error:** {ms['error']}")
            lines.append(f"- *Detail: [aggregator_{_sanitize_model(model)}.md](aggregator_{_sanitize_model(model)}.md)*")
            lines.append("")

        # Result
        lines.append("---")
        lines.append("## Result")
        lines.append("")
        lines.append(f"**Chosen Model:** {chosen_model}")
        if error:
            lines.append(f"**Error:** {error}")
        lines.append("")

        (run_dir / "planner.md").write_text("\n".join(lines), encoding="utf-8")

    except Exception as e:
        logger.warning("Failed to write planner.md: %s", e)


# -- run.json (merge-friendly) ------------------------------------------------


def _update_run_json(
    run_dir: Path,
    log_id: str,
    query: str,
    duration_s: float,
    usage: dict | None,
    agent_output: object | None,
    error: str | None,
    model_name: str,
    retrieval_logs: list[dict],
) -> None:
    """Write or update run.json with per-model results."""
    json_path = run_dir / "run.json"

    # Load existing if present (multiple models write to same file)
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    else:
        data = {
            "log_id": log_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": "regulation_executor",
            "input": {"query": query},
            "models": {},
        }

    # Add/update this model's entry
    model_entry: dict = {
        "status": "error" if error else "success",
        "duration_seconds": round(duration_s, 2),
    }
    if error:
        model_entry["error"] = error
    if usage:
        model_entry["usage"] = usage
    if agent_output and hasattr(agent_output, "quality"):
        model_entry["quality"] = agent_output.quality

    data.setdefault("models", {})[model_name] = model_entry

    # Retrieval info (write once)
    if retrieval_logs and "retrieval" not in data:
        data["retrieval"] = {
            "queries": len(retrieval_logs),
            "total_candidates": sum(rl.get("total_candidates", 0) for rl in retrieval_logs),
        }

    json_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
    )


# -- Aggregator markdown per model --------------------------------------------


def _write_aggregator_md(
    run_dir: Path,
    model_name: str,
    query: str,
    duration_s: float,
    usage: dict | None,
    agent_output: object | None,
    formatted_result: str | None,
    error: str | None,
    model_messages: list[dict] | None,
) -> None:
    """Write aggregator_{model}.md — one model's synthesis."""
    safe_name = _sanitize_model(model_name)
    lines: list[str] = []

    lines.append(f"# Aggregator — {model_name}")
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

    # Input
    lines.append("---")
    lines.append("## Input")
    lines.append("")
    lines.append(f"> {query[:200]}")
    lines.append("")

    # Error
    if error:
        lines.append("---")
        lines.append("## Error")
        lines.append("")
        lines.append(f"```\n{error}\n```")
        lines.append("")

    # Output
    if agent_output and hasattr(agent_output, "quality"):
        lines.append("---")
        lines.append("## Agent Output")
        lines.append("")
        lines.append(f"**Quality:** {agent_output.quality}")
        if hasattr(agent_output, "summary_md"):
            preview = (agent_output.summary_md or "")[:500]
            lines.append("")
            lines.append("**Summary Preview:**")
            lines.append("```markdown")
            lines.append(preview)
            if len(agent_output.summary_md or "") > 500:
                lines.append("...")
            lines.append("```")
        if hasattr(agent_output, "citations") and agent_output.citations:
            lines.append("")
            lines.append(f"**Citations:** {len(agent_output.citations)}")
            for c in agent_output.citations[:10]:
                lines.append(f"- {c.ref} | {c.title or '—'}")

    # Formatted result
    if formatted_result:
        lines.append("")
        lines.append("---")
        lines.append("## Formatted Result")
        lines.append("")
        lines.append(f"**Length:** {len(formatted_result)} chars")
        lines.append("")
        lines.append("```markdown")
        lines.append(formatted_result[:800])
        if len(formatted_result) > 800:
            lines.append("...")
        lines.append("```")

    # Tool calls from model messages
    if model_messages:
        tool_calls = _extract_tool_calls(model_messages)
        if tool_calls:
            lines.append("")
            lines.append("---")
            lines.append("## Tool Calls")
            lines.append("")
            for tc in tool_calls:
                lines.append(f"- **{tc['tool']}**: `{tc.get('query', '')[:80]}`")

    (run_dir / f"aggregator_{safe_name}.md").write_text(
        "\n".join(lines), encoding="utf-8",
    )


# -- Similarity search markdown -----------------------------------------------


def _write_similarity_md(file_path: Path, query_num: int, rl: dict) -> None:
    """Write a similarity search markdown file."""
    lines: list[str] = []

    lines.append(f"# Similarity Search — Query {query_num}")
    lines.append("")
    lines.append(f"**Status:** {rl.get('status', '?')}")
    lines.append(f"**Duration:** {rl.get('duration_s', '?')}s")
    lines.append("")

    # Input
    lines.append("---")
    lines.append("## Input")
    lines.append("")
    lines.append(f"**Query:** {rl.get('query', '')}")
    lines.append(f"**Match Count:** {rl.get('match_count', '?')}")
    lines.append(f"**Top N:** {rl.get('top_n', '?')}")
    lines.append("")

    # Retrieval counts
    lines.append("---")
    lines.append("## Retrieval")
    lines.append("")
    lines.append(f"- **Articles:** {rl.get('articles_count', 0)} candidates")
    lines.append(f"- **Sections:** {rl.get('sections_count', 0)} candidates")
    lines.append(f"- **Regulations:** {rl.get('regulations_count', 0)} candidates")
    lines.append(f"- **Total Candidates:** {rl.get('total_candidates', 0)}")
    lines.append(f"- **After Unfolding:** {rl.get('unfolded_count', '?')} results")
    lines.append("")

    # Top results table
    top_results = rl.get("top_results", [])
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

    file_path.write_text("\n".join(lines), encoding="utf-8")


# -- Helpers -------------------------------------------------------------------


def _sanitize_model(model_name: str) -> str:
    """Convert model name to safe filename component."""
    return model_name.replace("/", "_").replace(" ", "_").replace(".", "_")


def _extract_tool_calls(model_messages: list[dict]) -> list[dict]:
    """Extract tool call summaries from model messages."""
    calls: list[dict] = []
    for msg in model_messages:
        for part in msg.get("parts", []):
            if part.get("part_kind") == "tool-call":
                args = part.get("args", {})
                query = args.get("query", "") if isinstance(args, dict) else ""
                calls.append({
                    "tool": part.get("tool_name", "?"),
                    "query": query,
                })
    return calls
