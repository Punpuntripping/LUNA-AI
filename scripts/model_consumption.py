"""Per-model LLM consumption for a single calendar day, from the llm_calls ledger.

For the given day it groups every llm_calls row by `model` and reports, per model:
calls, input / output / reasoning / cached tokens, total tokens, and cost.

Cost is the CORRECTED per-call figure (the cost_for_day.py method): recompute
each call from the model_pricing table via the project's own cost_usd() formula
(reasoning billed at output rate, cached subset at cached rate), but fall back to
the stored cost_usd for rows whose `model` is not a real priced id (memory slot
labels like `artifact_summarizer:tier_2`) or the legacy bare `deep_search` rollup
(one model stamped on a multi-model turn). See project_llm_calls_reprice_traps.

Token attribution caveat: the same mislabeled rows put their tokens under the
wrong/placeholder model name. The report flags how many tokens/cost that touches
so the per-model split can be read with eyes open.

Usage:
    python scripts/model_consumption.py 2026-06-02
    python scripts/model_consumption.py 2026-06-02 --tz 3          # Riyadh day (UTC+3)
    python scripts/model_consumption.py 2026-06-02 --agent deep_search   # only deep_search.*
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collections import defaultdict

from shared.db.client import get_supabase_client
from shared.pricing import load_pricing, get_price
from agents.utils.agent_models import cost_usd

# Rows whose `model` column is unreliable for repricing / attribution:
#   - the bare `deep_search` legacy rollup (one model stamped on a multi-model turn)
# plus, detected dynamically: any model not present in model_pricing (slot labels).
ROLLUP_AGENTS = {"deep_search"}


def day_window(day: str, tz_offset_h: int) -> tuple[str, str]:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise SystemExit(f"error: day must be YYYY-MM-DD, got {day!r}")
    y, m, d = (int(x) for x in day.split("-"))
    tz = dt.timezone(dt.timedelta(hours=tz_offset_h))
    base = dt.datetime(y, m, d, tzinfo=tz)
    return base.astimezone(dt.timezone.utc).isoformat(), \
        (base + dt.timedelta(days=1)).astimezone(dt.timezone.utc).isoformat()


def fetch(client, start_iso: str, end_iso: str, agent_prefix: str | None) -> list[dict]:
    rows, page, PAGE = [], 0, 1000
    while True:
        q = (client.table("llm_calls")
             .select("agent,model,tokens_in,tokens_out,tokens_reasoning,tokens_cached,cost_usd")
             .gte("created_at", start_iso).lt("created_at", end_iso))
        if agent_prefix:
            q = q.like("agent", f"{agent_prefix}%")
        r = q.order("created_at").range(page * PAGE, page * PAGE + PAGE - 1).execute()
        d = r.data or []
        rows.extend(d)
        if len(d) < PAGE:
            break
        page += 1
    return rows


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("day", nargs="?", help="YYYY-MM-DD (default: today UTC)")
    p.add_argument("--tz", type=int, default=0, help="timezone offset hours for the day boundary (default 0=UTC)")
    p.add_argument("--agent", default=None, help="only count rows whose agent starts with this prefix")
    a = p.parse_args()

    day = a.day or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    start_iso, end_iso = day_window(day, a.tz)

    client = get_supabase_client()
    load_pricing(client)
    rows = fetch(client, start_iso, end_iso, a.agent)

    tzlabel = "UTC" if a.tz == 0 else f"UTC{a.tz:+d}"
    scope = f"agent~'{a.agent}*'" if a.agent else "all agents"
    print(f"Day {day} ({tzlabel})   window {start_iso} -> {end_iso}")
    print(f"scope: {scope}   |   llm_calls rows: {len(rows)}\n")
    if not rows:
        print("(no calls in window)")
        return

    agg = defaultdict(lambda: {"calls": 0, "in": 0, "out": 0, "re": 0, "cached": 0, "cost": 0.0})
    # data-quality accounting
    bad_tokens = 0  # tokens under an unreliable model label
    bad_cost = 0.0
    for row in rows:
        model = row.get("model") or "(null)"
        ti = int(row.get("tokens_in") or 0); to = int(row.get("tokens_out") or 0)
        tr = int(row.get("tokens_reasoning") or 0); tc = int(row.get("tokens_cached") or 0)
        stored = float(row.get("cost_usd") or 0)
        priced = get_price(model) is not None
        reliable = priced and (row.get("agent") not in ROLLUP_AGENTS)
        cost = cost_usd(model, ti, to, tr, tc) if reliable else stored
        g = agg[model]
        g["calls"] += 1; g["in"] += ti; g["out"] += to; g["re"] += tr; g["cached"] += tc
        g["cost"] += cost
        if not reliable:
            bad_tokens += ti + to + tr
            bad_cost += cost

    h = (f"{'model':<26}{'calls':>6}{'input':>13}{'output':>12}{'reason':>11}"
         f"{'cached':>10}{'total':>13}{'cost$':>12}")
    print(h); print("-" * len(h))
    T = {"calls": 0, "in": 0, "out": 0, "re": 0, "cached": 0, "cost": 0.0}
    for model in sorted(agg, key=lambda k: -agg[k]["cost"]):
        g = agg[model]
        tot = g["in"] + g["out"] + g["re"]
        mark = "" if (get_price(model) is not None) else "  (!)"
        print(f"{model:<26}{g['calls']:>6}{g['in']:>13,}{g['out']:>12,}{g['re']:>11,}"
              f"{g['cached']:>10,}{tot:>13,}{g['cost']:>12.5f}{mark}")
        for k in T:
            T[k] += g[k]
    gtot = T["in"] + T["out"] + T["re"]
    print("-" * len(h))
    print(f"{'TOTAL':<26}{T['calls']:>6}{T['in']:>13,}{T['out']:>12,}{T['re']:>11,}"
          f"{T['cached']:>10,}{gtot:>13,}{T['cost']:>12.5f}")
    print(f"\nTOTAL cost: ${T['cost']:.4f}   |   total tokens: {gtot:,} "
          f"(in {T['in']:,} / out {T['out']:,} / reasoning {T['re']:,})")

    if bad_tokens or any(get_price(m) is None for m in agg):
        print("\n(!) data-quality note:")
        for m in sorted(agg):
            if get_price(m) is None:
                print(f"    '{m}': not a priced model id (memory slot label) -> "
                      f"cost taken from stored cost_usd; tokens really ran on a tier_2 flash model.")
        print(f"    ~{bad_tokens:,} tokens / ${bad_cost:.4f} sit under an unreliable model label "
              f"(slot labels or legacy bare-deep_search rollup). Cost is correct; "
              f"per-model token split for those is approximate.")


if __name__ == "__main__":
    main()
