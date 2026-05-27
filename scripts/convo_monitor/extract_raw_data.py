"""Convo-monitor raw_data extractor.

Materialises every agent invocation in a Luna conversation as a folder tree
under ``agents_reports/convo_<slug>/raw_data/``. The folder layout and
file contracts are defined in ``agents_reports/convo_monitor_raw_data_spec.md``.

Read-only on production. SELECT-only against Supabase and Logfire.

Usage:

    python scripts/convo_monitor/extract_raw_data.py \\
        --conv-id <uuid> \\
        [--out agents_reports/convo_<slug>/raw_data] \\
        [--logfire-spans <path-to-prefetched-json>]

The Logfire access path:
    Logfire reads in this repo go through the MCP tool
    ``mcp__logfire__query_run`` (project ``rihan``). The script does not
    bake in API credentials. There are two ways to feed it:

    (1) ``--logfire-spans <path>``: a JSON file containing the full span
        dump for the conversation, produced by an upstream MCP query.
        Format: one of
          - a JSON array of span rows (each a dict), or
          - the verbatim ``{columns:[...], rows:[...]}`` envelope from
            ``mcp__logfire__query_run`` (the script handles both).

    (2) No flag: the script writes
        ``agents_reports/convo_<slug>/raw_data/_queries.sql`` containing
        every Logfire SQL query the script would have run, then exits
        with a ``GAPS_PENDING`` status. The operator runs the queries
        via MCP, concatenates the rows into ``_logfire_spans.json``, and
        re-invokes the script with ``--logfire-spans``.

Supabase access uses ``shared.db.client.get_supabase_client()`` (service-role).

Idempotent: re-running over the same ``conv_id`` wipes the existing tree
under ``raw_data/`` (preserving ``_debug/``) and rebuilds it.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import textwrap
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

# Repository-root path bootstrap so ``shared.db`` etc. resolve when this script
# is invoked from anywhere. We rely on the layout ``<repo>/scripts/convo_monitor/extract_raw_data.py``.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


logger = logging.getLogger("extract_raw_data")


# ---------------------------------------------------------------------------
# Tier / layer registry (verified against agents/utils/agent_models.py and
# the wave_9_agent_runs.md hierarchy)
# ---------------------------------------------------------------------------

# slot name -> (tier, layer)
AGENT_SLOT_INFO: dict[str, tuple[str, str]] = {
    "router_agent":              ("tier_1", "Layer 1 Conductor"),
    "router":                    ("tier_1", "Layer 1 Conductor"),
    "planner_decider":           ("tier_1", "Layer 2 Major"),
    "planner_responder":         ("tier_1", "Layer 2 Major"),
    "sector_picker":             ("tier_2", "Layer 3 Task"),
    "reg_search_expander":       ("tier_1", "Layer 3 Task"),
    "compliance_search_expander":("tier_1", "Layer 3 Task"),
    "case_search_expander":      ("tier_1", "Layer 3 Task"),
    "reg_search_reranker":       ("tier_2", "Layer 3 Task"),
    "compliance_search_reranker":("tier_2", "Layer 3 Task"),
    "case_search_reranker":      ("tier_2", "Layer 3 Task"),
    "aggregator":                ("tier_1", "Layer 3 Task"),
    "aggregator_draft":          ("tier_1", "Layer 3 Task"),
    "aggregator_critique":       ("tier_1", "Layer 3 Task"),
    "aggregator_rewrite":        ("tier_1", "Layer 3 Task"),
    "artifact_summarizer":       ("tier_2", "Layer 4 Memory"),
    "item_analyzer":             ("tier_2", "Layer 4 Memory"),
    "writer_planner_decider":    ("tier_1", "Layer 2 Major"),
    "writer_agent":              ("tier_1", "Layer 3 Task"),
}


# ---------------------------------------------------------------------------
# SQL queries (Apache DataFusion / Logfire)
# ---------------------------------------------------------------------------

# Returned to the operator when running without --logfire-spans. The operator
# runs each via mcp__logfire__query_run(project='rihan').

def render_queries(conv_id: str) -> str:
    return textwrap.dedent(
        f"""\
        -- =========================================================
        -- Convo-monitor raw_data: Logfire queries for conv {conv_id}
        -- Run each in Logfire (project='rihan') and concatenate rows
        -- into a single JSON array -> _logfire_spans.json.
        -- =========================================================

        -- (1) Every span directly tagged with the conversation_id.
        SELECT
          trace_id, span_id, parent_span_id, span_name,
          start_timestamp, end_timestamp, duration,
          level, exception_type, exception_message,
          attributes, message
        FROM records
        WHERE attributes->>'conversation_id' = '{conv_id}'
        ORDER BY start_timestamp
        LIMIT 1000;

        -- (2) Every span in any trace that contains a conversation-tagged
        --     span (catches `agent run`, `chat <model>`, httpx GET/POST
        --     children that don't carry conversation_id themselves).
        WITH conv_traces AS (
          SELECT DISTINCT trace_id
          FROM records
          WHERE attributes->>'conversation_id' = '{conv_id}'
        )
        SELECT
          trace_id, span_id, parent_span_id, span_name,
          start_timestamp, end_timestamp, duration,
          level, exception_type, exception_message,
          attributes, message
        FROM records
        WHERE trace_id IN (SELECT trace_id FROM conv_traces)
        ORDER BY start_timestamp
        LIMIT 5000;
        """
    )


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Span:
    span_id: str
    parent_span_id: Optional[str]
    trace_id: str
    span_name: str
    start_timestamp: str
    end_timestamp: Optional[str]
    duration: Optional[float]
    level: Optional[int]
    exception_type: Optional[str]
    attributes: dict[str, Any]
    message: Optional[str] = None

    @property
    def agent_name(self) -> Optional[str]:
        return self.attributes.get("agent_name")

    @property
    def conversation_id(self) -> Optional[str]:
        return self.attributes.get("conversation_id")


@dataclass
class LeafRun:
    """One leaf agent run = one folder we will produce."""

    agent_group: str
    sub_agent: Optional[str]
    run_index: int
    worker_index: Optional[int]
    agent_name: str
    span: Span
    chat_calls: list[Span] = field(default_factory=list)
    dispatch_span: Optional[Span] = None
    router_span: Optional[Span] = None
    sources: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Span ingestion
# ---------------------------------------------------------------------------


def _load_spans(path: Path) -> list[Span]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw["rows"] if isinstance(raw, dict) and "rows" in raw else raw
    spans: dict[str, Span] = {}
    for row in rows:
        sid = row.get("span_id")
        if not sid or sid in spans:
            continue
        attrs = row.get("attributes")
        if isinstance(attrs, str):
            try:
                attrs = json.loads(attrs)
            except Exception:
                attrs = {}
        elif attrs is None:
            attrs = {}
        spans[sid] = Span(
            span_id=sid,
            parent_span_id=row.get("parent_span_id") or None,
            trace_id=row["trace_id"],
            span_name=row["span_name"],
            start_timestamp=row["start_timestamp"],
            end_timestamp=row.get("end_timestamp"),
            duration=row.get("duration"),
            level=row.get("level"),
            exception_type=row.get("exception_type"),
            attributes=attrs,
            message=row.get("message"),
        )
    return sorted(spans.values(), key=lambda s: (s.start_timestamp, s.span_id))


# ---------------------------------------------------------------------------
# Parent walks + classification
# ---------------------------------------------------------------------------


def _build_index(spans: list[Span]) -> dict[str, Span]:
    return {s.span_id: s for s in spans}


def _walk_ancestors(span: Span, index: dict[str, Span]) -> Iterable[Span]:
    cur: Optional[Span] = span
    seen: set[str] = set()
    while cur and cur.span_id not in seen:
        seen.add(cur.span_id)
        yield cur
        if not cur.parent_span_id:
            return
        cur = index.get(cur.parent_span_id)


def _classify(span: Span, index: dict[str, Span]) -> Optional[tuple[str, Optional[str]]]:
    """Top-level container classification: (agent_group, sub_agent)."""
    if span.span_name != "agent run":
        return None
    agent = span.agent_name or "agent"

    ancestors = list(_walk_ancestors(span, index))
    names = [a.span_name for a in ancestors]

    # router
    if any(n == "router.classify" for n in names):
        return ("router", None)

    # planner family (decider / responder / sector_picker)
    if agent in ("planner_decider", "planner_responder"):
        return ("search_planner", agent)
    if agent == "sector_picker":
        return ("search_planner", "sector_picker")

    # expanders
    if agent.endswith("_expander"):
        sub = agent.replace("_search_", "_")
        return ("expanders", sub)

    # rerankers
    if agent.endswith("_reranker"):
        sub = agent.replace("_search_", "_")
        return ("rerankers", sub)

    # aggregator family
    if agent.startswith("aggregator"):
        return ("aggregator", None)

    # artifact_summarizer (Layer 4 Memory)
    if agent == "artifact_summarizer":
        return ("item_analyzer", "artifact_summarizer")

    # item_analyzer (Layer 4 Memory) — distinguish refs vs meta family
    if agent == "item_analyzer":
        for anc in ancestors:
            if anc.span_name == "item_analyzer.refs":
                return ("item_analyzer", "item_analyzer_refs")
            if anc.span_name == "item_analyzer.meta":
                return ("item_analyzer", "item_analyzer_meta")
        return ("item_analyzer", "item_analyzer")

    # Unnamed `agent` — walk up to dispatch.specialist
    if agent == "agent":
        for anc in ancestors:
            if anc.span_name == "dispatch.specialist":
                family = anc.attributes.get("agent_family")
                if family == "writing":
                    parent = index.get(span.parent_span_id) if span.parent_span_id else None
                    if parent and parent.span_name == "publish.workspace_item":
                        return ("writing_executor", None)
                    fr = span.attributes.get("final_result")
                    if isinstance(fr, dict):
                        if any(k in fr for k in ("sections", "title_ar")):
                            return ("writing_executor", None)
                        if any(k in fr for k in ("intent_ar", "plan_md", "selected_wis", "role_assignments")):
                            return ("writing_planner", None)
                    return ("writing_planner", None)
                if family == "memory":
                    return ("item_analyzer", None)
        return None

    return None


def _find_dispatch_router(span: Span, index: dict[str, Span]) -> tuple[Optional[Span], Optional[Span]]:
    dispatch_span: Optional[Span] = None
    router_span: Optional[Span] = None
    for anc in _walk_ancestors(span, index):
        if anc.span_name == "dispatch.specialist" and dispatch_span is None:
            dispatch_span = anc
        if anc.span_name == "router.classify" and router_span is None:
            router_span = anc
    return dispatch_span, router_span


# ---------------------------------------------------------------------------
# Per-agent payload rendering
# ---------------------------------------------------------------------------


def _system_prompt_from_span(span: Span) -> Optional[str]:
    si = span.attributes.get("gen_ai.system_instructions")
    if not si:
        return None
    if isinstance(si, list) and si:
        first = si[0]
        if isinstance(first, dict):
            return first.get("content")
    if isinstance(si, dict):
        return si.get("content")
    if isinstance(si, str):
        return si
    return None


def _user_messages_from_span(span: Span) -> list[dict]:
    msgs = span.attributes.get("pydantic_ai.all_messages")
    if not isinstance(msgs, list):
        return []
    return msgs


def _final_result_from_span(span: Span) -> Any:
    return span.attributes.get("final_result")


def _model_used(span: Span) -> Optional[str]:
    return (
        span.attributes.get("gen_ai.request.model")
        or span.attributes.get("gen_ai.response.model")
    )


def _model_chain(span: Span) -> Optional[str]:
    return span.attributes.get("model_name")


def _tokens(span: Span) -> tuple[Optional[int], Optional[int], Optional[int]]:
    in_t = span.attributes.get("gen_ai.usage.input_tokens")
    out_t = span.attributes.get("gen_ai.usage.output_tokens")
    reasoning = span.attributes.get("gen_ai.usage.details.reasoning_tokens")
    return (
        int(in_t) if in_t is not None else None,
        int(out_t) if out_t is not None else None,
        int(reasoning) if reasoning is not None else None,
    )


def _render_prompt_md(span: Span) -> str:
    parts: list[str] = []
    sysprompt = _system_prompt_from_span(span)
    if sysprompt:
        parts.append("## SYSTEM\n")
        parts.append(sysprompt.strip())
    else:
        parts.append("## SYSTEM\n\n_MISSING: gen_ai.system_instructions not in span attributes_")
    parts.append("\n\n## CONVERSATION\n")
    msgs = _user_messages_from_span(span)
    if not msgs:
        parts.append("\n_MISSING: pydantic_ai.all_messages not in span attributes_")
        return "\n".join(parts)
    for m in msgs:
        role = m.get("role", "unknown")
        parts.append(f"\n### {role}")
        for blk in m.get("parts", []):
            btype = blk.get("type", "?")
            content = blk.get("content", "")
            if isinstance(content, (dict, list)):
                content = json.dumps(content, ensure_ascii=False, indent=2)
            parts.append(f"\n**{btype}:**\n\n{content}")
    return "\n".join(parts)


def _render_outputs_md(span: Span) -> str:
    fr = _final_result_from_span(span)
    if fr is None:
        for m in reversed(_user_messages_from_span(span)):
            if m.get("role") == "assistant":
                lines = []
                for blk in m.get("parts", []):
                    btype = blk.get("type", "?")
                    content = blk.get("content", "")
                    if isinstance(content, (dict, list)):
                        content = json.dumps(content, ensure_ascii=False, indent=2)
                    lines.append(f"### {btype}\n\n{content}")
                return "\n\n".join(lines) or "_MISSING: assistant message had no parts_"
        return "_MISSING: no final_result and no assistant message in pydantic_ai.all_messages_"
    if isinstance(fr, (dict, list)):
        return "```json\n" + json.dumps(fr, ensure_ascii=False, indent=2) + "\n```\n"
    return str(fr)


def _render_dependency_md(leaf: LeafRun, supabase_ctx: dict) -> str:
    parts: list[str] = []
    parts.append(f"# Dependency context - {leaf.agent_group}/{leaf.sub_agent or ''}\n")

    parts.append("## Routing context\n")
    if leaf.router_span:
        router_attrs = leaf.router_span.attributes
        parts.append(f"- router.classify span_id: `{leaf.router_span.span_id}`")
        parts.append(f"- decision: `{router_attrs.get('decision')}`")
        parts.append(f"- agent_family: `{router_attrs.get('agent_family')}`")
        parts.append(f"- workspace_item_count: `{router_attrs.get('workspace_item_count')}`")
        parts.append(f"- has_compaction_summary: `{router_attrs.get('has_compaction_summary')}`")
    else:
        parts.append("- _no router.classify span in this trace_")

    if leaf.dispatch_span:
        d = leaf.dispatch_span.attributes
        parts.append("\n## Dispatch context\n")
        parts.append(f"- dispatch.specialist span_id: `{leaf.dispatch_span.span_id}`")
        parts.append(f"- agent_family: `{d.get('agent_family')}`")
        parts.append(f"- subtype: `{d.get('subtype')}`")
        parts.append(f"- task_label: `{d.get('dispatch.task_label')}`")
        parts.append(f"- describe_query_chars: `{d.get('dispatch.describe_query_chars')}`")
        parts.append(f"- attached_count: `{d.get('attached_count')}`")
        parts.append(f"- target_item_id: `{d.get('target_item_id')}`")

    parts.append("\n## Supabase context (source: supabase)\n")
    parts.append(f"- conversation_id: `{supabase_ctx.get('conversation_id')}`")
    parts.append(f"- user_id: `{supabase_ctx.get('user_id')}`")
    parts.append(f"- case_id: `{supabase_ctx.get('case_id')}`")
    parts.append(f"- created_at: `{supabase_ctx.get('created_at')}`")

    recent = supabase_ctx.get("recent_messages_at_or_before_run", [])
    if recent:
        parts.append("\n### Recent messages at the moment of this run\n")
        for m in recent[-6:]:
            content = (m.get("content") or "")[:300]
            parts.append(f"- **{m.get('role')}** @ `{m.get('created_at')}`: {content}")

    prior = supabase_ctx.get("prior_workspace_items", [])
    if prior:
        parts.append("\n### Prior workspace_items at the moment of this run\n")
        for w in prior:
            parts.append(
                f"- WI-{w.get('wi_seq')} kind=`{w.get('kind')}` title=`{(w.get('title') or '')[:80]}`"
            )

    pre_trace = supabase_ctx.get("pre_trace_items", [])
    if pre_trace:
        parts.append("\n### Pre-trace items (out of Logfire window)\n")
        for w in pre_trace:
            parts.append(
                f"- WI-{w.get('wi_seq')} kind=`{w.get('kind')}` title=`{(w.get('title') or '')[:80]}` created_at=`{w.get('created_at')}`"
            )

    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# Cost derivation
# ---------------------------------------------------------------------------


def _estimate_cost(agent_name: str, tokens_in: Optional[int], tokens_out: Optional[int], reasoning: Optional[int]) -> Optional[float]:
    if tokens_in is None and tokens_out is None:
        return None
    tier, _ = AGENT_SLOT_INFO.get(agent_name, ("tier_1", "Layer 3 Task"))
    in_rate, out_rate = (
        (0.003 / 1000, 0.009 / 1000) if tier == "tier_1" else (0.00014 / 1000, 0.00028 / 1000)
    )
    total = 0.0
    total += (tokens_in or 0) * in_rate
    total += (tokens_out or 0) * out_rate
    total += (reasoning or 0) * out_rate
    return round(total, 6)


# ---------------------------------------------------------------------------
# Supabase pulls
# ---------------------------------------------------------------------------


def _load_supabase(conv_id: str) -> dict:
    try:
        # Direct import — shared.db.__init__ does not re-export this name.
        from shared.db.client import get_supabase_client  # noqa: WPS433
    except Exception as e:
        logger.warning("Supabase client unavailable (%s) - proceeding without ground-truth", e)
        return {
            "conversation": None,
            "messages": [],
            "workspace_items": [],
            "agent_runs": [],
        }

    try:
        sb = get_supabase_client()
    except Exception as e:
        logger.warning("get_supabase_client failed (%s) - proceeding without ground-truth", e)
        return {
            "conversation": None,
            "messages": [],
            "workspace_items": [],
            "agent_runs": [],
        }

    out: dict[str, Any] = {}

    try:
        out["conversation"] = (
            sb.table("conversations")
            .select("conversation_id, user_id, case_id, created_at, updated_at")
            .eq("conversation_id", conv_id)
            .maybe_single()
            .execute()
            .data
        )
    except Exception as e:
        logger.warning("conversations SELECT failed: %s", e)
        out["conversation"] = None

    try:
        out["messages"] = (
            sb.table("messages")
            .select("message_id, role, content, created_at, finish_reason, metadata")
            .eq("conversation_id", conv_id)
            .order("created_at")
            .execute()
            .data
            or []
        )
    except Exception as e:
        logger.warning("messages SELECT failed: %s", e)
        out["messages"] = []

    try:
        out["workspace_items"] = (
            sb.table("workspace_items")
            .select(
                "item_id, wi_seq, kind, title, summary, describe_query, content_md, "
                "metadata, created_at, deleted_at, agent_family"
            )
            .eq("conversation_id", conv_id)
            .order("created_at")
            .execute()
            .data
            or []
        )
    except Exception as e:
        logger.warning("workspace_items SELECT failed: %s", e)
        out["workspace_items"] = []

    try:
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
    except Exception as e:
        logger.warning("agent_runs SELECT failed: %s", e)
        out["agent_runs"] = []

    return out


def _agent_runs_by_run_id(supabase: dict) -> dict[str, dict]:
    return {r["run_id"]: r for r in supabase.get("agent_runs", [])}


def _items_by_id(supabase: dict) -> dict[str, dict]:
    return {w["item_id"]: w for w in supabase.get("workspace_items", [])}


# ---------------------------------------------------------------------------
# Per-run context assembly
# ---------------------------------------------------------------------------


def _supabase_ctx_for_leaf(leaf: LeafRun, supabase: dict, trace_start: dict[str, str]) -> dict:
    convo = supabase.get("conversation") or {}
    run_ts = leaf.span.start_timestamp
    messages = supabase.get("messages") or []
    wis = supabase.get("workspace_items") or []

    recent = [m for m in messages if (m.get("created_at") or "") <= run_ts]
    prior = [w for w in wis if (w.get("created_at") or "") <= run_ts and not w.get("deleted_at")]

    earliest_trace = trace_start.get(leaf.span.trace_id, "")
    pre_trace = [
        w for w in wis if earliest_trace and (w.get("created_at") or "") < earliest_trace
    ]

    return {
        "conversation_id": convo.get("conversation_id"),
        "user_id": convo.get("user_id"),
        "case_id": convo.get("case_id"),
        "created_at": convo.get("created_at"),
        "recent_messages_at_or_before_run": recent,
        "prior_workspace_items": prior,
        "pre_trace_items": pre_trace,
    }


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )


def _data_json(
    leaf: LeafRun,
    convo_id: str,
    supabase_ctx: dict,
    agent_runs_lookup: dict[str, dict],
    items_lookup: dict[str, dict],
) -> dict:
    in_t, out_t, reasoning_t = _tokens(leaf.span)
    model_used = _model_used(leaf.span)
    chain = _model_chain(leaf.span)
    tier, layer = AGENT_SLOT_INFO.get(leaf.agent_name, ("?", "?"))
    cost_est = _estimate_cost(leaf.agent_name, in_t, out_t, reasoning_t)

    chat_calls = [
        {
            "span_id": c.span_id,
            "request_model": c.attributes.get("gen_ai.request.model"),
            "response_model": c.attributes.get("gen_ai.response.model"),
            "system": c.attributes.get("gen_ai.system"),
            "input_tokens": c.attributes.get("gen_ai.usage.input_tokens"),
            "output_tokens": c.attributes.get("gen_ai.usage.output_tokens"),
            "duration_s": c.duration,
            "response_id": c.attributes.get("gen_ai.response.id"),
        }
        for c in leaf.chat_calls
    ]

    matching_run = None
    if leaf.dispatch_span is not None:
        df = leaf.dispatch_span.attributes.get("agent_family")
        for r in agent_runs_lookup.values():
            if (
                r.get("agent_family") == df
                and r.get("trace_id")
                and leaf.span.trace_id
                and r.get("trace_id") == leaf.span.trace_id
            ):
                matching_run = r
                break

    output_item = None
    if matching_run and matching_run.get("output_item_id"):
        oi = items_lookup.get(matching_run["output_item_id"])
        if oi:
            meta = oi.get("metadata") or {}
            output_item = {
                "item_id": oi.get("item_id"),
                "kind": oi.get("kind"),
                "title": oi.get("title"),
                "wi_seq": oi.get("wi_seq"),
                "subtype": meta.get("subtype") if isinstance(meta, dict) else None,
                "confidence": meta.get("confidence") if isinstance(meta, dict) else None,
            }

    return {
        "agent_group": leaf.agent_group,
        "sub_agent": leaf.sub_agent,
        "run_index": leaf.run_index,
        "worker_index": leaf.worker_index,
        "agent_name": leaf.agent_name,
        "layer": layer,
        "tier": tier,
        "trace_id": leaf.span.trace_id,
        "span_id": leaf.span.span_id,
        "parent_span_id": leaf.span.parent_span_id,
        "dispatch_span_id": leaf.dispatch_span.span_id if leaf.dispatch_span else None,
        "router_span_id": leaf.router_span.span_id if leaf.router_span else None,
        "conversation_id": convo_id,
        "user_id": supabase_ctx.get("user_id"),
        "case_id": supabase_ctx.get("case_id"),
        "user_message_id": None,
        "assistant_message_id": None,
        "start_timestamp": leaf.span.start_timestamp,
        "end_timestamp": leaf.span.end_timestamp,
        "duration_s": leaf.span.duration,
        "model_used": model_used,
        "model_chain": chain,
        "provider": leaf.span.attributes.get("gen_ai.system"),
        "tokens_in": in_t,
        "tokens_out": out_t,
        "tokens_reasoning": reasoning_t,
        "tokens_cache_read": leaf.span.attributes.get("gen_ai.usage.cache_read_tokens"),
        "cost_usd": cost_est,
        "provider_cost_usd": None,
        "outcome": "cancelled" if leaf.span.exception_type == "asyncio.exceptions.CancelledError" else (
            "error" if leaf.span.exception_type else "ok"
        ),
        "agent_runs_row": matching_run,
        "output_item": output_item,
        "llm_calls": chat_calls,
        "tools_called": [],
        "sources": {
            "agent_run_span_id": leaf.span.span_id,
            "agent_runs_row_run_id": matching_run.get("run_id") if matching_run else None,
        },
    }


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def _slug(conv_id: str) -> str:
    return conv_id.split("-")[0][:8]


def _identify_leaves(spans: list[Span], index: dict[str, Span]) -> list[LeafRun]:
    leaves: list[LeafRun] = []
    for s in spans:
        cls = _classify(s, index)
        if cls is None:
            continue
        group, sub = cls
        ag = s.agent_name or "agent"
        dispatch, router = _find_dispatch_router(s, index)
        leaves.append(LeafRun(
            agent_group=group,
            sub_agent=sub,
            run_index=0,
            worker_index=None,
            agent_name=ag,
            span=s,
            chat_calls=[],
            dispatch_span=dispatch,
            router_span=router,
        ))

    # Attach chat calls
    children_by_parent: dict[str, list[Span]] = defaultdict(list)
    for s in spans:
        if s.parent_span_id:
            children_by_parent[s.parent_span_id].append(s)
    for leaf in leaves:
        leaf.chat_calls = [
            c for c in children_by_parent.get(leaf.span.span_id, [])
            if c.span_name.startswith("chat ")
        ]

    return leaves


def _assign_run_worker_indices(leaves: list[LeafRun]) -> None:
    def run_key(leaf: LeafRun) -> tuple:
        return (
            leaf.agent_group,
            leaf.sub_agent or "",
            leaf.dispatch_span.span_id if leaf.dispatch_span else leaf.span.trace_id,
        )

    leaves.sort(key=lambda l: (l.span.start_timestamp, l.span.span_id))

    next_run_idx: dict[tuple[str, str], int] = {}
    seen_run_keys: dict[tuple, int] = {}
    for leaf in leaves:
        gs = (leaf.agent_group, leaf.sub_agent or "")
        rk = run_key(leaf)
        if rk not in seen_run_keys:
            next_run_idx[gs] = next_run_idx.get(gs, 0) + 1
            seen_run_keys[rk] = next_run_idx[gs]
        leaf.run_index = seen_run_keys[rk]

    buckets: dict[tuple, list[LeafRun]] = defaultdict(list)
    for leaf in leaves:
        buckets[(leaf.agent_group, leaf.sub_agent or "", leaf.run_index)].append(leaf)
    for bucket in buckets.values():
        if len(bucket) <= 1:
            continue
        bucket.sort(key=lambda l: (l.span.start_timestamp, l.span.span_id))
        for i, leaf in enumerate(bucket, start=1):
            leaf.worker_index = i


def _leaf_folder(out_root: Path, leaf: LeafRun) -> Path:
    parts = [out_root, leaf.agent_group, f"run_{leaf.run_index}"]
    if leaf.sub_agent:
        parts.append(leaf.sub_agent)
    if leaf.worker_index:
        parts.append(f"worker_{leaf.worker_index}")
    return Path(*parts)


def _process(conv_id: str, spans: list[Span], out_dir: Path) -> dict:
    if not spans:
        return {"status": "empty", "reason": "no spans loaded"}

    index = _build_index(spans)
    leaves = _identify_leaves(spans, index)
    _assign_run_worker_indices(leaves)

    trace_start: dict[str, str] = {}
    for s in spans:
        cur = trace_start.get(s.trace_id)
        if cur is None or s.start_timestamp < cur:
            trace_start[s.trace_id] = s.start_timestamp

    supabase = _load_supabase(conv_id)
    agent_runs_lookup = _agent_runs_by_run_id(supabase)
    items_lookup = _items_by_id(supabase)

    # Wipe and recreate (preserve _debug)
    if out_dir.exists():
        for entry in out_dir.iterdir():
            if entry.name in ("_debug", "_logfire_spans.json", "_queries.sql", "_README_GAPS_PENDING.md"):
                continue
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest_entries: list[dict] = []
    skipped_phases: list[dict] = []

    for s in spans:
        if s.span_name in (
            "deep_search.phase.reg.skipped",
            "deep_search.phase.compliance.skipped",
            "deep_search.phase.case.skipped",
        ):
            skipped_phases.append({
                "span_name": s.span_name,
                "span_id": s.span_id,
                "trace_id": s.trace_id,
                "start_timestamp": s.start_timestamp,
            })

    for leaf in leaves:
        folder = _leaf_folder(out_dir, leaf)
        folder.mkdir(parents=True, exist_ok=True)

        supabase_ctx = _supabase_ctx_for_leaf(leaf, supabase, trace_start)
        data = _data_json(leaf, conv_id, supabase_ctx, agent_runs_lookup, items_lookup)

        _write(folder / "prompt.md", _render_prompt_md(leaf.span))
        _write(folder / "dependency.md", _render_dependency_md(leaf, supabase_ctx))
        _write(folder / "outputs.md", _render_outputs_md(leaf.span))
        _write_json(folder / "data.json", data)

        manifest_entries.append({
            "folder": str(folder.relative_to(out_dir)).replace("\\", "/"),
            "agent_group": leaf.agent_group,
            "sub_agent": leaf.sub_agent,
            "run_index": leaf.run_index,
            "worker_index": leaf.worker_index,
            "agent_name": leaf.agent_name,
            "trace_id": leaf.span.trace_id,
            "span_id": leaf.span.span_id,
            "agent_runs_row_run_id": data.get("sources", {}).get("agent_runs_row_run_id"),
        })

    convo = supabase.get("conversation") or {}
    earliest_trace = min(trace_start.values()) if trace_start else None
    pre_trace_items = [
        {
            "item_id": w.get("item_id"),
            "wi_seq": w.get("wi_seq"),
            "kind": w.get("kind"),
            "title": w.get("title"),
            "created_at": w.get("created_at"),
        }
        for w in supabase.get("workspace_items", [])
        if earliest_trace and (w.get("created_at") or "") < earliest_trace
    ]

    manifest = {
        "conversation_id": conv_id,
        "slug": _slug(conv_id),
        "user_id": convo.get("user_id"),
        "case_id": convo.get("case_id"),
        "earliest_trace_start": earliest_trace,
        "trace_count": len(trace_start),
        "span_count": len(spans),
        "leaf_count": len(leaves),
        "skipped_phases": skipped_phases,
        "pre_trace_items": pre_trace_items,
        "agent_runs_row_count": len(supabase.get("agent_runs", [])),
        "workspace_items_count": len(supabase.get("workspace_items", [])),
        "messages_count": len(supabase.get("messages", [])),
        "leaves": sorted(
            manifest_entries,
            key=lambda e: (e["agent_group"], e["run_index"], e["worker_index"] or 0, e["folder"]),
        ),
    }
    _write_json(out_dir / "_manifest.json", manifest)

    debug = out_dir / "_debug"
    debug.mkdir(parents=True, exist_ok=True)
    _write_json(debug / "all_spans.json", [
        {
            "span_id": s.span_id,
            "parent_span_id": s.parent_span_id,
            "trace_id": s.trace_id,
            "span_name": s.span_name,
            "start_timestamp": s.start_timestamp,
            "end_timestamp": s.end_timestamp,
            "level": s.level,
            "exception_type": s.exception_type,
        }
        for s in spans
    ])
    _write_json(debug / "supabase_snapshot.json", supabase)

    return {"status": "ok", "manifest": manifest}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Convo-monitor raw_data extractor")
    parser.add_argument("--conv-id", required=True)
    parser.add_argument("--out", default=None)
    parser.add_argument("--logfire-spans", default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if not _UUID_RE.match(args.conv_id):
        sys.stderr.write(f"ERROR: --conv-id is not a UUID: {args.conv_id}\n")
        return 2

    slug = _slug(args.conv_id)
    out_dir = Path(args.out) if args.out else _REPO_ROOT / "agents_reports" / f"convo_{slug}" / "raw_data"
    out_dir = out_dir.resolve()

    if args.logfire_spans:
        path = Path(args.logfire_spans)
        if not path.is_absolute():
            # Try as cwd-relative, then as repo-relative
            cand = Path.cwd() / path
            if cand.exists():
                path = cand
            else:
                path = (_REPO_ROOT / path).resolve()
        else:
            path = path.resolve()
        if not path.exists():
            sys.stderr.write(f"ERROR: --logfire-spans path does not exist: {path}\n")
            return 2
        spans = _load_spans(path)
        if not spans:
            sys.stderr.write("ERROR: no spans loaded from --logfire-spans\n")
            return 3
        result = _process(args.conv_id, spans, out_dir)
        sys.stdout.write(f"[ok] {len(spans)} spans -> {out_dir}\n")
        sys.stdout.write(f"     leaves={result['manifest']['leaf_count']}\n")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    queries_path = out_dir / "_queries.sql"
    queries_path.write_text(render_queries(args.conv_id), encoding="utf-8")
    note_path = out_dir / "_README_GAPS_PENDING.md"
    note_path.write_text(textwrap.dedent(f"""\
        # GAPS_PENDING - Logfire spans not yet fetched

        Conversation: `{args.conv_id}`
        Slug:         `{slug}`

        This extractor needs the Logfire span dump for the conversation but no
        ``--logfire-spans`` path was supplied. Run the two SELECTs in
        ``_queries.sql`` against Logfire (project=``rihan``) via
        ``mcp__logfire__query_run``, save the combined rows as JSON to
        ``_logfire_spans.json`` (this folder), then re-invoke:

        ```
        python scripts/convo_monitor/extract_raw_data.py \\
            --conv-id {args.conv_id} \\
            --logfire-spans {out_dir / '_logfire_spans.json'}
        ```

        Accepted formats for ``_logfire_spans.json``:
          - A JSON array of rows (each row a dict from the MCP response),
          - The verbatim ``{{"columns": [...], "rows": [...]}}`` envelope.
        """), encoding="utf-8")

    sys.stdout.write(f"[gaps-pending] queries written: {queries_path}\n")
    sys.stdout.write(f"[gaps-pending] re-run with --logfire-spans <path>\n")
    return 4


if __name__ == "__main__":
    sys.exit(main())
