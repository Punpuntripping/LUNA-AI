"""Average tokens per ONE deep_search request (planning -> planner responder).

A deep_search request = all `deep_search.*` ledger rows sharing a run_id.
The `deep_search.planner` row already combines decider (phase 1) + responder
(phase 3) per agents/deep_search_v4/planner/runner.py:_emit_planner_ledger.

Total tokens = tokens_in + tokens_out + tokens_reasoning
(cached is a subset of input, already inside tokens_in — not added again).

Two telemetry eras are reported separately:
  * NEW   — granular per-stage rows grouped by run_id (current pipeline).
  * LEGACY— bare `deep_search` rollup rows (run_id NULL), one row per request.
"""
from __future__ import annotations
import datetime as dt
import statistics as st
import sys
from collections import defaultdict, Counter
from shared.db.client import get_supabase_client

c = get_supabase_client()

# Optional lookback window: `python ds_avg_tokens.py 5` -> last 5 days (rolling).
days = int(sys.argv[1]) if len(sys.argv) > 1 else None
cutoff_iso = None
if days is not None:
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    cutoff_iso = cutoff.isoformat()

rows, page, PAGE = [], 0, 1000
while True:
    q = (c.table("llm_calls")
         .select("message_id,run_id,agent,model,tokens_in,tokens_out,tokens_reasoning,cost_usd,created_at"))
    if cutoff_iso:
        q = q.gte("created_at", cutoff_iso)
    r = q.order("created_at").range(page*PAGE, page*PAGE+PAGE-1).execute()
    d = r.data or []
    rows.extend(d)
    if len(d) < PAGE:
        break
    page += 1

if rows:
    win = f"last {days} days (>= {cutoff_iso})" if days else "all time"
    print(f"window: {win}")
    print(f"rows in window: {len(rows)}   ({rows[0]['created_at']} -> {rows[-1]['created_at']})")


def tot(r):
    return (int(r.get("tokens_in") or 0) + int(r.get("tokens_out") or 0)
            + int(r.get("tokens_reasoning") or 0))


ds = [r for r in rows if (r.get("agent") or "").startswith("deep_search")]

# ── NEW era: granular rows with a run_id ──────────────────────────────────────
new_rows = [r for r in ds if r.get("run_id")]
by_run = defaultdict(list)
for r in new_rows:
    by_run[r["run_id"]].append(r)

# ── LEGACY era: bare `deep_search` rollup, run_id NULL (one row == one request)─
legacy_rows = [r for r in ds if not r.get("run_id") and r.get("agent") == "deep_search"]


def summarize(name, per_request_totals):
    vals = sorted(per_request_totals)
    if not vals:
        print(f"\n{name}: no requests")
        return
    print(f"\n=== {name} — {len(vals)} requests ===")
    print(f"  mean   total tokens/request : {st.mean(vals):>12,.0f}")
    print(f"  median total tokens/request : {st.median(vals):>12,.0f}")
    print(f"  min / max                   : {vals[0]:>12,} / {vals[-1]:,}")
    if len(vals) > 1:
        print(f"  stdev                       : {st.pstdev(vals):>12,.0f}")


# NEW era per-request totals + token split + per-stage averages
new_totals, new_in, new_out, new_re = [], [], [], []
stage_tok = defaultdict(list)
stage_present = Counter()
for rid, items in by_run.items():
    t = sum(tot(r) for r in items)
    new_totals.append(t)
    new_in.append(sum(int(r.get("tokens_in") or 0) for r in items))
    new_out.append(sum(int(r.get("tokens_out") or 0) for r in items))
    new_re.append(sum(int(r.get("tokens_reasoning") or 0) for r in items))
    seen = set()
    for r in items:
        stage_tok[r["agent"]].append(tot(r))
        seen.add(r["agent"])
    for a in seen:
        stage_present[a] += 1

summarize("NEW era (run_id-grouped, current pipeline)", new_totals)
if new_totals:
    n = len(new_totals)
    print(f"  avg input/req={sum(new_in)/n:,.0f}  output/req={sum(new_out)/n:,.0f}  reasoning/req={sum(new_re)/n:,.0f}")
    print(f"  avg stages/req: {sum(len(v) for v in by_run.values())/n:.1f}")
    print("\n  per-stage avg tokens (and % of requests containing the stage):")
    for stage in sorted(stage_tok, key=lambda s: -sum(stage_tok[s])/len(stage_tok[s])):
        vals = stage_tok[stage]
        pct = 100 * stage_present[stage] / n
        print(f"    {stage:<34}{st.mean(vals):>10,.0f} tok   in {pct:>3.0f}% of requests")

# ── Per-MODEL breakdown (NEW era only — legacy rows have a mislabeled model) ──
if new_rows:
    nreq = len(by_run)
    per_model = defaultdict(lambda: {"in": 0, "out": 0, "re": 0, "calls": 0, "stages": set()})
    for r in new_rows:
        m = r.get("model") or "(null)"
        pm = per_model[m]
        pm["in"] += int(r.get("tokens_in") or 0)
        pm["out"] += int(r.get("tokens_out") or 0)
        pm["re"] += int(r.get("tokens_reasoning") or 0)
        pm["calls"] += 1
        pm["stages"].add(r.get("agent"))
    print(f"\n=== PER-MODEL token usage (NEW era, {nreq} requests) ===")
    h = (f"{'model':<20}{'calls':>6}{'input':>13}{'output':>12}{'reason':>11}{'total':>13}"
         f"{'in/req':>11}{'out/req':>10}")
    print(h); print("-" * len(h))
    grand = {"in": 0, "out": 0, "re": 0, "calls": 0}
    for m in sorted(per_model, key=lambda k: -(per_model[k]["in"] + per_model[k]["out"] + per_model[k]["re"])):
        pm = per_model[m]
        t = pm["in"] + pm["out"] + pm["re"]
        print(f"{m:<20}{pm['calls']:>6}{pm['in']:>13,}{pm['out']:>12,}{pm['re']:>11,}{t:>13,}"
              f"{pm['in']/nreq:>11,.0f}{pm['out']/nreq:>10,.0f}")
        for k in ("in", "out", "re", "calls"):
            grand[k] += pm[k]
    gt = grand["in"] + grand["out"] + grand["re"]
    print("-" * len(h))
    print(f"{'TOTAL':<20}{grand['calls']:>6}{grand['in']:>13,}{grand['out']:>12,}{grand['re']:>11,}{gt:>13,}"
          f"{grand['in']/nreq:>11,.0f}{grand['out']/nreq:>10,.0f}")
    print("\n  stages per model:")
    for m in sorted(per_model):
        print(f"    {m:<20} {sorted(s.replace('deep_search.', '') for s in per_model[m]['stages'])}")

legacy_totals = [tot(r) for r in legacy_rows]
summarize("LEGACY era (bare deep_search rollup rows)", legacy_totals)

# Combined view
allt = new_totals + legacy_totals
summarize("COMBINED (all eras)", allt)
