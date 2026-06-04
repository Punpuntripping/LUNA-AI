"""Compute LLM spend for one calendar day from the llm_calls ledger.

Two costing methods, side by side:

  * naive   — sum tokens per model, multiply by the model_pricing rate. This is
              the "take the model, sum input/output, apply pricing" method. It is
              biased by rows whose `model` column does not represent all their
              tokens (e.g. legacy deep_search rollup rows that stamp one model on
              a turn that actually fanned out across several).
  * corrected — recompute PER CALL with the project's own cost_usd() formula
              (reasoning billed at output rate, cached subset at cached rate);
              for rows whose `model` is NOT a real priced id (slot labels like
              `artifact_summarizer:tier_2`, or the bare `deep_search` rollup),
              fall back to the stored cost_usd, which was computed at write time
              from the true per-model split. This is the trustworthy number.

Usage:
  python scripts/cost_for_day.py 2026-06-02            # UTC calendar day
  python scripts/cost_for_day.py 2026-06-02 3          # Riyadh day (UTC+3)
"""
from __future__ import annotations

import datetime as dt
import sys
from collections import defaultdict

from shared.db.client import get_supabase_client
from shared.pricing import load_pricing, get_price
from agents.utils.agent_models import cost_usd

# Rows whose `model` column is unreliable for repricing:
#   - any value not found in model_pricing (slot labels like `*:tier_2`)
#   - the bare `deep_search` legacy rollup agent (one model stamped on a
#     multi-model turn). For these we trust the stored cost_usd instead.
ROLLUP_AGENTS = {"deep_search"}


def window(day: str, tz_offset_h: int):
    y, m, d = (int(x) for x in day.split("-"))
    base = dt.datetime(y, m, d, tzinfo=dt.timezone(dt.timedelta(hours=tz_offset_h)))
    start = base.astimezone(dt.timezone.utc)
    end = (base + dt.timedelta(days=1)).astimezone(dt.timezone.utc)
    return start.isoformat(), end.isoformat()


def fetch_all(client, start_iso: str, end_iso: str):
    rows, page, PAGE = [], 0, 1000
    while True:
        r = (client.table("llm_calls")
             .select("agent,model,tokens_in,tokens_out,tokens_reasoning,tokens_cached,cost_usd")
             .gte("created_at", start_iso).lt("created_at", end_iso)
             .order("created_at").range(page * PAGE, page * PAGE + PAGE - 1).execute())
        d = r.data or []
        rows.extend(d)
        if len(d) < PAGE:
            break
        page += 1
    return rows


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else "2026-06-02"
    tz = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    client = get_supabase_client()
    load_pricing(client)

    start_iso, end_iso = window(day, tz)
    rows = fetch_all(client, start_iso, end_iso)
    tzlabel = "UTC" if tz == 0 else f"UTC{tz:+d}"
    print(f"Day {day} ({tzlabel})   window {start_iso} -> {end_iso}")
    print(f"llm_calls rows: {len(rows)}\n")

    agg = defaultdict(lambda: {"in": 0, "out": 0, "reason": 0, "cached": 0,
                               "naive": 0.0, "corrected": 0.0, "stored": 0.0, "calls": 0})
    for row in rows:
        model = row.get("model") or "(null)"
        ti = int(row.get("tokens_in") or 0); to = int(row.get("tokens_out") or 0)
        tr = int(row.get("tokens_reasoning") or 0); tc = int(row.get("tokens_cached") or 0)
        stored = float(row.get("cost_usd") or 0)
        reliable = get_price(model) is not None and (row.get("agent") not in ROLLUP_AGENTS)
        a = agg[model]
        a["in"] += ti; a["out"] += to; a["reason"] += tr; a["cached"] += tc
        a["stored"] += stored
        a["naive"] += cost_usd(model, ti, to, tr, tc)            # reprice everything
        a["corrected"] += cost_usd(model, ti, to, tr, tc) if reliable else stored
        a["calls"] += 1

    h = f"{'model':<24}{'calls':>6}{'in':>12}{'out':>11}{'reason':>9}{'naive$':>11}{'corrected$':>12}{'stored$':>11}"
    print(h); print("-" * len(h))
    T = defaultdict(float); Ti = 0
    for model in sorted(agg, key=lambda k: -agg[k]["corrected"]):
        a = agg[model]
        print(f"{model:<24}{a['calls']:>6}{a['in']:>12,}{a['out']:>11,}{a['reason']:>9,}"
              f"{a['naive']:>11.5f}{a['corrected']:>12.5f}{a['stored']:>11.5f}")
        for k in ("naive", "corrected", "stored"):
            T[k] += a[k]
        Ti += a["calls"]
    print("-" * len(h))
    print(f"{'TOTAL':<24}{Ti:>6}{'':>12}{'':>11}{'':>9}{T['naive']:>11.5f}{T['corrected']:>12.5f}{T['stored']:>11.5f}")
    print()
    print(f"  naive  reprice-by-model   : ${T['naive']:.4f}   (biased high by rollup/slot rows)")
    print(f"  CORRECTED per-call cost    : ${T['corrected']:.4f}   <-- best estimate")
    print(f"  stored ledger cost_usd     : ${T['stored']:.4f}")


if __name__ == "__main__":
    main()
