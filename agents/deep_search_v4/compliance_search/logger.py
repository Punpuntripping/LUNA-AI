"""Run logger for compliance_search agent.

Per-run directory matching reg_search logging pattern:

    logs/{log_id}/
        run.json                        # Full machine-readable JSON
        run.md                          # Overview: timeline, rounds, final result
        expander/
            round_1.md                  # Expander output per round
        search/
            round_1_q1_{slug}.md        # Raw search results per query per round
        reranker/
            round_1.md                  # Reranker decisions per round
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import ComplianceSearchResult, ExpanderOutput, ServiceRerankerOutput

logger = logging.getLogger(__name__)

import os as _os
_default_logs = Path(__file__).resolve().parent / "reports"
_logs_override = _os.environ.get("LUNA_DEEP_SEARCH_LOGS_DIR")
LOGS_DIR = (Path(_logs_override) / "v4_compliance_search") if _logs_override else _default_logs
QUALITY_LABELS = {"strong": "قوية", "moderate": "متوسطة", "weak": "ضعيفة"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_log_id() -> str:
    """Create a flat timestamp log_id (used when no URA query_id available)."""
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def make_ura_log_id(query_id: int | str) -> str:
    """Create a query-namespaced log_id: query_{id}/{timestamp}."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"query_{query_id}/{ts}"


def _slugify(text: str, max_len: int = 20) -> str:
    cleaned = re.sub(r"[^\w\s]", "", text, flags=re.UNICODE).strip()[:max_len]
    return re.sub(r"\s+", "_", cleaned) or "query"


def _format_usage(usage: dict) -> str:
    req = usage.get("requests", 0)
    inp = usage.get("input_tokens", 0)
    out = usage.get("output_tokens", 0)
    tot = usage.get("total_tokens", 0)
    return (
        f"| Requests | Input tokens | Output tokens | Total tokens |\n"
        f"|----------|-------------|---------------|-------------|\n"
        f"| {req} | {inp:,} | {out:,} | {tot:,} |"
    )


def _extract_reasoning(messages_json: bytes | None) -> list[str]:
    if not messages_json:
        return []
    try:
        messages = json.loads(messages_json)
    except Exception:
        return []
    blocks: list[str] = []
    for msg in messages:
        for part in msg.get("parts", []):
            if part.get("part_kind", "") in ("thinking", "reasoning") and part.get("content"):
                blocks.append(part["content"])
    return blocks


# ---------------------------------------------------------------------------
# Directory setup
# ---------------------------------------------------------------------------

def create_run_dir(log_id: str) -> Path:
    run_dir = LOGS_DIR / log_id
    (run_dir / "expander").mkdir(parents=True, exist_ok=True)
    (run_dir / "search").mkdir(parents=True, exist_ok=True)
    (run_dir / "reranker").mkdir(parents=True, exist_ok=True)
    return run_dir


# ---------------------------------------------------------------------------
# Per-node markdown writers
# ---------------------------------------------------------------------------

def save_expander_md(
    log_id: str,
    round_num: int,
    system_prompt: str,
    user_message: str,
    output: ExpanderOutput,
    usage: dict,
    messages_json: bytes | None = None,
) -> None:
    run_dir = LOGS_DIR / log_id
    path = run_dir / "expander" / f"round_{round_num}.md"

    lines: list[str] = []
    lines.append(f"# Expander — Round {round_num}")
    lines.append("")
    lines.append("## Usage")
    lines.append(_format_usage(usage))
    lines.append("")

    lines.append(f"## Output — {len(output.queries)} queries (task_count={output.task_count})")
    lines.append("")
    for i, q in enumerate(output.queries, 1):
        rat = output.rationales[i - 1] if i <= len(output.rationales) else ""
        lines.append(f"{i}. {q}")
        if rat:
            lines.append(f"   > {rat}")
    lines.append("")

    reasoning_blocks = _extract_reasoning(messages_json)
    if reasoning_blocks:
        lines.append(f"## Reasoning ({len(reasoning_blocks)} block(s))")
        lines.append("")
        for block in reasoning_blocks:
            lines.append(block)
            lines.append("")

    lines.append("<details><summary>System Prompt</summary>")
    lines.append("")
    lines.append(f"```\n{system_prompt}\n```")
    lines.append("")
    lines.append("</details>")
    lines.append("")

    lines.append(f"<details><summary>User Message ({len(user_message):,} chars)</summary>")
    lines.append("")
    lines.append(user_message)
    lines.append("")
    lines.append("</details>")
    lines.append("")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        logger.debug("Expander MD saved -> %s", path)
    except Exception as e:
        logger.warning("Failed to save expander MD: %s", e)


