"""Extract a Luna agentic conversation from Logfire trace dumps into a report.

Reproduces the **convo_ffdf** report layout (model-named LLM-call files, a
``summary.md`` with a timing waterfall + TRACKING GAPS section, and a singular
``final_answer.md``). Distinct from ``scripts/agentic_monitor_extract.py`` which
emits the newer agent-named / fix-verification layout.

Driven by the ``/convo-monitor`` slash command: it dumps each Logfire trace to a
JSON file, then::

    python scripts/convo_monitor_extract.py --conv <id> --traces <f1> [<f2> ...]

Writes a self-contained report under
``agents_reports/agentic_monitor/convo_<id>/``.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── CLI ──────────────────────────────────────────────────────────────────────
_ap = argparse.ArgumentParser(description="Extract a Luna conversation from Logfire trace dumps (convo_ffdf format).")
_ap.add_argument("--conv", required=True, help="conversation_id (used for the output folder name)")
_ap.add_argument("--traces", nargs="+", required=True, help="paths to saved Logfire full-trace JSON dumps (one per trace/turn)")
_ap.add_argument("--out", default=None, help="output dir (default: agents_reports/agentic_monitor/convo_<conv>)")
_args = _ap.parse_args()

CONV = _args.conv
TRACE_FILES = _args.traces
OUT = _args.out or os.path.join("agents_reports", "agentic_monitor", f"convo_{CONV}")

MODEL_TIER = {
    "qwen3.6-plus": "tier_1", "deepseek-v4-pro": "tier_1",
    "qwen3.5-flash": "tier_2", "deepseek-v4-flash": "tier_2",
}

os.makedirs(os.path.join(OUT, "llm_calls"), exist_ok=True)
# Clear stale per-call files so the folder only reflects the current extraction.
for _f in os.listdir(os.path.join(OUT, "llm_calls")):
    if _f.endswith(".md"):
        os.remove(os.path.join(OUT, "llm_calls", _f))


def _attr(r):
    a = r.get("attributes")
    if isinstance(a, str):
        try:
            return json.loads(a)
        except Exception:
            return {}
    return a or {}


def as_list(v):
    if v is None:
        return []
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            return [{"role": "?", "content": v}]
    return v if isinstance(v, list) else [v]


def _safe(s):
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in (s or "")) or "model"


# ── load + merge ─────────────────────────────────────────────────────────────
rows = {}
for path in TRACE_FILES:
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    for r in (payload["rows"] if isinstance(payload, dict) else payload):
        rows[r["span_id"]] = r
rows = sorted(rows.values(), key=lambda r: r["start_timestamp"])
for r in rows:
    r["_a"] = _attr(r)
byid = {r["span_id"]: r for r in rows}
traces = sorted({r["trace_id"] for r in rows})


def secs(r):
    return r.get("duration") or 0.0


def is_llm(r):
    return r["span_name"].startswith("chat ")


def is_desc(r, root_id):
    cur = r
    for _ in range(16):
        pid = cur.get("parent_span_id")
        if pid == root_id:
            return True
        cur = byid.get(pid)
        if cur is None:
            return False
    return False


def model_of(r):
    a = r["_a"]
    return a.get("gen_ai.response.model") or a.get("gen_ai.request.model") or "?"


def ancestors(r, maxhop=24):
    """Yield ancestor spans from nearest parent outward (bounded)."""
    cur = byid.get(r.get("parent_span_id"))
    for _ in range(maxhop):
        if cur is None:
            return
        yield cur
        cur = byid.get(cur.get("parent_span_id"))


# graph node class -> short role; search-graph name -> phase prefix
_NODE_ROLE = {"ExpanderNode": "expander", "RerankerNode": "reranker", "SearchNode": "search"}
_TOP_STAGE = {
    "router.classify": "router",
    "deep_search.planner": "planner",
    "deep_search.sector_picker": "sector_picker",
    "deep_search.aggregator": "aggregator",
    "summarize_workspace_item": "summarize",
    "ocr_extraction.run": "ocr",
}


def stage_label(r):
    """Pipeline-stage label for a `chat` span by walking its parent chain.

    Composes ``<phase>.<node>`` for search-graph calls (e.g. ``reg.reranker``,
    ``compliance.expander``) and falls back to the nearest top-level stage
    (``router``, ``planner``, ``aggregator``, ``sector_picker``, ``summarize``).
    Returns ``other`` when nothing recognizable is found.
    """
    node = phase = top = None
    for anc in ancestors(r):
        nm = anc["span_name"]
        if nm.startswith("run node "):
            cls = nm[len("run node "):]
            if node is None and cls in _NODE_ROLE:
                node = _NODE_ROLE[cls]
        elif nm.startswith("run graph ") and nm.endswith("_search_graph"):
            if phase is None:
                phase = nm[len("run graph "):-len("_search_graph")]
        elif nm.startswith("deep_search.phase."):
            if phase is None:
                phase = nm[len("deep_search.phase."):].split(".")[0]
        elif top is None and nm in _TOP_STAGE:
            top = _TOP_STAGE[nm]
    if node and phase:
        return f"{phase}.{node}"
    return node or top or phase or "other"


# ── cost (pipeline leaves cost_usd null; recompute) ──────────────────────────
try:
    from agents.utils.agent_models import cost_usd as _cost_usd

    def cost(model, ti, to, rz=0):
        try:
            return round(_cost_usd(model, ti, to, rz, 0), 6)
        except Exception:
            return 0.0
    COST_SRC = "agents.utils.agent_models.cost_usd"
except Exception as e:  # pragma: no cover
    def cost(model, ti, to, rz=0):
        return 0.0
    COST_SRC = f"UNAVAILABLE ({e})"


def _assistant_text(msgs):
    out = []
    for m in as_list(msgs):
        if isinstance(m, dict) and m.get("role") == "assistant":
            for p in as_list(m.get("parts") or m.get("content")):
                if isinstance(p, dict) and p.get("type") in ("text", "output_text"):
                    out.append(str(p.get("content", "")))
                elif isinstance(p, str):
                    out.append(p)
    return "\n".join(t for t in out if t.strip())


def render_messages(msgs):
    """Header is the role; body is the FULL message object (role + parts +
    finish_reason) as indented JSON, unless ``content`` is a plain string."""
    out = []
    for m in as_list(msgs):
        if isinstance(m, dict):
            role = m.get("role", "?")
            c = m.get("content")
            body = c if isinstance(c, str) else json.dumps(m, ensure_ascii=False, indent=2)
            out.append(f"#### [{role}]\n\n{body}\n")
        else:
            out.append(f"#### [?]\n\n{m}\n")
    return "\n".join(out)


# Fixed deep_search timing-waterfall template: (indent_depth, span_name). Stages
# absent from a turn are skipped; phase.case falls back to its `.skipped` span.
WF_TEMPLATE = [
    (0, "message.stream"), (1, "router.classify"), (1, "dispatch.specialist"),
    (2, "deep_search.planner"), (2, "deep_search.sector_picker"), (2, "deep_search.run_full_loop"),
    (3, "deep_search.phase.reg"), (3, "deep_search.phase.compliance"), (3, "deep_search.phase.case"),
    (2, "deep_search.aggregator"), (1, "publish.workspace_item"),
]


# ── 1. raw_spans.json ────────────────────────────────────────────────────────
json.dump(
    [{"start": r["start_timestamp"], "duration_s": r.get("duration"),
      "span_name": r["span_name"], "service": r["service_name"],
      "env": r.get("deployment_environment"), "trace_id": r["trace_id"],
      "span_id": r["span_id"], "parent_span_id": r.get("parent_span_id"),
      "level": r.get("level"), "is_exception": r.get("is_exception"),
      "attributes": r["_a"]} for r in rows],
    open(os.path.join(OUT, "raw_spans.json"), "w", encoding="utf-8"),
    ensure_ascii=False, indent=2,
)

# ── 2. spans_index.csv ───────────────────────────────────────────────────────
with open(os.path.join(OUT, "spans_index.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["idx", "start", "duration_s", "span_name", "service", "env",
                "trace_id", "span_id", "parent_span_id", "is_exception"])
    for i, r in enumerate(rows):
        w.writerow([i, r["start_timestamp"], r.get("duration"), r["span_name"],
                    r["service_name"], r.get("deployment_environment"), r["trace_id"],
                    r["span_id"], r.get("parent_span_id"), r.get("is_exception")])

# ── 3. llm_calls.csv + per-call md (model-named) ─────────────────────────────
llm = [r for r in rows if is_llm(r)]
tok_in = tok_out = tok_rz = 0
calls_by_model = Counter()
tok_by_model = {}
with open(os.path.join(OUT, "llm_calls.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["idx", "stage", "start", "model", "tokens_in", "tokens_out", "reasoning_tokens",
                "duration_s", "finish_reason", "est_cost_usd", "response_id", "span_id"])
    for i, r in enumerate(llm):
        a = r["_a"]
        model = model_of(r)
        label = stage_label(r)
        ti = int(a.get("gen_ai.usage.input_tokens", 0) or 0)
        to = int(a.get("gen_ai.usage.output_tokens", 0) or 0)
        rz = int(a.get("gen_ai.usage.details.reasoning_tokens", 0) or 0)
        tok_in += ti
        tok_out += to
        tok_rz += rz
        calls_by_model[model] += 1
        t = tok_by_model.setdefault(model, [0, 0, 0])
        t[0] += ti
        t[1] += to
        t[2] += rz
        fr = a.get("gen_ai.response.finish_reasons")
        rid = a.get("gen_ai.response.id")
        c = cost(model, ti, to, rz)
        w.writerow([i, label, r["start_timestamp"], model, ti, to, rz, round(secs(r), 3),
                    json.dumps(fr, ensure_ascii=False), c, rid, r["span_id"]])
        md = [f"# LLM call {i:02d} — {label} — {model}",
              f"- stage: {label}",
              f"- start: {r['start_timestamp']}  duration: {round(secs(r),3)}s",
              f"- tokens: in={ti} out={to} reasoning={rz}  est_cost=${c}",
              f"- finish: {fr}   response_id: {rid}",
              f"- span_id: {r['span_id']}  parent: {r.get('parent_span_id')}", ""]
        si = a.get("gen_ai.system_instructions")
        if si:
            si = si if isinstance(si, str) else json.dumps(si, ensure_ascii=False, indent=2)
            md += ["## System instructions", "", si, ""]
        md += ["## Input messages", "", render_messages(a.get("gen_ai.input.messages")), ""]
        md += ["## Output messages", "", render_messages(a.get("gen_ai.output.messages")), ""]
        open(os.path.join(OUT, "llm_calls", f"{i:02d}_{_safe(label)}_{_safe(model)}_{r['span_id']}.md"),
             "w", encoding="utf-8").write("\n".join(md))

# ── 4. stage_timeline.csv ────────────────────────────────────────────────────
stage_rows = [r for r in rows if r["span_name"] in {
    "message.stream", "ocr_extraction.run", "router.classify", "dispatch.specialist",
    "agent_runs.record", "publish.workspace_item", "summarize_workspace_item",
} or r["span_name"].startswith(("deep_search.", "run graph ", "run node "))]
with open(os.path.join(OUT, "stage_timeline.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["start", "stage/span", "env", "duration_s", "agent_family", "subtype",
                "model_used", "tokens_in", "tokens_out", "cost_usd", "outcome/status",
                "rounds_used", "rqr_count", "extra"])
    for r in stage_rows:
        a = r["_a"]
        extra = {k: a[k] for k in a if k.startswith(("dispatch.task", "dispatch.attached",
                 "dispatch.describe", "sector")) or (k.startswith("deep_search.") and k != "deep_search.")}
        dur = round(secs(r), 3) if r.get("duration") is not None else a.get("duration_ms")
        w.writerow([r["start_timestamp"], r["span_name"], r.get("deployment_environment"), dur,
                    a.get("agent_family"), a.get("subtype"), a.get("model_used"),
                    a.get("tokens_in") or a.get("total_tokens_in"),
                    a.get("tokens_out") or a.get("total_tokens_out"),
                    a.get("cost_usd"), a.get("outcome") or a.get("status"),
                    a.get("rounds_used"), a.get("rqr_count"),
                    json.dumps(extra, ensure_ascii=False) if extra else ""])

# ── 5. final_answer.md ───────────────────────────────────────────────────────
ms = next((r for r in rows if r["span_name"] == "message.stream"), None)
disp = next((r for r in rows if r["span_name"] == "dispatch.specialist"), None)
task_label = disp["_a"].get("dispatch.task_label") if disp else None
agg = next((r for r in rows if r["span_name"] == "deep_search.aggregator"), None)
answer = ""
if agg:
    agg_chats = sorted([r for r in llm if is_desc(r, agg["span_id"])],
                       key=lambda r: r["start_timestamp"])
    for c in reversed(agg_chats):
        t = _assistant_text(c["_a"].get("gen_ai.output.messages"))
        if t:
            answer = t
            break
if not answer:  # fallback: last LLM call with a substantial assistant text
    for r in reversed(llm):
        t = _assistant_text(r["_a"].get("gen_ai.output.messages"))
        if t and len(t) > 200:
            answer = t
            break
fa = ["# Final assistant output (last tier_1 / aggregator call)", "",
      f"**Question (task_label):** {task_label}", "",
      f"**Streamed answer chars:** {ms['_a'].get('full_content_chars') if ms else '?'}"
      f"  assistant_message_id: {ms['_a'].get('assistant_message_id') if ms else '?'}",
      "", "---", "", answer, ""]
open(os.path.join(OUT, "final_answer.md"), "w", encoding="utf-8").write("\n".join(fa))

# ── 6. cost_estimate.md ──────────────────────────────────────────────────────
total_cost = 0.0
cl = ["# Cost estimate (recomputed — pipeline stored cost_usd = NULL)", "",
      f"Cost source: `{COST_SRC}`", "",
      "| model | tier | calls | tokens_in | tokens_out | reasoning | est_cost_usd |",
      "|---|---|---|---|---|---|---|"]
for model, (ti, to, rz) in sorted(tok_by_model.items(), key=lambda x: -(x[1][0] + x[1][1])):
    c = cost(model, ti, to, rz)
    total_cost += c
    cl.append(f"| {model} | {MODEL_TIER.get(model,'?')} | {calls_by_model[model]} | {ti:,} | {to:,} | {rz:,} | ${c:.6f} |")
cl.append(f"| **TOTAL** | | {len(llm)} | {tok_in:,} | {tok_out:,} | {tok_rz:,} | **${total_cost:.6f}** |")
open(os.path.join(OUT, "cost_estimate.md"), "w", encoding="utf-8").write("\n".join(cl))

# ── 7. summary.md ────────────────────────────────────────────────────────────
main_trace = ms["trace_id"] if ms else (traces[0] if traces else "?")
family = (next((r for r in rows if r["span_name"] == "router.classify"), {}).get("_a", {}).get("agent_family")
          or (disp["_a"].get("agent_family") if disp else None))
ar_rows = [r for r in rows if r["span_name"] == "agent_runs.record"]
primary_ar = next((r for r in ar_rows if r["_a"].get("agent_family") == family), None)
outcome = (primary_ar["_a"].get("status") if primary_ar else None) or (ms["_a"].get("outcome") if ms else "?")
rollup = sum(int(r["_a"].get("tokens_in", 0) or 0) + int(r["_a"].get("tokens_out", 0) or 0) for r in ar_rows)
measured = tok_in + tok_out
span_counts = Counter(r["span_name"] for r in rows)

S = ["# Agentic turn — extraction report",
     f"Conversation: `{CONV}`",
     f"Main trace: `{main_trace}`", "",
     "## What this turn was",
     f"- **Question (task_label):** {task_label}",
     f"- **Route:** router.classify -> {family} (agent_family={family})",
     f"- **Attachments:** {ms['_a'].get('attachment_count') if ms else '?'}   "
     f"case_id: {ms['_a'].get('case_id') if ms else '?'}",
     f"- **Outcome:** {outcome}, answer streamed = "
     f"{ms['_a'].get('full_content_chars') if ms else '?'} chars", "",
     "## Timing waterfall (seconds)"]
byname = {}
for r in rows:
    byname.setdefault(r["span_name"], r)
for depth, name in WF_TEMPLATE:
    r = byname.get(name)
    skipped = False
    if r is None and name == "deep_search.phase.case":
        r = byname.get("deep_search.phase.case.skipped")
        skipped = True
    if r is None:
        continue
    indent = "  " * depth
    if skipped or r["span_name"].endswith(".skipped"):
        S.append(f"{indent}- {name}: SKIPPED")
    elif name == "message.stream":
        S.append(f"{indent}- message.stream (total): {secs(r):.1f}")
    else:
        S.append(f"{indent}- {name}: {secs(r):.1f}")
S += ["",
      "## LLM usage (from instrumented `chat` spans)",
      f"- total LLM calls: **{len(llm)}**",
      f"- total tokens: in **{tok_in:,}**, out **{tok_out:,}**, reasoning {tok_rz:,}",
      f"- **recomputed cost: ${total_cost:.4f}** (see cost_estimate.md)", "",
      "## agent_runs.record rows (the DB rollup)"]
for r in ar_rows:
    a = r["_a"]
    S.append(f"- family={a.get('agent_family')} subtype={a.get('subtype')} model={a.get('model_used')} "
             f"tokens_in={a.get('tokens_in')} tokens_out={a.get('tokens_out')} "
             f"**cost_usd={a.get('cost_usd')}** status={a.get('status')} run_id={a.get('run_id')} "
             f"env={r.get('deployment_environment')}")

# TRACKING GAPS — computed checks
gaps = []
gaps.append("`cost_usd` is NULL on every `agent_runs.record` row and on all wrapper stage spans "
            "(planner / run_full_loop / phase.* / aggregator). Only router.classify & sector_picker carry cost."
            if all(r["_a"].get("cost_usd") is None for r in ar_rows) else
            "Some `agent_runs.record` rows carry cost_usd; verify wrapper-stage coverage.")
gaps.append(f"DB rollup under-counts tokens vs actual `chat`-span consumption "
            f"(rollup ~{rollup:,} vs measured {measured:,})."
            if rollup < measured else
            f"DB rollup tokens (~{rollup:,}) vs measured chat tokens ({measured:,}).")
gaps.append("Wrapper stages carry duration+outcome but no model/tokens — usage lives only on child chat spans.")
if any(r["span_name"] == "deep_search.phase.compliance" for r in rows):
    gaps.append("`deep_search.phase.compliance` & `dispatch.specialist` use raw logfire spans with ad-hoc keys "
                "(total_tokens_in/out, dispatch.*) — compliance does not set `stage`.")
S += ["", "## TRACKING GAPS observed"]
S += [f"{i}. {g}" for i, g in enumerate(gaps, 1)]

S += ["", "## Span inventory (this turn)"]
for k, v in span_counts.most_common():
    S.append(f"- {v} x {k}")
S += ["", "## Files in this folder",
      "- `raw_spans.json` — every span, attributes parsed (full LLM prompts/responses live in llm_calls/)",
      "- `spans_index.csv` — flat index of all spans (parent/child via span_id/parent_span_id)",
      "- `stage_timeline.csv` — pipeline stage spans with tokens/cost/outcome",
      "- `llm_calls.csv` — one row per LLM call (stage, model, tokens, est cost)",
      "- `llm_calls/NN_<stage>_<model>_*.md` — full system+input+output messages of each LLM call "
      "(NN = chronological order; stage = router / planner / sector_picker / reg.* / compliance.* / aggregator / summarize)",
      "- `cost_estimate.md` — recomputed cost (pipeline stored NULL)",
      "- `final_answer.md` — the streamed answer + question"]
open(os.path.join(OUT, "summary.md"), "w", encoding="utf-8").write("\n".join(S))

# ── 8. README.md ─────────────────────────────────────────────────────────────
when = (rows[0]["start_timestamp"][:16].replace("T", " ") + " UTC") if rows else "?"
open(os.path.join(OUT, "README.md"), "w", encoding="utf-8").write(
    f"# convo_{CONV}\n\nLogfire extraction of one agentic deep_search turn ({when}).\n"
    f"Start with **summary.md**.\n")

print("WROTE", OUT)
print(f"spans={len(rows)} traces={len(traces)} llm={len(llm)} "
      f"tokens={tok_in}/{tok_out} cost=${total_cost:.4f}")
print("files:", sorted(os.listdir(OUT)))
