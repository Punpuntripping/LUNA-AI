"""Convo-monitor bootstrap script.

One-shot Phase-1 data preparation for the convo-monitor PLANNER agent.
Removes all in-context data handling from the agent: the agent runs
exactly ONE Logfire MCP query (the universal pull), copies the cached
result to disk, then calls this script. From that point on the agent
reads only ``_bootstrap_summary.json`` (small) and dispatches sub-agents.

What this script does, in order:

1. Validates inputs and creates the report subdirectory tree under
   ``<out>/trace_dumps`` and ``<out>/raw_data``.
2. Pulls every Supabase row for the conversation
   (conversations, messages, workspace_items, agent_runs) via
   ``shared.db.client.get_supabase_client()`` and writes them as JSON
   to ``<out>/trace_dumps/supabase_<table>.json``.
3. Shells out to ``scripts/convo_monitor/extract_raw_data.py`` with the
   agent's pre-dumped Logfire spans file, populating ``<out>/raw_data``.
4. Reads the Logfire dump + the raw_data manifest + the Supabase dumps,
   and emits ``<out>/_bootstrap_summary.json`` listing:
     - every turn (trace_id, dispatch_type, message_ids, terminal_status,
       per-turn raw_data folder list, anomaly hints, cost estimate)
     - cross-cut hints (cancel-bug count, kind mismatches, memory skips,
       orphan items, cost disagreements)

Output paths the planner reads:
    <out>/_bootstrap_summary.json    -- the planner's primary input
    <out>/trace_dumps/supabase_*.json
    <out>/trace_dumps/_logfire_spans_raw.json   -- consumed in-place
    <out>/raw_data/                  -- per-agent folder tree
    <out>/raw_data/_manifest.json

Usage:
    python scripts/convo_monitor/bootstrap.py \\
        --conv-id <uuid> \\
        --out <abs_path_to_report_dir> \\
        --logfire-spans <abs_path_to_logfire_spans_raw.json>

Exit codes:
    0  success, _bootstrap_summary.json written
    1  usage / input error
    2  Supabase pull failed (credentials missing or unreachable)
    3  Logfire spans dump unreadable
    4  extract_raw_data.py failed (stderr is propagated)
    5  summary generation failed (bug in this script)

Read-only on production data. SELECT-only against Supabase. Does not
hit Logfire (the agent's MCP query already dumped what we need).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger("convo_bootstrap")

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

EXTRACTOR_PATH = _REPO_ROOT / "scripts" / "convo_monitor" / "extract_raw_data.py"


# ---------------------------------------------------------------------------
# Logfire spans loader (re-uses the extractor's envelope handling)
# ---------------------------------------------------------------------------


def _load_logfire_spans(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "rows" in raw:
        return raw["rows"]
    if isinstance(raw, list):
        return raw
    raise ValueError(
        f"Unrecognised Logfire spans shape in {path}: expected list or "
        f"{{columns, rows}} envelope, got {type(raw).__name__}"
    )


def _coerce_attrs(attrs: Any) -> dict:
    if isinstance(attrs, dict):
        return attrs
    if isinstance(attrs, str):
        try:
            return json.loads(attrs)
        except Exception:
            return {}
    return {}


# ---------------------------------------------------------------------------
# Supabase pull
# ---------------------------------------------------------------------------


def _pull_supabase(conv_id: str) -> dict[str, Any]:
    from shared.db.client import get_supabase_client

    sb = get_supabase_client()
    out: dict[str, Any] = {}

    out["conversation"] = (
        sb.table("conversations")
        .select("conversation_id, user_id, case_id, created_at, updated_at")
        .eq("conversation_id", conv_id)
        .maybe_single()
        .execute()
        .data
    )

    out["messages"] = (
        sb.table("messages")
        .select("message_id, role, content, created_at, finish_reason, metadata")
        .eq("conversation_id", conv_id)
        .order("created_at")
        .execute()
        .data
        or []
    )

    out["workspace_items"] = (
        sb.table("workspace_items")
        .select(
            "item_id, wi_seq, kind, title, summary, describe_query, content_md, "
            "metadata, created_at, deleted_at, agent_family, message_id"
        )
        .eq("conversation_id", conv_id)
        .order("created_at")
        .execute()
        .data
        or []
    )

    out["agent_runs"] = (
        sb.table("agent_runs")
        .select(
            "run_id, agent_family, subtype, status, case_id, message_id, task_label, "
            "input_summary, output_item_id, duration_ms, tokens_in, tokens_out, "
            "tokens_reasoning, cost_usd, model_used, per_phase_stats, error, "
            "trace_id, span_id, created_at"
        )
        .eq("conversation_id", conv_id)
        .order("created_at")
        .execute()
        .data
        or []
    )
    # Derive produced_artifact since the column is not in the schema.
    for r in out["agent_runs"]:
        r["produced_artifact"] = bool(r.get("output_item_id"))

    return out


def _write_supabase_dumps(supabase: dict[str, Any], dumps_dir: Path) -> None:
    mapping = {
        "supabase_conversation.json": supabase.get("conversation"),
        "supabase_messages.json": supabase.get("messages", []),
        "supabase_workspace_items.json": supabase.get("workspace_items", []),
        "supabase_agent_runs.json": supabase.get("agent_runs", []),
    }
    for filename, payload in mapping.items():
        (dumps_dir / filename).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# Extractor invocation
# ---------------------------------------------------------------------------


def _run_extractor(conv_id: str, out_dir: Path, spans_path: Path) -> tuple[int, str, str]:
    raw_data_dir = out_dir / "raw_data"
    raw_data_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(EXTRACTOR_PATH),
        "--conv-id",
        conv_id,
        "--out",
        str(raw_data_dir),
        "--logfire-spans",
        str(spans_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return proc.returncode, proc.stdout, proc.stderr


# ---------------------------------------------------------------------------
# Plan summary builder
# ---------------------------------------------------------------------------


def _ts_to_iso(ts: Any) -> Optional[str]:
    if ts is None:
        return None
    if isinstance(ts, str):
        return ts
    return str(ts)


def _parse_iso(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    if isinstance(ts, datetime):
        return ts
    s = str(ts).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _index_spans_by_trace(spans: list[dict]) -> dict[str, list[dict]]:
    by_trace: dict[str, list[dict]] = defaultdict(list)
    for s in spans:
        tid = s.get("trace_id")
        if tid:
            by_trace[tid].append(s)
    for tid in by_trace:
        by_trace[tid].sort(key=lambda r: r.get("start_timestamp") or "")
    return by_trace


def _index_spans_by_id(spans: list[dict]) -> dict[str, dict]:
    return {s["span_id"]: s for s in spans if s.get("span_id")}


def _walk_ancestors_dicts(span: dict, by_id: dict[str, dict]) -> list[dict]:
    chain: list[dict] = []
    seen: set[str] = set()
    cur = span
    while cur and cur.get("span_id") not in seen:
        seen.add(cur["span_id"])
        chain.append(cur)
        parent_id = cur.get("parent_span_id")
        cur = by_id.get(parent_id) if parent_id else None
    return chain


def _detect_dispatch_type(trace_spans: list[dict], by_id: dict[str, dict]) -> str:
    """Look at router.classify's child agent run final_result, OR walk dispatch.specialist's children."""
    # First, try router.classify -> agent run [router_agent] -> attributes.final_result
    for s in trace_spans:
        if s.get("span_name") != "router.classify":
            continue
        # Find the agent run child
        for c in trace_spans:
            if c.get("parent_span_id") != s.get("span_id"):
                continue
            if c.get("span_name") != "agent run":
                continue
            attrs = _coerce_attrs(c.get("attributes"))
            fr = attrs.get("final_result")
            if isinstance(fr, str):
                try:
                    fr = json.loads(fr)
                except Exception:
                    pass
            if isinstance(fr, dict):
                # ChatResponse vs DispatchAgent
                if "agent_family" in fr:
                    return str(fr.get("agent_family", "unknown"))
                if "message" in fr or fr.get("__type__", "").endswith("ChatResponse"):
                    return "chat"
            # fall through
    # Fallback: look for dispatch.specialist and its agent_family attribute
    for s in trace_spans:
        if s.get("span_name") == "dispatch.specialist":
            attrs = _coerce_attrs(s.get("attributes"))
            af = attrs.get("agent_family")
            if af:
                return str(af)
    # No dispatch — likely chat-only
    for s in trace_spans:
        if s.get("span_name") == "router.classify":
            return "chat"
    return "unknown"


def _detect_terminal_status(trace_spans: list[dict]) -> str:
    for s in trace_spans:
        if s.get("span_name") == "message.stream":
            attrs = _coerce_attrs(s.get("attributes"))
            return str(attrs.get("outcome") or "unknown")
    return "no-message-stream"


def _detect_message_ids(trace_spans: list[dict]) -> tuple[Optional[str], Optional[str], Optional[str]]:
    for s in trace_spans:
        if s.get("span_name") == "message.stream":
            attrs = _coerce_attrs(s.get("attributes"))
            return (
                attrs.get("user_message_id"),
                attrs.get("assistant_message_id"),
                attrs.get("case_id"),
            )
    return (None, None, None)


def _detect_anomalies(
    trace_spans: list[dict],
    by_id: dict[str, dict],
    trace_agent_runs: list[dict],
) -> tuple[list[str], bool]:
    """Return (hints, cancel_bug_signature)."""
    hints: list[str] = []
    cancel_bug = False

    # Exceptions and warnings
    for s in trace_spans:
        et = s.get("exception_type")
        lvl = s.get("level")
        if et or (isinstance(lvl, int) and lvl >= 17):
            hints.append(
                f"span `{s.get('span_name')}` [{(s.get('span_id') or '')[:8]}] "
                f"level={lvl} exception_type={et!r}"
            )

    # Smoking-gun event
    for s in trace_spans:
        if s.get("span_name") == "message.stream.pipeline_cancelled":
            attrs = _coerce_attrs(s.get("attributes"))
            hints.append(
                f"smoking-gun event message.stream.pipeline_cancelled fired at "
                f"{s.get('start_timestamp')} (outcome={attrs.get('outcome')!r}, "
                f"disconnect_detected={attrs.get('disconnect_detected')!r})"
            )

    # Cancel-bug pattern: span shows CancelledError but agent_runs row says status='ok' AND cost_usd is null
    has_cancel_err = any(
        (s.get("exception_type") or "").endswith("CancelledError")
        for s in trace_spans
    )
    if has_cancel_err:
        for r in trace_agent_runs:
            if r.get("status") == "ok" and r.get("cost_usd") in (None, 0, 0.0):
                cancel_bug = True
                hints.append(
                    f"cancel-bug pattern: agent_runs run_id={r.get('run_id')!r} "
                    f"has status='ok' cost_usd=NULL despite CancelledError in trace"
                )
                break

    # OTEL dropout: child has parent_span_id not in dump
    span_ids = {s.get("span_id") for s in trace_spans}
    dangling_parents: set[str] = set()
    for s in trace_spans:
        pid = s.get("parent_span_id")
        if pid and pid not in span_ids:
            dangling_parents.add(pid)
    # Only flag if more than 1 (the trace's HTTP root naturally has parent outside)
    if len(dangling_parents) > 1:
        hints.append(
            f"possible OTEL dropout: {len(dangling_parents)} parent span_ids referenced "
            f"by children but absent from the dump"
        )

    return hints, cancel_bug


def _per_turn_raw_data_folders(manifest: dict, trace_id: str, raw_data_root: Path) -> list[str]:
    leaves = manifest.get("leaves") or manifest.get("entries") or []
    folders: list[str] = []
    for leaf in leaves:
        if leaf.get("trace_id") != trace_id:
            continue
        # Folder is either explicit "folder" or "path"
        f = leaf.get("folder") or leaf.get("path") or leaf.get("relative_path")
        if not f:
            continue
        folders.append(str(f).replace("\\", "/"))
    return sorted(set(folders))


def _per_turn_cost(agent_runs: list[dict], trace_id: str) -> float:
    """Per-turn cost from Supabase agent_runs ledger (more reliable than manifest)."""
    total = 0.0
    for r in agent_runs:
        if r.get("trace_id") != trace_id:
            continue
        c = r.get("cost_usd")
        if isinstance(c, (int, float)):
            total += float(c)
    return round(total, 6)


def _per_turn_workspace_items(
    workspace_items: list[dict], assistant_message_id: Optional[str]
) -> list[dict]:
    if not assistant_message_id:
        return []
    out = []
    for w in workspace_items:
        if w.get("message_id") == assistant_message_id:
            out.append(
                {
                    "item_id": w.get("item_id"),
                    "kind": w.get("kind"),
                    "title_first_60": (w.get("title") or "")[:60],
                }
            )
    return out


def _cross_cut_hints(
    spans: list[dict],
    supabase: dict[str, Any],
    manifest: dict,
    turns: list[dict],
) -> dict[str, Any]:
    hints: dict[str, Any] = {}

    # 1. cancel-bug signature count
    hints["cancel_bug_signature_count"] = sum(1 for t in turns if t.get("cancel_bug_signature"))

    # 2. total cost from Supabase ledger + cancel-path NULL-cost detection
    sb_total = 0.0
    null_cost_ok_status: list[str] = []
    for r in supabase.get("agent_runs", []):
        c = r.get("cost_usd")
        if isinstance(c, (int, float)):
            sb_total += float(c)
        elif r.get("status") == "ok":
            # Suspicious: status='ok' with NULL cost is the cancel-bug signature
            null_cost_ok_status.append(r.get("run_id"))
    hints["total_cost_usd_supabase"] = round(sb_total, 6)
    hints["agent_runs_status_ok_with_null_cost"] = null_cost_ok_status

    # 2b. leaf vs run reconciliation (no cost compare — leaves don't carry cost_usd in manifest)
    leaves = manifest.get("leaves") or manifest.get("entries") or []
    leaf_run_ids = {leaf.get("agent_runs_row_run_id") for leaf in leaves if leaf.get("agent_runs_row_run_id")}
    run_ids = {r.get("run_id") for r in supabase.get("agent_runs", [])}
    hints["agent_runs_without_raw_data_leaf"] = sorted(run_ids - leaf_run_ids)
    hints["raw_data_leaves_without_agent_runs_row"] = sum(
        1 for leaf in leaves if not leaf.get("agent_runs_row_run_id")
    )
    hints["logfire_vs_supabase_cost_disagreement_count"] = (
        "see cost-rollup-analyzer (requires reading each leaf data.json for the Logfire-side cost)"
    )

    # 3. orphan items pre-trace-window
    earliest_span_ts = min(
        (s.get("start_timestamp") for s in spans if s.get("start_timestamp")),
        default=None,
    )
    orphans: list[str] = []
    if earliest_span_ts:
        earliest_dt = _parse_iso(earliest_span_ts)
        for w in supabase.get("workspace_items", []):
            wdt = _parse_iso(w.get("created_at"))
            if earliest_dt and wdt and wdt < earliest_dt:
                orphans.append(w.get("item_id"))
    hints["orphan_items_pre_trace_window"] = orphans

    # 4. produced_artifact kind mismatches
    items_by_id = {w.get("item_id"): w for w in supabase.get("workspace_items", [])}
    family_to_expected_kinds = {
        "deep_search": {"agent_search"},
        "writer": {"writing"},
        "item_analyzer": {"convo_context"},
    }
    mismatches: list[str] = []
    for r in supabase.get("agent_runs", []):
        if not r.get("produced_artifact"):
            continue
        oi = r.get("output_item_id")
        if not oi:
            continue
        item = items_by_id.get(oi)
        if not item:
            continue
        expected = family_to_expected_kinds.get(r.get("agent_family"))
        if expected and item.get("kind") not in expected:
            mismatches.append(r.get("run_id"))
    hints["produced_artifact_kind_mismatches"] = mismatches

    # 5. memory skipped turns: turns whose dispatch was deep_search/writer and produced an item,
    #    but where no item_analyzer span fired afterwards (best-effort).
    skipped: list[int] = []
    item_analyzer_traces = {
        s.get("trace_id")
        for s in spans
        if s.get("span_name") in ("item_analyzer.analyze", "item_analyzer.refs", "item_analyzer.meta")
    }
    for t in turns:
        if t.get("dispatch_type") not in ("deep_search", "writer"):
            continue
        if not t.get("produced_items"):
            continue
        if t.get("trace_id") in item_analyzer_traces:
            continue
        skipped.append(t.get("turn_index"))
    hints["memory_stage_skipped_turns"] = skipped

    return hints


def _build_summary(
    conv_id: str,
    out_dir: Path,
    spans: list[dict],
    supabase: dict[str, Any],
    manifest: dict,
) -> dict[str, Any]:
    by_trace = _index_spans_by_trace(spans)
    by_id = _index_spans_by_id(spans)
    runs_by_trace: dict[str, list[dict]] = defaultdict(list)
    for r in supabase.get("agent_runs", []):
        tid = r.get("trace_id")
        if tid:
            runs_by_trace[tid].append(r)

    # Turn order: by earliest start_timestamp per trace
    trace_starts = sorted(
        ((tid, ts[0].get("start_timestamp")) for tid, ts in by_trace.items()),
        key=lambda x: x[1] or "",
    )

    turns: list[dict[str, Any]] = []
    for idx, (tid, _) in enumerate(trace_starts, start=1):
        trace_spans = by_trace[tid]
        start_ts = trace_spans[0].get("start_timestamp")
        end_ts = max(
            (s.get("end_timestamp") for s in trace_spans if s.get("end_timestamp")),
            default=None,
        )
        # Duration in seconds
        sdt, edt = _parse_iso(start_ts), _parse_iso(end_ts)
        duration_s = round((edt - sdt).total_seconds(), 3) if sdt and edt else None

        dispatch_type = _detect_dispatch_type(trace_spans, by_id)
        terminal_status = _detect_terminal_status(trace_spans)
        umid, amid, case_id = _detect_message_ids(trace_spans)
        # Fallback case_id from supabase conversations row
        if case_id is None and supabase.get("conversation"):
            case_id = supabase["conversation"].get("case_id")

        # Router decision summary: best-effort from router.classify's agent run final_result
        router_summary: Optional[str] = None
        for s in trace_spans:
            if s.get("span_name") != "router.classify":
                continue
            for c in trace_spans:
                if c.get("parent_span_id") != s.get("span_id") or c.get("span_name") != "agent run":
                    continue
                attrs = _coerce_attrs(c.get("attributes"))
                fr = attrs.get("final_result")
                if isinstance(fr, str):
                    try:
                        fr = json.loads(fr)
                    except Exception:
                        pass
                if isinstance(fr, dict):
                    tl = fr.get("task_label")
                    if tl:
                        router_summary = f"task_label={tl!r}"
                    elif fr.get("message"):
                        router_summary = f"chat message_head={str(fr['message'])[:80]!r}"
                    break
            if router_summary:
                break

        produced = _per_turn_workspace_items(supabase.get("workspace_items", []), amid)
        hints, cancel_bug = _detect_anomalies(trace_spans, by_id, runs_by_trace.get(tid, []))

        turns.append(
            {
                "turn_index": idx,
                "trace_id": tid,
                "trace_id_short": tid[:12],
                "start_timestamp": start_ts,
                "end_timestamp": end_ts,
                "duration_s": duration_s,
                "dispatch_type": dispatch_type,
                "router_decision_summary": router_summary,
                "user_message_id": umid,
                "assistant_message_id": amid,
                "case_id": case_id,
                "terminal_status": terminal_status,
                "raw_data_folders": _per_turn_raw_data_folders(
                    manifest, tid, out_dir / "raw_data"
                ),
                "anomaly_hints": hints,
                "cancel_bug_signature": cancel_bug,
                "cost_estimate_usd": _per_turn_cost(supabase.get("agent_runs", []), tid),
                "produced_items": produced,
            }
        )

    summary = {
        "conversation_id": conv_id,
        "slug": conv_id[:8],
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "turn_count": len(turns),
        "total_cost_estimate_usd": round(sum(t.get("cost_estimate_usd") or 0 for t in turns), 6),
        "turns": turns,
        "cross_cut_hints": _cross_cut_hints(spans, supabase, manifest, turns),
        "_paths": {
            "report_dir": str(out_dir),
            "raw_data_root": str(out_dir / "raw_data"),
            "raw_data_manifest": str(out_dir / "raw_data" / "_manifest.json"),
            "logfire_spans_dump": str(out_dir / "trace_dumps" / "_logfire_spans_raw.json"),
            "supabase_dumps": [
                str(out_dir / "trace_dumps" / f)
                for f in (
                    "supabase_conversation.json",
                    "supabase_messages.json",
                    "supabase_workspace_items.json",
                    "supabase_agent_runs.json",
                )
            ],
        },
    }
    return summary


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(description="Convo-monitor Phase-1 bootstrap.")
    parser.add_argument("--conv-id", required=True, help="conversation_id (UUID).")
    parser.add_argument(
        "--out",
        required=True,
        help="Absolute path to the report dir (agents_reports/convo_<slug>/).",
    )
    parser.add_argument(
        "--logfire-spans",
        required=True,
        help="Absolute path to the agent's pre-dumped Logfire spans JSON "
        "(typically copied from the MCP tool-result cache).",
    )
    args = parser.parse_args(argv)

    conv_id = args.conv_id.strip()
    if not UUID_RE.match(conv_id):
        print(f"ERROR: --conv-id is not a UUID: {conv_id!r}", file=sys.stderr)
        return 1

    out_dir = Path(args.out).resolve()
    spans_path = Path(args.logfire_spans).resolve()

    if not spans_path.exists():
        print(f"ERROR: --logfire-spans path not found: {spans_path}", file=sys.stderr)
        return 3

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "trace_dumps").mkdir(parents=True, exist_ok=True)
    (out_dir / "raw_data").mkdir(parents=True, exist_ok=True)
    (out_dir / "per_turn").mkdir(parents=True, exist_ok=True)

    # Phase 1a: Load Logfire dump (from agent's MCP cache copy)
    logger.info("Loading Logfire spans from %s", spans_path)
    try:
        spans = _load_logfire_spans(spans_path)
    except Exception as e:
        print(f"ERROR: failed to read Logfire spans dump: {e}", file=sys.stderr)
        return 3
    logger.info("Loaded %d Logfire spans", len(spans))

    # Normalise: copy to the canonical dump path inside trace_dumps (idempotent)
    canonical_spans = out_dir / "trace_dumps" / "_logfire_spans_raw.json"
    if spans_path != canonical_spans:
        canonical_spans.write_text(
            json.dumps(
                {"rows": spans}
                if not (isinstance(spans, list) and spans and isinstance(spans[0], dict))
                else spans,
                indent=2,
                ensure_ascii=False,
                default=str,
            ),
            encoding="utf-8",
        )

    # Phase 1b: Supabase pull
    logger.info("Pulling Supabase ground truth for %s", conv_id)
    try:
        supabase = _pull_supabase(conv_id)
    except Exception as e:
        print(f"ERROR: Supabase pull failed: {e}", file=sys.stderr)
        return 2
    if supabase.get("conversation") is None:
        print(
            f"WARNING: no conversations row for {conv_id} — the conv_id may be invalid",
            file=sys.stderr,
        )
    _write_supabase_dumps(supabase, out_dir / "trace_dumps")
    logger.info(
        "Supabase: %d messages, %d workspace_items, %d agent_runs",
        len(supabase.get("messages", [])),
        len(supabase.get("workspace_items", [])),
        len(supabase.get("agent_runs", [])),
    )

    # Phase 1c: Run the raw_data extractor
    logger.info("Running raw_data extractor")
    rc, stdout, stderr = _run_extractor(conv_id, out_dir, spans_path)
    if rc != 0:
        print(f"ERROR: extract_raw_data.py exited {rc}", file=sys.stderr)
        if stdout:
            print("--- extractor stdout ---", file=sys.stderr)
            print(stdout, file=sys.stderr)
        if stderr:
            print("--- extractor stderr ---", file=sys.stderr)
            print(stderr, file=sys.stderr)
        return 4

    manifest_path = out_dir / "raw_data" / "_manifest.json"
    if not manifest_path.exists():
        print(
            f"ERROR: extractor finished rc=0 but {manifest_path} is missing",
            file=sys.stderr,
        )
        return 4
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"ERROR: cannot read manifest {manifest_path}: {e}", file=sys.stderr)
        return 4

    # Phase 1d: Build the summary
    logger.info("Building _bootstrap_summary.json")
    try:
        summary = _build_summary(conv_id, out_dir, spans, supabase, manifest)
    except Exception as e:
        print(f"ERROR: summary builder failed: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return 5

    summary_path = out_dir / "_bootstrap_summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    # Human summary
    print()
    print("=" * 70)
    print(f"convo-monitor bootstrap complete: {conv_id}")
    print("=" * 70)
    print(f"  report_dir              : {out_dir}")
    print(f"  turns                   : {summary['turn_count']}")
    print(f"  total cost (Supabase)   : ${summary['cross_cut_hints']['total_cost_usd_supabase']}")
    print(f"  cancel-bug turns        : {summary['cross_cut_hints']['cancel_bug_signature_count']}")
    print(f"  null-cost ok-status runs: {len(summary['cross_cut_hints']['agent_runs_status_ok_with_null_cost'])}")
    print(f"  kind mismatches         : {len(summary['cross_cut_hints']['produced_artifact_kind_mismatches'])}")
    print(f"  memory-skipped turns    : {summary['cross_cut_hints']['memory_stage_skipped_turns']}")
    print(f"  orphan items (pre-trace): {len(summary['cross_cut_hints']['orphan_items_pre_trace_window'])}")
    print(f"  runs without raw_data   : {len(summary['cross_cut_hints']['agent_runs_without_raw_data_leaf'])}")
    print(f"  summary file            : {summary_path}")
    print(f"  raw_data leaves         : {len(manifest.get('leaves') or manifest.get('entries') or [])}")
    print("=" * 70)
    print()
    print("Next: planner reads _bootstrap_summary.json and dispatches sub-agents.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