def save_search_query_md(
    log_id: str,
    round_num: int,
    query_index: int,
    query: str,
    results: list[dict],
    rationale: str = "",
) -> None:
    slug = _slugify(query)
    path = LOGS_DIR / log_id / "search" / f"round_{round_num}_q{query_index}_{slug}.md"

    lines: list[str] = []
    lines.append(f"# Search — Round {round_num}, Query {query_index}")
    lines.append("")
    lines.append(f"**Query:** {query}")
    if rationale:
        lines.append(f"**Rationale:** {rationale}")
    lines.append(f"**Results:** {len(results)}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for i, row in enumerate(results, 1):
        ref = row.get("service_ref", "?")
        name = row.get("service_name_ar", "?")
        provider = row.get("provider_name", "")
        score = row.get("score", 0.0)
        ctx = row.get("service_context", "")
        lines.append(f"### {i}. {name}")
        lines.append(f"- **ref:** {ref} | **score:** {score:.4f}")
        if provider:
            lines.append(f"- **provider:** {provider}")
        if ctx:
            lines.append(f"\n> {ctx[:500]}")
        lines.append("")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        logger.debug("Search MD saved -> %s", path)
    except Exception as e:
        logger.warning("Failed to save search MD: %s", e)


def save_reranker_per_query_mds(
    log_id: str,
    round_num: int,
    queries: list[str],
    rationales: list[str],
    per_query_service_refs: "dict[str, list[str]]",
    all_results_flat: list[dict],
    output: "ServiceRerankerOutput",
) -> None:
    """Write one reranker MD per expander query (mirrors reg_search layout).

    Each file is ``reranker/round_{N}_q{i}_{slug}.md`` and shows which of that
    query's retrieved services were kept or dropped by the fused reranker.
    """
    # position (1-based) → row dict and service_ref
    pos_to_row = {i + 1: row for i, row in enumerate(all_results_flat)}
    pos_to_ref = {pos: (row.get("service_ref") or "") for pos, row in pos_to_row.items()}

    # decision lookup: service_ref → decision object
    ref_to_dec: dict[str, Any] = {}
    for dec in output.decisions:
        ref = pos_to_ref.get(dec.position, "")
        if ref:
            ref_to_dec[ref] = dec

    run_dir = LOGS_DIR / log_id
    for qi, query in enumerate(queries, 1):
        slug = _slugify(query)
        path = run_dir / "reranker" / f"round_{round_num}_q{qi}_{slug}.md"
        rationale = rationales[qi - 1] if qi <= len(rationales) else ""

        # Service refs retrieved by this query
        query_refs = set(per_query_service_refs.get(query, []))

        kept_rows: list[tuple[dict, Any]] = []
        dropped_rows: list[tuple[dict, Any]] = []
        for ref in query_refs:
            row = next((r for r in all_results_flat if r.get("service_ref") == ref), None)
            if row is None:
                continue
            dec = ref_to_dec.get(ref)
            if dec and dec.action == "keep":
                kept_rows.append((row, dec))
            else:
                dropped_rows.append((row, dec))

        # Sort kept by score desc
        kept_rows.sort(key=lambda t: float(t[0].get("score", 0.0) or 0.0), reverse=True)

        lines: list[str] = []
        lines.append(f"# Reranker — Round {round_num}, Query {qi}")
        lines.append("")
        lines.append(f"**Query:** {query}")
        if rationale:
            lines.append(f"**Rationale:** {rationale}")
        lines.append(f"**Sufficient:** {output.sufficient}  *(global round decision)*")
        lines.append(f"**Kept from this query:** {len(kept_rows)}")
        lines.append(f"**Dropped from this query:** {len(dropped_rows)}")
        if output.summary_note:
            lines.append(f"**Note:** {output.summary_note}")
        lines.append("")

        if kept_rows:
            lines.append(f"## Kept ({len(kept_rows)})")
            lines.append("")
            for row, dec in kept_rows:
                name = row.get("service_name_ar", "?")
                ref = row.get("service_ref", "?")
                provider = row.get("provider_name", "")
                rel = dec.relevance if dec else "?"
                reasoning = dec.reasoning if dec else ""
                lines.append(f"### {name}")
                lines.append(f"- **ref:** {ref} | **relevance:** {rel}")
                if provider:
                    lines.append(f"- **provider:** {provider}")
                if reasoning:
                    lines.append(f"- **reasoning:** {reasoning}")
                lines.append("")

        if dropped_rows:
            lines.append(f"## Dropped ({len(dropped_rows)})")
            lines.append("")
            for row, dec in dropped_rows:
                name = row.get("service_name_ar", "?")
                reasoning = (dec.reasoning if dec else "")[:80]
                lines.append(f"- {name} — {reasoning}")
            lines.append("")

        if output.weak_axes:
            lines.append(f"## Weak Axes ({len(output.weak_axes)})")
            lines.append("")
            for ax in output.weak_axes:
                lines.append(f"- **{ax.reason}** → {ax.suggested_query}")
            lines.append("")

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(lines), encoding="utf-8")
            logger.debug("Per-query reranker MD saved -> %s", path)
        except Exception as e:
            logger.warning("Failed to save per-query reranker MD: %s", e)


