"""Monitoring harness for ``run_full_loop`` -- captures every pipeline stage.

Mirrors the standalone CLI (``agents.deep_search_v4.cli``) but, instead of
just printing the AggregatorOutput, dumps every intermediate artifact a human
might want to inspect after the fact: expander outputs, raw DB search hits,
reranker LLM I/O, the URA, the *exact* prompt the aggregator saw, the raw
LLM completion, validation, and runtime stats.

Layout per query (``agents/deep_search_v3/monitor/query_{id}/{ts}/``):

    README.md                   <- pipeline flow + index of every stage file
    00_query.md                 <- query text + executor flags + log_ids

    10_reg_search/              (mirror of reg_search/reports/{log_id}/)
        overview.md
        expander/...
        search/...
        reranker/...
        rqr_table.md            <- per-sub-query RQR table (post-reranker)

    05_planner/                 (only when --enable-planner; populated from deps._plan + planner events)
        plan.md                 <- human-readable plan: invoke / focus / sectors / rationale
        plan.json               <- raw PlannerOutput model_dump
        derived.md              <- derived caps + aggregator prompt key (FOCUS_PROFILES lookup)

    20_compliance_search/       (mirror of compliance_search/reports/{log_id}/)
        overview.md
        expander/...
        search/...
        reranker/...
        rqr_table.md

    30_case_search/             (mirror of case_search/reports/{log_id}/)
        overview.md
        expander_*/...
        search/...
        reranker/...
        rqr_table.md

    40_ura.md                   <- merged UnifiedRetrievalArtifact

    50_aggregator/              (populated by AggregatorLogger we inject)
        input.md                <- AggregatorInput (URA + sub_queries) as fed
        prompt_*.md             <- exact system prompt + user message per call
        llm_raw_*.txt           <- raw model completion per stage (single/draft/critique/rewrite/fallback)
        thinking.md             <- <thinking> block (if any)
        synthesis.md            <- final synthesis + reference block
        references.json         <- structured references
        validation.json         <- post-validate report
        run.md                  <- aggregator run summary (timing, refs, validation)
        output.md               <- pretty-printed AggregatorOutput

    60_runtime/
        events.md               <- SSE event counts + tail (last 200)
        events.jsonl            <- every captured event, line-delimited
        per_executor_stats.md   <- duration + token totals per phase

    summary.md
    CRASH.md                    (only on exception)

Usage:
    python -m agents.deep_search_v4.monitor.run_monitor --query-id 5
    python -m agents.deep_search_v4.monitor.run_monitor --query-id 5 --query-id 10 --query-id 27

Sequential by design -- DB rate limits + LLM cost.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

# Force UTF-8 on stdout/stderr so Arabic console output doesn't trip Windows
# cp1252. (E4 — see agents/deep_search_v4/planning/PIPELINE_VALIDATION.md.)
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:
    pass

# Auto-load .env so credentials (ALIBABA_API_KEY, OPENAI_API_KEY, JINA, etc.)
# are visible to the orchestrator + model registry. Done before any agent
# imports that might read settings at import time. (E1 — see
# agents/deep_search_v4/planning/PIPELINE_VALIDATION.md.)
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()
except ImportError:
    pass  # dotenv is optional; production sets env directly.

# Configure Logfire so spans emitted by the orchestrator/planner/aggregator
# actually phone home (when LOGFIRE_TOKEN is set in .env). Same call the
# backend's main.py uses, so the monitor sees the same telemetry as prod.
try:
    from shared.observability import configure_logfire as _configure_logfire
    _configure_logfire()
except Exception:
    pass

from agents.deep_search_v4.aggregator.logger import AggregatorLogger
from agents.deep_search_v4.aggregator.models import AggregatorInput, AggregatorOutput


class _DirectAggregatorLogger(AggregatorLogger):
    """AggregatorLogger that writes straight into a chosen directory.

    The default ``AggregatorLogger`` builds its output path from
    ``base/query_{id}/log_id/variant``. The monitor wants every aggregator
    artifact to land in ``50_aggregator/`` next to the per-phase mirrors, so
    we override ``aggregator_dir`` to point at a fixed path.
    """

    def __init__(self, target_dir: Path) -> None:
        self._target_dir = target_dir
        self._target_dir.mkdir(parents=True, exist_ok=True)
        # Skip the parent constructor (which would mkdir a different path).
        # Set just enough state for the inherited writers to work.
        self.query_id = 0
        self.log_id = ""
        self.variant = None
        self.base_logs_dir = target_dir

    @property
    def aggregator_dir(self) -> Path:  # type: ignore[override]
        return self._target_dir
from agents.deep_search_v4.monitor.render_ura import (
    render_aggregator_md,
    render_ura_md,
)
from agents.deep_search_v4.orchestrator import FullLoopDeps, run_full_loop
from agents.deep_search_v4.shared.models import RerankerQueryResult
from agents.utils.embeddings import embed_regulation_query_alibaba
from shared.config import get_settings
from shared.db.client import get_supabase_client


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
DSV4_ROOT = REPO_ROOT / "agents" / "deep_search_v4"
# Monitor sessions land alongside the v4 monitor module so they're checked-out
# next to the orchestrator they exercised. Old v3 sessions remain readable at
# agents/deep_search_v3/monitor/ — this just stops new ones landing there.
MONITOR_ROOT = DSV4_ROOT / "monitor"
TEST_QUERIES = REPO_ROOT / "agents" / "test_queries.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _load_query(query_id: int) -> dict | None:
    data = json.loads(TEST_QUERIES.read_text(encoding="utf-8"))
    for q in data.get("queries", []):
        if int(q.get("id")) == int(query_id):
            return q
    return None


def _truncate_for_event(msg: Any, n: int = 240) -> str:
    s = "" if msg is None else str(msg)
    s = s.replace("\n", " ").replace("\r", " ")
    return s if len(s) <= n else s[:n] + "..."


def _read_text_safe(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return f"_(could not read {p.name}: {exc})_"


def _copytree_safe(src: Path | None, dst: Path) -> tuple[bool, str | None]:
    """Copy src tree into dst. Returns (ok, error_msg).

    Exists so a failed mirror does not lose the rest of the dump.
    """
    if src is None:
        return False, "log dir was not captured (phase may have crashed before logger init)"
    if not src.exists():
        return False, f"log dir does not exist: {src}"
    try:
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Per-stage renderers
# ---------------------------------------------------------------------------


def _write_query_md(out_dir: Path, query: dict, ts: str, deps: FullLoopDeps) -> None:
    lines = [
        "# 00 -- Query",
        "",
        f"- **query_id**: {query.get('id')}",
        f"- **category**: {query.get('category')}",
        f"- **timestamp_utc**: {ts}",
        "",
        "## Query Text",
        "",
        "```",
        query.get("text", ""),
        "```",
        "",
        "## Executor Flags",
        "",
        f"- **expander_prompt_key**: {deps.expander_prompt_key}",
        f"- **case_expander_prompt_key**: {deps.case_expander_prompt_key}",
        f"- **use_reranker**: {deps.use_reranker}",
        f"- **include_compliance**: {deps.include_compliance}",
        f"- **include_cases**: {deps.include_cases}",
        f"- **detail_level**: {deps.detail_level}",
        f"- **unfold_mode**: {deps.unfold_mode}",
        f"- **concurrency**: {deps.concurrency}",
        f"- **model_override**: {deps.model_override}",
        "",
        "## Per-phase log dirs (filled in after run)",
        "",
        f"- reg_log_dir: `{deps._reg_log_dir or '(not set)'}`",
        f"- comp_log_dir: `{deps._comp_log_dir or '(not set)'}`",
        f"- case_log_dir: `{deps._case_log_dir or '(not set)'}`",
        "",
    ]
    (out_dir / "00_query.md").write_text("\n".join(lines), encoding="utf-8")


def _rqr_table_block(title: str, rqrs: list[RerankerQueryResult]) -> str:
    """Render a list of RerankerQueryResult as a DB-style markdown table."""
    lines = [f"# {title}\n"]
    if not rqrs:
        lines.append("_(no reranker runs captured)_\n")
        return "\n".join(lines)
    for i, rqr in enumerate(rqrs, start=1):
        lines.append(f"\n## Sub-query {i} -- domain={rqr.domain}, sufficient={rqr.sufficient}")
        lines.append("")
        lines.append(f"- **query**: {rqr.query}")
        lines.append(f"- **rationale**: {rqr.rationale}")
        lines.append(f"- **dropped_count**: {rqr.dropped_count}")
        lines.append(f"- **kept_count**: {len(rqr.results or [])}")
        if rqr.summary_note:
            lines.append(f"- **summary_note**: {rqr.summary_note}")
        if getattr(rqr, "unfold_rounds", 0):
            lines.append(f"- **unfold_rounds**: {rqr.unfold_rounds}")
        if getattr(rqr, "total_unfolds", 0):
            lines.append(f"- **total_unfolds**: {rqr.total_unfolds}")
        lines.append("")
        if rqr.results:
            lines.append("| pos | ref_id | relevance | rrf_max | source_type | title | reasoning |")
            lines.append("|-----|--------|-----------|---------|-------------|-------|-----------|")
            for pos, r in enumerate(rqr.results, start=1):
                title_v = (getattr(r, "title", "") or "").replace("|", "\\|").replace("\n", " ")
                if len(title_v) > 110:
                    title_v = title_v[:110] + "..."
                reasoning_v = (getattr(r, "reasoning", "") or "").replace("|", "\\|").replace("\n", " ")
                if len(reasoning_v) > 200:
                    reasoning_v = reasoning_v[:200] + "..."
                lines.append(
                    f"| {pos} | {getattr(r, 'ref_id', '')} | {getattr(r, 'relevance', '')} "
                    f"| {getattr(r, 'rrf_max', 0.0)} | {getattr(r, 'source_type', '')} "
                    f"| {title_v} | {reasoning_v} |"
                )
        else:
            lines.append("_(no kept results)_")
        lines.append("")
    return "\n".join(lines)


def _write_phase_flow_md(phase_dir: Path, rqrs: list[RerankerQueryResult]) -> None:
    """Write flow.md into phase_dir: expander queries → search hits → reranker decisions.

    Reads run.json (rounds + search_results_log) from phase_dir and combines it
    with the RQR objects so one file tells the full expander→search→reranker story.
    """
    run_json_path = phase_dir / "run.json"
    if not run_json_path.exists():
        return

    try:
        data = json.loads(run_json_path.read_text(encoding="utf-8"))
    except Exception:
        return

    agent = data.get("agent", "?")
    status = data.get("status", "?")
    duration = data.get("duration_seconds", 0)
    rounds_data = data.get("rounds", [])
    search_log = data.get("search_results_log", [])
    per_agent = data.get("cost", {}).get("per_agent", [])

    # Build search lookup: (round, query) → result_count
    search_by_round: dict[int, list[dict]] = {}
    for entry in search_log:
        r = entry.get("round", 1)
        search_by_round.setdefault(r, []).append(entry)

    # Build per-agent token lookup: (agent_name, round, query_index) → tokens
    reranker_tokens: dict[tuple, int] = {}
    expander_tokens: dict[int, int] = {}
    for pa in per_agent:
        a = pa.get("agent", "")
        if a == "expander":
            expander_tokens[pa.get("round", 1)] = pa.get("total_tokens", 0)
        elif a == "reranker":
            key = (pa.get("round", 1), pa.get("reranker_round", 1), pa.get("query_index", 0))
            reranker_tokens[key] = reranker_tokens.get(key, 0) + pa.get("total_tokens", 0)

    lines: list[str] = [
        f"# {agent} — Expander → Search → Reranker Flow",
        "",
        f"- **status**: {status}",
        f"- **duration**: {duration:.1f}s",
        f"- **outer rounds**: {data.get('result', {}).get('rounds', '?')}",
        "",
    ]

    # ---- Per outer round ----
    if rounds_data:
        for rd in rounds_data:
            rn = rd.get("round", "?")
            exp_queries = rd.get("expander_queries", [])
            search_total = rd.get("search_total", 0)
            kept = rd.get("reranker_kept", 0)
            sufficient = rd.get("reranker_sufficient", False)
            weak_axes = rd.get("weak_axes_count", 0)
            exp_tok = expander_tokens.get(rn, 0)

            lines.append(f"## Round {rn}")
            lines.append("")
            lines.append(f"### Expander → {len(exp_queries)} queries  (tokens: {exp_tok:,})")
            lines.append("")
            for i, q in enumerate(exp_queries, 1):
                lines.append(f"{i}. {q}")
            lines.append("")

            # Search breakdown for this round
            round_searches = search_by_round.get(rn, [])
            lines.append(f"### Search → {search_total} unique services ({len(round_searches)} queries run)")
            lines.append("")
            if round_searches:
                lines.append("| q# | count | query |")
                lines.append("|----|-------|-------|")
                for qi, se in enumerate(round_searches, 1):
                    q_text = (se.get("query") or "").replace("|", "\\|")
                    if len(q_text) > 100:
                        q_text = q_text[:100] + "…"
                    lines.append(f"| {qi} | {se.get('result_count', 0)} | {q_text} |")
            else:
                lines.append("_(no search log entries for this round)_")
            lines.append("")

            # Reranker summary for this round
            lines.append(f"### Reranker → kept={kept}, sufficient={sufficient}, weak_axes={weak_axes}")
            lines.append("")
    else:
        # Fallback when rounds list is empty (reg_search uses round_trace instead)
        all_queries = data.get("result", {}).get("queries", [])
        lines.append("## Expander queries (all rounds)")
        lines.append("")
        for i, q in enumerate(all_queries, 1):
            lines.append(f"{i}. {q}")
        lines.append("")
        if search_log:
            lines.append(f"## Search ({len(search_log)} queries run)")
            lines.append("")
            lines.append("| round | q# | count | query |")
            lines.append("|-------|----|-------|-------|")
            for qi, se in enumerate(search_log, 1):
                q_text = (se.get("query") or "").replace("|", "\\|")
                if len(q_text) > 100:
                    q_text = q_text[:100] + "…"
                lines.append(f"| {se.get('round', 1)} | {qi} | {se.get('result_count', 0)} | {q_text} |")
            lines.append("")

    # ---- RQR summary (post-reranker, per sub-query) ----
    if rqrs:
        lines.append(f"## Reranker Results (RQR) — {len(rqrs)} sub-queries")
        lines.append("")
        lines.append("| # | query | sufficient | kept | dropped | note |")
        lines.append("|---|-------|------------|------|---------|------|")
        for i, rqr in enumerate(rqrs, 1):
            q = (rqr.query or "").replace("|", "\\|")
            if len(q) > 80:
                q = q[:80] + "…"
            note = (rqr.summary_note or "").replace("|", "\\|")
            if len(note) > 80:
                note = note[:80] + "…"
            lines.append(
                f"| {i} | {q} | {rqr.sufficient} | {len(rqr.results or [])} "
                f"| {rqr.dropped_count} | {note} |"
            )
        lines.append("")

    # ---- Cost summary ----
    total_in = data.get("cost", {}).get("total_input_tokens", 0)
    total_out = data.get("cost", {}).get("total_output_tokens", 0)
    lines.append("## Token Cost")
    lines.append("")
    lines.append(f"| input | output | total |")
    lines.append(f"|-------|--------|-------|")
    lines.append(f"| {total_in:,} | {total_out:,} | {total_in + total_out:,} |")
    lines.append("")

    try:
        (phase_dir / "flow.md").write_text("\n".join(lines), encoding="utf-8")
    except Exception:
        pass


def _mirror_phase(
    label: str,
    src_path_str: str | None,
    dst: Path,
    rqrs: list[RerankerQueryResult],
) -> str:
    """Copy a per-phase log dir into the monitor session and write a summary.

    Returns a short status line for README.
    """
    src = Path(src_path_str) if src_path_str else None
    ok, err = _copytree_safe(src, dst)

    # Always write rqr_table.md and flow.md so the monitor session is readable
    # even when the source mirror failed.
    try:
        dst.mkdir(parents=True, exist_ok=True)
        (dst / "rqr_table.md").write_text(
            _rqr_table_block(f"{label} -- Reranker Runs (RQR)", rqrs),
            encoding="utf-8",
        )
        _write_phase_flow_md(dst, rqrs)
    except Exception:  # noqa: BLE001
        pass

    if ok:
        return f"OK -- mirrored from `{src}`"
    return f"FAILED -- {err} (rqr_table.md still written)"


def _write_planner_dump(out_dir: Path, deps: FullLoopDeps) -> bool:
    """Dump planner output + derived caps into ``05_planner/``.

    Returns True when written, False when planner didn't run (no ``deps._plan``).
    """
    plan = deps._plan
    if plan is None:
        return False

    planner_dir = out_dir / "05_planner"
    planner_dir.mkdir(parents=True, exist_ok=True)

    # Locate planner_start / planner_done / planner_error / plan_ready events
    # captured by the orchestrator (see run_full_loop). Falls through cleanly
    # when events list is missing.
    events = deps._events or []
    planner_events = [
        e for e in events
        if isinstance(e, dict)
        and str(e.get("event", "")).startswith("planner")
        or (isinstance(e, dict) and e.get("event") == "plan_ready")
    ]
    duration_s = None
    model_used = None
    fallback = False
    for e in planner_events:
        et = e.get("event")
        if et == "planner_done":
            duration_s = e.get("duration_s")
            model_used = e.get("model")
        elif et == "planner_error":
            duration_s = e.get("duration_s")
            model_used = e.get("model")
            fallback = True

    # plan.json — raw model dump.
    try:
        (planner_dir / "plan.json").write_text(
            json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        pass

    # plan.md — human-readable.
    focus_lines = [f"  - **{k}**: {plan.focus[k]}" for k in sorted(plan.focus)]
    plan_md_lines = [
        "# 05 -- Planner",
        "",
        f"- **model**: {model_used or deps.planner_model or '(default)'}",
        f"- **duration_s**: {duration_s if duration_s is not None else '-'}",
        f"- **fallback**: {fallback}",
        "",
        "## Invoke",
        "",
        f"`{sorted(plan.invoke)}`",
        "",
        "## Focus",
        "",
        *focus_lines,
        "",
        "## Sectors",
        "",
        f"`{plan.sectors}`",
        "",
        "## Rationale",
        "",
        plan.rationale,
        "",
    ]
    (planner_dir / "plan.md").write_text("\n".join(plan_md_lines), encoding="utf-8")

    # derived.md — what apply_plan_to_deps + derive_aggregator_prompt_key
    # turned the plan into, read straight off deps post-apply.
    try:
        from agents.deep_search_v4.planner import derive_aggregator_prompt_key
        agg_key = derive_aggregator_prompt_key(plan)
    except Exception:  # noqa: BLE001
        agg_key = "?"

    derived_lines = [
        "# 05/derived -- caps + aggregator prompt",
        "",
        "| executor | invoked | reranker_max_high | reranker_max_medium | expander_max_queries |",
        "|----------|---------|-------------------|---------------------|----------------------|",
        f"| reg | {deps.include_reg} | {deps.reg_max_high} | {deps.reg_max_medium} "
        f"| {(deps.expander_max_queries or {}).get('reg', '-')} |",
        f"| compliance | {deps.include_compliance} | {deps.compliance_max_high} | {deps.compliance_max_medium} "
        f"| {(deps.expander_max_queries or {}).get('compliance', '-')} |",
        f"| cases | {deps.include_cases} | {deps.case_max_high} | {deps.case_max_medium} "
        f"| {(deps.expander_max_queries or {}).get('cases', '-')} |",
        "",
        f"- **aggregator_prompt_key**: `{agg_key}`",
        f"- **sectors_override (forwarded to URA)**: `{deps.sectors_override}`",
        "",
    ]
    (planner_dir / "derived.md").write_text("\n".join(derived_lines), encoding="utf-8")
    return True


def _write_aggregator_input_md(
    out_dir: Path,
    agg_input: AggregatorInput | None,
) -> None:
    """Render the AggregatorInput exactly as it was handed to the LLM stage.

    Note: the *prompt* (system + user message) is captured separately by the
    AggregatorLogger we inject. This file records the structured input the
    pipeline assembled before prompt rendering.
    """
    target = out_dir / "input.md"
    if agg_input is None:
        target.write_text(
            "# 50/01 -- AggregatorInput\n\n_(aggregator stage did not run)_\n",
            encoding="utf-8",
        )
        return

    lines: list[str] = [
        "# 50/01 -- AggregatorInput (handed to handle_aggregator_turn)",
        "",
        f"- **original_query**: {agg_input.original_query}",
        f"- **domain**: {agg_input.domain}",
        f"- **prompt_key**: {agg_input.prompt_key}",
        f"- **enable_dcr**: {agg_input.enable_dcr}",
        f"- **detail_level**: {agg_input.detail_level}",
        f"- **session_id**: {agg_input.session_id}",
        f"- **query_id**: {agg_input.query_id}",
        f"- **log_id**: {agg_input.log_id}",
        f"- **ura attached**: {agg_input.ura is not None}",
        "",
        f"## Sub-queries fed to aggregator ({len(agg_input.sub_queries or [])})",
        "",
    ]
    if agg_input.sub_queries:
        lines.append("| idx | domain | sufficient | dropped | kept | query | summary_note |")
        lines.append("|-----|--------|------------|---------|------|-------|--------------|")
        for i, sq in enumerate(agg_input.sub_queries, start=1):
            q = (getattr(sq, "query", "") or "").replace("|", "\\|").replace("\n", " ")
            if len(q) > 110:
                q = q[:110] + "..."
            note = (getattr(sq, "summary_note", "") or "").replace("|", "\\|").replace("\n", " ")
            if len(note) > 110:
                note = note[:110] + "..."
            lines.append(
                f"| {i} | {getattr(sq, 'domain', '?')} | {getattr(sq, 'sufficient', '?')} "
                f"| {getattr(sq, 'dropped_count', 0)} | {len(getattr(sq, 'results', []) or [])} "
                f"| {q} | {note} |"
            )
    else:
        lines.append("_(empty)_")
    lines.append("")

    target.write_text("\n".join(lines), encoding="utf-8")


def _write_events(out_dir: Path, deps: FullLoopDeps) -> None:
    events = deps._events or []
    tail = events[-200:]

    def _event_type(e: Any) -> str:
        if not isinstance(e, dict):
            return "?"
        return str(e.get("type") or e.get("event") or "?")

    counts = Counter(_event_type(e) for e in events)

    lines = [
        "# 60 -- SSE Events",
        "",
        f"Total events captured: {len(events)}",
        "",
        "## By type",
        "",
    ]
    for t, c in sorted(counts.items(), key=lambda kv: str(kv[0])):
        lines.append(f"- {t}: {c}")
    lines.append("")
    lines.append("## Tail (last 200)")
    lines.append("")
    lines.append("```")
    for e in tail:
        if isinstance(e, dict):
            t = _event_type(e)
            msg = e.get("message") or e.get("query") or e.get("note") or e
            if isinstance(msg, dict):
                msg = json.dumps(msg, ensure_ascii=False)
            lines.append(f"[{t}] {_truncate_for_event(msg)}")
        else:
            lines.append(f"[?] {_truncate_for_event(e)}")
    lines.append("```")
    (out_dir / "events.md").write_text("\n".join(lines), encoding="utf-8")

    # Full machine-readable dump too -- useful for grep / jq.
    try:
        with (out_dir / "events.jsonl").open("w", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e, ensure_ascii=False, default=str) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _write_per_executor_stats(out_dir: Path, deps: FullLoopDeps) -> None:
    lines = [
        "# 60 -- Per-Executor Stats",
        "",
        "Mirrors `deps._per_executor_stats` (populated by orchestrator phase wrappers).",
        "",
        "| executor | duration_ms | total_tokens_in | total_tokens_out |",
        "|----------|-------------|-----------------|------------------|",
    ]
    stats = deps._per_executor_stats or {}
    for name in ("reg_search", "compliance_search", "case_search"):
        s = stats.get(name) or {}
        lines.append(
            f"| {name} | {s.get('duration_ms', '-')} | "
            f"{s.get('total_tokens_in', '-')} | {s.get('total_tokens_out', '-')} |"
        )
    lines.append("")
    (out_dir / "per_executor_stats.md").write_text("\n".join(lines), encoding="utf-8")


def _write_summary(
    out_dir: Path,
    query: dict,
    deps: FullLoopDeps,
    agg_output: AggregatorOutput | None,
    duration_s: float,
    error: str | None,
    mirror_status: dict[str, str],
) -> None:
    ura = deps._ura
    stats = deps._per_executor_stats or {}
    total_in = sum(int((s or {}).get("total_tokens_in", 0) or 0) for s in stats.values())
    total_out = sum(int((s or {}).get("total_tokens_out", 0) or 0) for s in stats.values())

    lines = [
        f"# Summary -- query_id={query.get('id')}",
        "",
        f"- **status**: {'CRASHED' if error else 'OK'}",
        f"- **wall_time_s**: {duration_s:.2f}",
        f"- **category**: {query.get('category')}",
        "",
        "## Counts",
        "",
        f"- reg sub-queries (RQRs): {len(deps._reg_rqrs or [])}",
        f"- compliance sub-queries (RQRs): {len(deps._comp_rqrs or [])}",
        f"- case sub-queries (RQRs): {len(deps._case_rqrs or [])}",
        f"- URA high results: {len(ura.high_results) if ura else 0}",
        f"- URA medium results: {len(ura.medium_results) if ura else 0}",
        f"- URA dropped: {len(ura.dropped) if ura else 0}",
        f"- aggregator references: {len(agg_output.references) if agg_output else 0}",
        f"- aggregator confidence: {agg_output.confidence if agg_output else '-'}",
        f"- aggregator model_used: {agg_output.model_used if agg_output else '-'}",
        f"- aggregator prompt_key: {agg_output.prompt_key if agg_output else '-'}",
        "",
        "## Tokens (sum across phases)",
        "",
        f"- total_tokens_in: {total_in}",
        f"- total_tokens_out: {total_out}",
        "",
        "## Per-phase log mirror status",
        "",
    ]
    for k, v in mirror_status.items():
        lines.append(f"- **{k}**: {v}")
    lines.append("")

    if agg_output and agg_output.validation:
        v = agg_output.validation
        lines.append("## Validation")
        lines.append("")
        lines.append(f"- passed: {v.passed}")
        lines.append(f"- dangling_citations: {v.dangling_citations}")
        lines.append(f"- unused_references: {v.unused_references}")
        lines.append(f"- ungrounded_snippets: {getattr(v, 'ungrounded_snippets', '-')}")
        lines.append(f"- sub_query_coverage: {v.sub_query_coverage:.0%}")
        if v.notes:
            lines.append("- notes:")
            for n in v.notes:
                lines.append(f"  - {n}")
        lines.append("")

    if error:
        lines.append("## Error")
        lines.append("")
        lines.append("```")
        lines.append(error)
        lines.append("```")
    (out_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def _write_readme(
    out_dir: Path,
    query: dict,
    duration_s: float,
    error: str | None,
    agg_output: AggregatorOutput | None,
    mirror_status: dict[str, str],
) -> None:
    """Top-level index. First file a human opens after a run."""
    lines = [
        f"# Monitor session -- query_id={query.get('id')}",
        "",
        f"- **status**: {'CRASHED' if error else 'OK'}",
        f"- **wall_time_s**: {duration_s:.2f}",
        f"- **category**: {query.get('category')}",
        f"- **confidence**: {agg_output.confidence if agg_output else '-'}",
        f"- **references**: {len(agg_output.references) if agg_output else 0}",
        "",
        "## Pipeline flow",
        "",
        "```",
        "  reg_search ─┐",
        "             │",
        "  compliance ┼─── parallel ───→  URA merge  ───→  Aggregator  ───→  Output",
        "             │     (gather)        (40_ura)       (50_*)            (summary)",
        "  case_search┘",
        "```",
        "",
        "## Files (in pipeline order)",
        "",
        "| Stage | File | What's in it |",
        "|-------|------|--------------|",
        "| 00 | [00_query.md](00_query.md) | Query text + executor flags + per-phase log_ids |",
        "| 05 | [05_planner/](05_planner/) | Planner plan + derived caps + aggregator prompt key (only when --enable-planner) |",
        f"| 10 | [10_reg_search/](10_reg_search/) | Reg phase mirror -- expander + search + reranker. *{mirror_status.get('reg', '-')}* |",
        f"| 20 | [20_compliance_search/](20_compliance_search/) | Compliance phase mirror. *{mirror_status.get('compliance', '-')}* |",
        f"| 30 | [30_case_search/](30_case_search/) | Case phase mirror. *{mirror_status.get('case', '-')}* |",
        "| 40 | [40_ura.md](40_ura.md) | Merged UnifiedRetrievalArtifact (high + medium tiers) |",
        "| 50 | [50_aggregator/](50_aggregator/) | AggregatorInput, prompt(s), raw LLM, thinking, synthesis, validation |",
        "| 60 | [60_runtime/](60_runtime/) | SSE events, per-executor stats |",
        "| -- | [summary.md](summary.md) | Final tally, tokens, validation report |",
        "",
        "## What lives in each phase mirror",
        "",
        "Every per-domain mirror is a **verbatim copy** of the report dir written",
        "by that phase, so opening it is the same as opening the source. Useful",
        "files inside each:",
        "",
        "- `run.md` -- human overview (focus, queries, timeline, file index)",
        "- `run.json` -- machine-readable everything (events, inner_usage, step_timings)",
        "- `expander*/round_N.md` -- expander LLM I/O per round, with token usage",
        "- `expander*/reasoning_round_N.md` -- expander internal rationale per sub-query",
        "- `search/round_N_qX_*.md` -- raw DB hit list per sub-query (RRF positions)",
        "- `reranker/round_N_qX_*.md` -- reranker LLM input + classification output",
        "- `reranker/summary.json` -- aggregated reranker decisions",
        "- `flow.md` -- **monitor-only**: expander queries → search hit counts → reranker kept/dropped per sub-query in one file",
        "- `rqr_table.md` -- monitor-only table of `RerankerQueryResult` objects",
        "  exactly as they entered the URA merger (post-adapter)",
        "",
        "## What lives in 50_aggregator/",
        "",
        "Captured by `AggregatorLogger` which the monitor injects into the run:",
        "",
        "- `input.md` -- structured AggregatorInput (URA + sub_queries) before prompt render",
        "- `prompt_single.md` (or `prompt_draft.md` / `prompt_critique.md` / `prompt_rewrite.md`",
        "  for DCR / `prompt_fallback_single.md` if the primary failed) -- exact system prompt",
        "  + user message sent to the LLM, byte-for-byte",
        "- `llm_raw_*.txt` -- raw model completion per stage, before parsing",
        "- `thinking.md` -- stripped `<thinking>` block",
        "- `synthesis.md` -- final synthesis + reference block",
        "- `references.json` -- structured Reference objects",
        "- `validation.json` -- post-validate report (dangling cites, unused refs, coverage)",
        "- `run.md` -- per-aggregator-run summary",
        "- `output.md` -- pretty-printed AggregatorOutput",
        "",
    ]
    if error:
        lines.append("## Crash")
        lines.append("")
        lines.append(f"See [CRASH.md](CRASH.md). Error: `{error}`")
        lines.append("")

    (out_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Per-query runner
# ---------------------------------------------------------------------------


async def _run_one(
    query: dict,
    monitor_session_dir: Path,
    http_client: httpx.AsyncClient,
    supabase: Any,
    settings: Any,
    *,
    enable_planner: bool = False,
    planner_model: str | None = None,
    model_override: str | None = None,
) -> dict:
    """Run a single query and dump every stage."""
    q_id = int(query["id"])
    ts = _utc_ts()
    out_dir = monitor_session_dir / f"query_{q_id}" / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[monitor] === query_id={q_id} -- {query.get('category')} ===", flush=True)
    print(f"[monitor] output dir: {out_dir}", flush=True)

    # Inject a logger pointing at this run's 50_aggregator/ folder so the
    # aggregator writes its prompt + raw LLM + thinking + validation straight
    # into the monitor session.
    target_agg_dir = out_dir / "50_aggregator"
    agg_logger = _DirectAggregatorLogger(target_agg_dir)

    deps = FullLoopDeps(
        supabase=supabase,
        embedding_fn=embed_regulation_query_alibaba,
        jina_api_key=settings.JINA_RERANKER_API_KEY or "",
        http_client=http_client,
        # Production defaults -- mirror agents/orchestrator.py routing layer.
        # expander_prompt_key / case_expander_prompt_key intentionally left to
        # FullLoopDeps defaults (reg=prompt_2, case=prompt_3).
        use_reranker=False,
        concurrency=10,
        unfold_mode="precise",
        include_compliance=True,
        include_cases=True,
        detail_level="medium",
        aggregator_logger=agg_logger,
        # Planner wiring (v4 cut-1.5).
        enable_planner=enable_planner,
        planner_model=planner_model,
        model_override=model_override,
    )

    # Stamp the query file before kicking off so we have something on crash.
    _write_query_md(out_dir, query, ts, deps)

    t0 = time.perf_counter()
    error_msg: str | None = None
    agg_output: AggregatorOutput | None = None
    crash_tb: str | None = None

    try:
        agg_output = await run_full_loop(
            query=query["text"],
            query_id=q_id,
            deps=deps,
        )
    except Exception as exc:  # noqa: BLE001
        error_msg = f"{type(exc).__name__}: {exc}"
        crash_tb = traceback.format_exc()
        print(f"[monitor] !! query {q_id} crashed: {error_msg}", flush=True)

    duration_s = time.perf_counter() - t0

    # Re-stamp 00 so the freshly-set log_dir paths are visible.
    try:
        _write_query_md(out_dir, query, ts, deps)
    except Exception:  # noqa: BLE001
        pass

    # Mirror per-phase log dirs into the monitor session and write rqr_tables.
    def _safe(label: str, fn):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            (out_dir / f"_ERROR_{label}.md").write_text(
                f"# Stage {label} failed\n\n```\n{traceback.format_exc()}\n```\n",
                encoding="utf-8",
            )
            print(f"[monitor]   stage {label} dump failed: {exc}", flush=True)

    mirror_status: dict[str, str] = {}

    def _do_mirror_reg() -> None:
        mirror_status["reg"] = _mirror_phase(
            "10 -- reg_search",
            deps._reg_log_dir,
            out_dir / "10_reg_search",
            deps._reg_rqrs or [],
        )

    def _do_mirror_compliance() -> None:
        mirror_status["compliance"] = _mirror_phase(
            "20 -- compliance_search",
            deps._comp_log_dir,
            out_dir / "20_compliance_search",
            deps._comp_rqrs or [],
        )

    def _do_mirror_case() -> None:
        mirror_status["case"] = _mirror_phase(
            "30 -- case_search",
            deps._case_log_dir,
            out_dir / "30_case_search",
            deps._case_rqrs or [],
        )

    _safe("05_planner", lambda: _write_planner_dump(out_dir, deps))
    _safe("10_reg", _do_mirror_reg)
    _safe("20_compliance", _do_mirror_compliance)
    _safe("30_case", _do_mirror_case)

    _safe("40_ura", lambda: (out_dir / "40_ura.md").write_text(
        render_ura_md(deps._ura), encoding="utf-8",
    ))

    runtime_dir = out_dir / "60_runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    _safe("50_input", lambda: _write_aggregator_input_md(target_agg_dir, deps._aggregator_input))
    _safe("50_output", lambda: (target_agg_dir / "output.md").write_text(
        render_aggregator_md(agg_output), encoding="utf-8",
    ))
    _safe("60_events", lambda: _write_events(runtime_dir, deps))
    _safe("60_stats", lambda: _write_per_executor_stats(runtime_dir, deps))
    _safe("summary", lambda: _write_summary(
        out_dir, query, deps, agg_output, duration_s, error_msg, mirror_status,
    ))
    _safe("readme", lambda: _write_readme(
        out_dir, query, duration_s, error_msg, agg_output, mirror_status,
    ))

    if crash_tb:
        (out_dir / "CRASH.md").write_text(
            f"# CRASH -- query_id={q_id}\n\n## Exception\n\n```\n{error_msg}\n```\n\n"
            f"## Traceback\n\n```\n{crash_tb}\n```\n",
            encoding="utf-8",
        )

    return {
        "query_id": q_id,
        "status": "CRASHED" if error_msg else "OK",
        "duration_s": duration_s,
        "references": len(agg_output.references) if agg_output else 0,
        "out_dir": str(out_dir),
        "error": error_msg,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m agents.deep_search_v4.monitor.run_monitor")
    parser.add_argument(
        "--query-id",
        type=int,
        action="append",
        required=True,
        help="ID of a query in test_queries.json (repeatable).",
    )
    parser.add_argument(
        "--enable-planner",
        action="store_true",
        help=(
            "Run the v4 planner agent before the parallel executors. When set, "
            "the planner picks invoke + focus + sectors and apply_plan_to_deps "
            "overlays the result onto FullLoopDeps."
        ),
    )
    parser.add_argument(
        "--planner-model",
        default=None,
        help=(
            "Model registry key for the planner LLM (e.g. 'qwen3.6-plus'). "
            "Falls back to the planner's default ('qwen3-flash') when omitted."
        ),
    )
    parser.add_argument(
        "--model-override",
        default=None,
        help=(
            "Model registry key applied to all executor agents (expanders, "
            "rerankers, aggregator). Independent of --planner-model."
        ),
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    supabase = get_supabase_client()

    session_ts = _utc_ts()
    monitor_session_dir = MONITOR_ROOT
    monitor_session_dir.mkdir(parents=True, exist_ok=True)

    queries: list[dict] = []
    for qid in args.query_id:
        q = _load_query(qid)
        if not q:
            print(f"[monitor] !! query_id={qid} not found in {TEST_QUERIES}", flush=True)
            continue
        queries.append(q)
    if not queries:
        print("[monitor] no queries to run.", flush=True)
        return 1

    print(
        f"[monitor] session_ts={session_ts}, queries={[q['id'] for q in queries]} "
        f"enable_planner={args.enable_planner} planner_model={args.planner_model} "
        f"model_override={args.model_override}",
        flush=True,
    )

    results: list[dict] = []
    async with httpx.AsyncClient(timeout=30.0) as http_client:
        for q in queries:
            r = await _run_one(
                q, monitor_session_dir, http_client, supabase, settings,
                enable_planner=args.enable_planner,
                planner_model=args.planner_model,
                model_override=args.model_override,
            )
            results.append(r)
            print(
                f"[monitor] done query_id={r['query_id']} -- {r['status']} -- "
                f"{r['references']} refs -- {r['duration_s']:.1f}s",
                flush=True,
            )

    print("\n[monitor] === ALL DONE ===", flush=True)
    for r in results:
        print(
            f"  query_id={r['query_id']:>3}  {r['status']:<8}  "
            f"refs={r['references']:>2}  dur={r['duration_s']:.1f}s  -> {r['out_dir']}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