def save_reranker_md(
    log_id: str,
    round_num: int,
    user_message: str,
    output: "ServiceRerankerOutput",
    all_results_flat: list[dict],
    usage: dict,
    messages_json: bytes | None = None,
) -> None:
    path = LOGS_DIR / log_id / "reranker" / f"round_{round_num}.md"

    kept = [d for d in output.decisions if d.action == "keep"]
    dropped = [d for d in output.decisions if d.action == "drop"]

    lines: list[str] = []
    lines.append(f"# Reranker — Round {round_num}")
    lines.append("")
    lines.append(f"**Sufficient:** {output.sufficient}")
    lines.append(f"**Kept:** {len(kept)} | **Dropped:** {len(dropped)}")
    if output.summary_note:
        lines.append(f"**Note:** {output.summary_note}")
    lines.append("")

    lines.append("## Usage")
    lines.append(_format_usage(usage))
    lines.append("")

    if kept:
        lines.append(f"## Kept Services ({len(kept)})")
        lines.append("")
        for dec in kept:
            idx = dec.position - 1
            row = all_results_flat[idx] if 0 <= idx < len(all_results_flat) else {}
            name = row.get("service_name_ar", "?")
            ref = row.get("service_ref", "?")
            lines.append(f"### {dec.position}. {name}")
            lines.append(f"- **ref:** {ref} | **relevance:** {dec.relevance or '?'}")
            if dec.reasoning:
                lines.append(f"- **reasoning:** {dec.reasoning}")
            lines.append("")

    if dropped:
        lines.append(f"## Dropped ({len(dropped)})")
        lines.append("")
        for dec in dropped:
            idx = dec.position - 1
            row = all_results_flat[idx] if 0 <= idx < len(all_results_flat) else {}
            name = row.get("service_name_ar", "?")
            lines.append(f"- {dec.position}. {name} — {dec.reasoning[:80]}")
        lines.append("")

    if output.weak_axes:
        lines.append(f"## Weak Axes ({len(output.weak_axes)})")
        lines.append("")
        for i, ax in enumerate(output.weak_axes, 1):
            lines.append(f"{i}. **{ax.reason}**")
            lines.append(f"   suggested: {ax.suggested_query}")
        lines.append("")

    reasoning_blocks = _extract_reasoning(messages_json)
    if reasoning_blocks:
        lines.append(f"## Reasoning ({len(reasoning_blocks)} block(s))")
        lines.append("")
        for block in reasoning_blocks:
            lines.append(block)
            lines.append("")

    lines.append(f"<details><summary>User Message ({len(user_message):,} chars)</summary>")
    lines.append("")
    lines.append(user_message)
    lines.append("")
    lines.append("</details>")
    lines.append("")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        logger.debug("Reranker MD saved -> %s", path)
    except Exception as e:
        logger.warning("Failed to save reranker MD: %s", e)


# ---------------------------------------------------------------------------
# Run overview
# ---------------------------------------------------------------------------

def save_run_md(
    log_id: str,
    focus_instruction: str,
    duration_s: float,
    result: ComplianceSearchResult,
    round_summaries: list[dict],
) -> None:
    run_dir = LOGS_DIR / log_id
    path = run_dir / "run.md"
    qlabel = QUALITY_LABELS.get(result.quality, result.quality)

    lines: list[str] = []
    lines.append(f"# compliance_search — {log_id}")
    lines.append("")
    lines.append("| | |")
    lines.append("|---|---|")
    lines.append(f"| **Duration** | {duration_s:.1f}s |")
    lines.append(f"| **Quality** | {result.quality} ({qlabel}) |")
    lines.append(f"| **Rounds** | {result.rounds_used} |")
    lines.append(f"| **Queries** | {len(result.queries_used)} |")
    lines.append(f"| **Services kept** | {len(result.kept_results)} |")
    lines.append("")

    lines.append("## Focus")
    lines.append(f"> {focus_instruction[:300]}")
    lines.append("")

    lines.append("## Timeline")
    lines.append("")
    for rs in round_summaries:
        rn = rs.get("round", "?")
        lines.append(f"### Round {rn}")
        for q in rs.get("expander_queries", []):
            lines.append(f"  - exp: {q}")
        total = rs.get("search_total", 0)
        if total:
            lines.append(f"  - search: {total} unique services")
        kept = rs.get("reranker_kept", 0)
        suf = rs.get("reranker_sufficient", False)
        lines.append(f"  - reranker: kept={kept}, sufficient={suf}")
        weak = rs.get("weak_axes_count", 0)
        if weak:
            lines.append(f"  - weak axes: {weak}")
        lines.append("")

    lines.append("## All Queries")
    lines.append("")
    for i, q in enumerate(result.queries_used, 1):
        lines.append(f"{i}. {q}")
    lines.append("")

    lines.append("## Kept Services")
    lines.append("")
    for r in result.kept_results:
        # Support both typed RerankedServiceResult and legacy dict
        if hasattr(r, "relevance"):
            rel = r.relevance
            name = r.title
            ref = r.service_ref
        else:
            rel = r.get("_relevance", "?")
            name = r.get("service_name_ar", "?")
            ref = r.get("service_ref", "?")
        lines.append(f"- [{rel}] {name} (ref:{ref})")
    lines.append("")

    lines.append("## Files")
    lines.append("")
    if run_dir.exists():
        for sd in sorted(run_dir.iterdir()):
            if sd.is_dir():
                for f in sorted(sd.iterdir()):
                    if f.suffix in (".md", ".json"):
                        lines.append(f"- [{sd.name}/{f.name}]({sd.name}/{f.name})")
    lines.append("")

    try:
        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Run MD saved -> %s", path)
    except Exception as e:
        logger.warning("Failed to save run MD: %s", e)


def save_run_json(
    log_id: str,
    focus_instruction: str,
    user_context: str,
    duration_s: float,
    result: ComplianceSearchResult,
    events: list[dict],
    search_results_log: list[dict] | None = None,
    inner_usage: list[dict] | None = None,
    round_summaries: list[dict] | None = None,
    error: str | None = None,
) -> None:
    run_dir = LOGS_DIR / log_id
    path = run_dir / "run.json"

    ts = datetime.now(timezone.utc).isoformat()
    total_in = sum(u.get("input_tokens", 0) for u in (inner_usage or []))
    total_out = sum(u.get("output_tokens", 0) for u in (inner_usage or []))

    kept = result.kept_results or []

    log_data: dict[str, Any] = {
        "log_id": log_id,
        "timestamp": ts,
        "agent": "compliance_search",
        "status": "error" if error else "success",
        "duration_seconds": round(duration_s, 2),
        "input": {
            "focus_instruction": focus_instruction,
            "user_context": user_context,
        },
        "result": {
            "quality": result.quality,
            "rounds": result.rounds_used,
            "queries": result.queries_used,
            "kept_results_count": len(kept),
            "kept_results_summary": [
                (
                    {
                        "service_ref": r.service_ref,
                        "service_name_ar": r.title,
                        "relevance": r.relevance,
                        "reasoning": r.reasoning[:200],
                        "score": r.score,
                    }
                    if hasattr(r, "service_ref")
                    else {
                        "service_ref": r.get("service_ref", ""),
                        "service_name_ar": r.get("service_name_ar", ""),
                        "relevance": r.get("_relevance", ""),
                        "reasoning": (r.get("_reasoning", "") or "")[:200],
                        "score": r.get("score", 0.0),
                    }
                )
                for r in kept
            ],
        },
        "cost": {
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
            "total_tokens": total_in + total_out,
            "per_agent": inner_usage or [],
        },
        "rounds": round_summaries or [],
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


# ---------------------------------------------------------------------------
# Backward-compat shim (called by loop.py at end of run)
# ---------------------------------------------------------------------------

def save_run_log(
    log_id: str,
    focus_instruction: str,
    user_context: str,
    duration_s: float,
    result: ComplianceSearchResult,
    events: list[dict],
    search_results_log: list[dict] | None = None,
    inner_usage: list[dict] | None = None,
    round_summaries: list[dict] | None = None,
    error: str | None = None,
) -> None:
    """End-of-run logger: saves run.json + run.md."""
    create_run_dir(log_id)
    save_run_json(
        log_id=log_id,
        focus_instruction=focus_instruction,
        user_context=user_context,
        duration_s=duration_s,
        result=result,
        events=events,
        search_results_log=search_results_log,
        inner_usage=inner_usage,
        round_summaries=round_summaries,
        error=error,
    )
    save_run_md(
        log_id=log_id,
        focus_instruction=focus_instruction,
        duration_s=duration_s,
        result=result,
        round_summaries=round_summaries or [],
    )
