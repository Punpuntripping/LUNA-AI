"""Validate the llm_calls ledger against Logfire provider ground truth, per turn.

The cost ledger (``llm_calls``, migration 058) and the Logfire telemetry are
twins: both are written from the SAME usage dict at one chokepoint
(``agents/utils/tracking.py``). Independently, pydantic-ai's OpenAI
instrumentation emits one raw ``chat <model>`` span PER actual provider HTTP
call, carrying the provider's own ``gen_ai.usage.*`` counts — that is the
ground truth neither our ledger nor our wrapper spans can lie about.

This tool answers one question: **did the ledger faithfully bill what the
provider actually charged for this turn?** It compares, per model:

    ledger Σtokens   (llm_calls rows for the turn)
    provider Σtokens (raw `chat` spans in the turn's trace)

A gap means the ledger dropped a sub-call — exactly the class of bug that bit
deep_search (manual usage aggregation; see project_deep_search_ledger_granularity):
a phase whose inner usage never reaches ``record_call`` shows as provider tokens
with no matching ledger row.

Why two subcommands (not one script that does it all)
-----------------------------------------------------
Logfire is queryable ONLY through the MCP in this project (no read token in
scripts — same constraint that makes /convo-monitor an MCP-driven command).
Supabase, by contrast, we hit directly (like scripts/cost_for_day.py). So the
flow is: this script reads the ledger from Supabase and prints the exact Logfire
SQL to run; the /validate-calls command runs that SQL via the Logfire MCP and
saves the result; this script then reconciles the two.

    # 1. resolve the turn + dump the ledger side, print the Logfire SQL to run
    python scripts/validate_llm_calls.py resolve --turn last
    python scripts/validate_llm_calls.py resolve --turn <message_id | conversation_id>

    # 2. (the command runs the printed SQL via the Logfire MCP, saves rows to
    #     <dir>/chat_spans.json)

    # 3. reconcile ledger vs provider, write report.md, print the verdict
    python scripts/validate_llm_calls.py reconcile --dir agents_reports/llm_validation/<id>

Run standalone from the repo root; needs the same Supabase creds as the backend.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.db.client import get_supabase_client
from shared.pricing import load_pricing
from agents.utils.agent_models import cost_usd

OUT_ROOT = os.path.join("agents_reports", "llm_validation")

# Window around the turn. created_at on llm_calls is the FLUSH time (turn end,
# identical across all rows of a turn), so the provider chat spans sit a few
# minutes BEFORE it — look back generously, only a hair forward.
LOOKBACK_MIN = 8
LOOKAHEAD_SEC = 30

# Reconciliation tolerance: a model is "clean" when |Δ| is within both an
# absolute floor (rounding / a stray tiny call) and a relative band.
TOL_ABS = 150
TOL_REL = 0.005  # 0.5 %


# ── small helpers ─────────────────────────────────────────────────────────────
def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_created_at(s: str) -> dt.datetime:
    """Parse a Postgres timestamptz string ('2026-06-04 14:09:13.529139+00')."""
    s = s.strip().replace(" ", "T")
    # normalise '+00' → '+00:00' for fromisoformat
    if s.endswith("+00"):
        s = s[:-3] + "+00:00"
    try:
        return dt.datetime.fromisoformat(s)
    except ValueError:
        # last resort: drop fractional seconds
        head = s.split(".")[0]
        tz = s[len(head):]
        if tz and ":" not in tz:
            tz = tz + ":00"
        return dt.datetime.fromisoformat(head + (tz or "+00:00"))


def _rows_of(payload: object) -> list[dict]:
    """Accept a raw list, or the Logfire/MCP {'columns':..,'rows':..} envelope."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("rows") or []
    return []


# ── resolve ─────────────────────────────────────────────────────────────────
def _resolve_message_id(client, turn: str) -> tuple[str | None, str]:
    """Map the --turn argument to a (message_id, conversation_id).

    'last'/'recent'/'' → the most recent real turn (message_id not null).
    A UUID → tried as message_id first, then as conversation_id.
    Returns (message_id, conversation_id); message_id may be None only if the
    turn genuinely has none (background job) — not expected for this tool.
    """
    turn = (turn or "").strip().lower()
    if turn in ("", "last", "recent"):
        r = (client.table("llm_calls")
             .select("message_id,conversation_id,created_at")
             .not_.is_("message_id", "null")
             .order("created_at", desc=True).limit(1).execute())
        row = (r.data or [None])[0]
        if not row:
            sys.exit("No llm_calls rows with a message_id found.")
        return row["message_id"], row["conversation_id"]

    # A specific id. Try message_id, then conversation_id.
    r = (client.table("llm_calls").select("message_id,conversation_id")
         .eq("message_id", turn).limit(1).execute())
    if r.data:
        return r.data[0]["message_id"], r.data[0]["conversation_id"]
    r = (client.table("llm_calls").select("message_id,conversation_id,created_at")
         .eq("conversation_id", turn).not_.is_("message_id", "null")
         .order("created_at", desc=True).limit(1).execute())
    if r.data:
        return r.data[0]["message_id"], r.data[0]["conversation_id"]
    sys.exit(f"No llm_calls rows for id {turn!r} (tried message_id and conversation_id).")


def _fetch_ledger(client, message_id: str) -> list[dict]:
    r = (client.table("llm_calls")
         .select("agent,model,tokens_in,tokens_out,tokens_reasoning,tokens_cached,"
                 "cost_usd,requests,outcome,created_at,conversation_id")
         .eq("message_id", message_id).order("agent").execute())
    return r.data or []


def _sibling_turns(client, conversation_id: str, message_id: str,
                   start: dt.datetime, end: dt.datetime) -> list[str]:
    """Other turns of the same conversation inside the Logfire window. If any
    exist, the trace-by-conversation chat-span query may pull more than one
    turn → over-count. We warn rather than silently mis-reconcile."""
    r = (client.table("llm_calls").select("message_id,created_at")
         .eq("conversation_id", conversation_id)
         .not_.is_("message_id", "null")
         .gte("created_at", _iso(start)).lte("created_at", _iso(end)).execute())
    return sorted({row["message_id"] for row in (r.data or [])
                   if row["message_id"] != message_id})


def _aggregate_ledger(rows: list[dict]) -> tuple[dict, list[dict], dict]:
    per_model: dict[str, dict] = defaultdict(
        lambda: {"in": 0, "out": 0, "reason": 0, "cached": 0, "cost": 0.0, "rows": 0})
    per_stage: list[dict] = []
    tot = {"in": 0, "out": 0, "reason": 0, "cached": 0, "cost": 0.0}
    for row in rows:
        model = row.get("model") or "(null)"
        ti = int(row.get("tokens_in") or 0)
        to = int(row.get("tokens_out") or 0)
        tr = int(row.get("tokens_reasoning") or 0)
        tc = int(row.get("tokens_cached") or 0)
        c = float(row.get("cost_usd") or 0.0)
        m = per_model[model]
        m["in"] += ti; m["out"] += to; m["reason"] += tr; m["cached"] += tc
        m["cost"] += c; m["rows"] += 1
        tot["in"] += ti; tot["out"] += to; tot["reason"] += tr; tot["cached"] += tc
        tot["cost"] += c
        per_stage.append({"agent": row.get("agent"), "model": model, "in": ti,
                          "out": to, "reason": tr, "cached": tc, "cost": round(c, 6),
                          "outcome": row.get("outcome")})
    return per_model, per_stage, tot


def _logfire_sql(conv_prefix: str) -> str:
    """The chat-span aggregation the command runs via the Logfire MCP.

    Anchors to `message.stream` traces (excludes the detached memory/summarize
    trace, which has no message.stream) and matches conversation_id with a
    LIKE on the first segment — robust to Logfire's PII scrubber, which redacts
    all-digit id prefixes to `[Scrubbed due to '<digits>']` (the digits survive,
    so LIKE matches both scrubbed and unscrubbed values)."""
    return (
        "WITH turn_traces AS (\n"
        "  SELECT DISTINCT trace_id FROM records\n"
        "  WHERE span_name = 'message.stream'\n"
        f"    AND attributes->>'conversation_id' LIKE '%{conv_prefix}%'\n"
        ")\n"
        "SELECT attributes->>'gen_ai.response.model' AS model,\n"
        "       count(*) AS chat_calls,\n"
        "       sum(cast(attributes->>'gen_ai.usage.input_tokens'  AS bigint)) AS tok_in,\n"
        "       sum(cast(attributes->>'gen_ai.usage.output_tokens' AS bigint)) AS tok_out,\n"
        "       sum(cast(attributes->>'gen_ai.usage.details.reasoning_tokens' AS bigint)) AS tok_reason,\n"
        "       sum(cast(attributes->>'gen_ai.usage.details.cached_tokens' AS bigint)) AS tok_cached\n"
        "FROM records\n"
        "WHERE span_name LIKE 'chat %'\n"
        "  AND trace_id IN (SELECT trace_id FROM turn_traces)\n"
        "GROUP BY 1 ORDER BY tok_in DESC NULLS LAST LIMIT 50;"
    )


def cmd_resolve(args) -> None:
    client = get_supabase_client()
    message_id, conversation_id = _resolve_message_id(client, args.turn)
    rows = _fetch_ledger(client, message_id)
    if not rows:
        sys.exit(f"No llm_calls rows for message_id {message_id}.")

    created_at = max(_parse_created_at(r["created_at"]) for r in rows)
    win_start = created_at - dt.timedelta(minutes=LOOKBACK_MIN)
    win_end = created_at + dt.timedelta(seconds=LOOKAHEAD_SEC)
    conv_prefix = (conversation_id or "").split("-")[0]
    siblings = _sibling_turns(client, conversation_id, message_id, win_start, win_end)

    per_model, per_stage, tot = _aggregate_ledger(rows)
    out_dir = os.path.join(OUT_ROOT, message_id)
    os.makedirs(out_dir, exist_ok=True)
    meta = {
        "message_id": message_id,
        "conversation_id": conversation_id,
        "conv_prefix": conv_prefix,
        "created_at": _iso(created_at),
        "window_start": _iso(win_start),
        "window_end": _iso(win_end),
        "sibling_turns_in_window": siblings,
    }
    with open(os.path.join(out_dir, "ledger.json"), "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "per_model": per_model, "per_stage": per_stage,
                   "totals": tot}, f, ensure_ascii=False, indent=2)

    # Stdout: everything the command needs to run the Logfire query + reconcile.
    print(json.dumps({
        "message_id": message_id,
        "conversation_id": conversation_id,
        "conv_prefix": conv_prefix,
        "window_start": _iso(win_start),
        "window_end": _iso(win_end),
        "sibling_turns_in_window": siblings,
        "out_dir": out_dir,
        "ledger_totals": {k: round(v, 6) if k == "cost" else v for k, v in tot.items()},
        "ledger_stages": [s["agent"] for s in per_stage],
        "logfire_project": "rihan",
        "logfire_sql": _logfire_sql(conv_prefix),
        "next": (f"Run logfire_sql via query_run(project='rihan', "
                 f"start_timestamp='{_iso(win_start)}', end_timestamp='{_iso(win_end)}'), "
                 f"save rows to {os.path.join(out_dir, 'chat_spans.json')}, then: "
                 f"python scripts/validate_llm_calls.py reconcile --dir {out_dir}"),
    }, ensure_ascii=False, indent=2))
    if siblings:
        print(f"\nWARNING: {len(siblings)} other turn(s) of this conversation fall in the "
              f"Logfire window — the chat-span query may pull >1 turn and over-count. "
              f"Sibling message_ids: {siblings}", file=sys.stderr)


# ── reconcile ─────────────────────────────────────────────────────────────────
def _clean(delta: int, provider_total: int) -> bool:
    return abs(delta) <= max(TOL_ABS, int(TOL_REL * max(provider_total, 0)))


def cmd_reconcile(args) -> None:
    out_dir = args.dir
    with open(os.path.join(out_dir, "ledger.json"), encoding="utf-8") as f:
        ledger = json.load(f)
    chat_path = os.path.join(out_dir, "chat_spans.json")
    if not os.path.exists(chat_path):
        sys.exit(f"Missing {chat_path}. Run the Logfire query first (see resolve output).")
    with open(chat_path, encoding="utf-8") as f:
        chat_rows = _rows_of(json.load(f))

    client = get_supabase_client()
    load_pricing(client)  # cost_usd needs the pricing registry populated

    meta = ledger["meta"]
    led_model = ledger["per_model"]

    # Provider side, per model. (chat spans report the response model, which
    # matches the ledger `model` column for the Alibaba primary path.)
    prov: dict[str, dict] = defaultdict(
        lambda: {"in": 0, "out": 0, "reason": 0, "cached": 0, "calls": 0})
    for r in chat_rows:
        model = r.get("model") or "(null)"
        p = prov[model]
        p["in"] += int(r.get("tok_in") or 0)
        p["out"] += int(r.get("tok_out") or 0)
        p["reason"] += int(r.get("tok_reason") or 0)
        p["cached"] += int(r.get("tok_cached") or 0)
        p["calls"] += int(r.get("chat_calls") or 0)

    models = sorted(set(led_model) | set(prov), key=lambda m: -(prov.get(m, {}).get("in", 0)))

    lines: list[str] = []
    L = lines.append
    L(f"# llm_calls ↔ Logfire validation — turn `{meta['message_id']}`\n")
    L(f"- conversation: `{meta['conversation_id']}`")
    L(f"- turn flush (created_at): {meta['created_at']}")
    L(f"- Logfire window: {meta['window_start']} → {meta['window_end']}  (project `rihan`)")
    if meta.get("sibling_turns_in_window"):
        L(f"- ⚠️ **{len(meta['sibling_turns_in_window'])} sibling turn(s) in window** — "
          f"provider totals may include another turn; treat deltas as upper bounds.")
    if not chat_rows:
        L("\n**No provider `chat` spans found for this turn.** Either the Logfire window "
          "missed it, the conversation prefix did not match, or the spans aged out of "
          "retention. Cannot ground-truth this turn.")
        _write_and_print(out_dir, lines, verdict="NO DATA", flagged=[])
        return

    # ── per-model 3-way ──
    L("\n## Per-model reconciliation (provider chat spans = ground truth)\n")
    L("| model | ledger in/out | provider in/out | Δ in | Δ out | Δ reason | ledger calls→provider |")
    L("|---|---|---|---|---|---|---|")
    flagged: list[str] = []
    tot_led = {"in": 0, "out": 0, "reason": 0, "cost": 0.0}
    tot_prov = {"in": 0, "out": 0, "reason": 0, "cost": 0.0}
    for m in models:
        lm = led_model.get(m, {"in": 0, "out": 0, "reason": 0, "cached": 0, "cost": 0.0, "rows": 0})
        pm = prov.get(m, {"in": 0, "out": 0, "reason": 0, "cached": 0, "calls": 0})
        din = pm["in"] - lm["in"]; dout = pm["out"] - lm["out"]; drea = pm["reason"] - lm["reason"]
        ok = _clean(din, pm["in"]) and _clean(dout, pm["out"])
        if not ok:
            flagged.append(m)
        # provider-side cost recomputed with the SAME formula the ledger used.
        prov_cost = cost_usd(m, pm["in"], pm["out"], pm["reason"], pm["cached"])
        tot_led["in"] += lm["in"]; tot_led["out"] += lm["out"]; tot_led["reason"] += lm["reason"]
        tot_led["cost"] += float(lm["cost"])
        tot_prov["in"] += pm["in"]; tot_prov["out"] += pm["out"]; tot_prov["reason"] += pm["reason"]
        tot_prov["cost"] += prov_cost
        mark = "" if ok else " ⚠️"
        L(f"| {m}{mark} | {lm['in']:,}/{lm['out']:,} | {pm['in']:,}/{pm['out']:,} "
          f"| {din:+,} | {dout:+,} | {drea:+,} | {lm['rows']}→{pm['calls']} |")
    L(f"| **TOTAL** | {tot_led['in']:,}/{tot_led['out']:,} | "
      f"{tot_prov['in']:,}/{tot_prov['out']:,} | {tot_prov['in']-tot_led['in']:+,} "
      f"| {tot_prov['out']-tot_led['out']:+,} | {tot_prov['reason']-tot_led['reason']:+,} | |")

    # ── cost ──
    # in/out tokens are the authoritative dropped-call signal. Reasoning is
    # reported by the provider WITHIN output_tokens but tracked in its own ledger
    # column, so a reasoning-only delta is an accounting nuance, not a dropped
    # call — and `cost_usd` adds reasoning to billable_out, so a provider recompute
    # that inherits the provider's (higher) reasoning count would look like the
    # ledger "under-bills" when in/out actually match. Key the wording off in/out.
    in_out_clean = (_clean(tot_prov["in"] - tot_led["in"], tot_prov["in"])
                    and _clean(tot_prov["out"] - tot_led["out"], tot_prov["out"]))
    L("\n## Cost\n")
    L(f"- ledger stored cost (what actually billed): **${tot_led['cost']:.6f}**")
    L(f"- provider-token recompute (same `cost_usd()`): **${tot_prov['cost']:.6f}**")
    dc = tot_prov["cost"] - tot_led["cost"]
    L(f"- Δ (provider − ledger): **${dc:+.6f}**")
    if in_out_clean:
        L("- in/out tokens match the provider, so the **billed cost is sound**. The Δ above is "
          "a reasoning-column accounting difference (reasoning is reported within output_tokens), "
          "not a dropped call.")
    elif dc > 1e-6:
        L("- **ledger UNDER-bills** — driven by the flagged token gap above (provider made calls "
          "the ledger did not fully capture).")
    else:
        L("- ledger over-bills relative to the provider recompute — investigate the flagged model.")

    # ── per-stage ledger (context for localizing a flagged model) ──
    L("\n## Per-stage ledger (for localization)\n")
    L("| stage (agent) | model | in | out | reason | cost | outcome |")
    L("|---|---|---|---|---|---|---|")
    for s in ledger["per_stage"]:
        L(f"| {s['agent']} | {s['model']} | {s['in']:,} | {s['out']:,} | {s['reason']:,} "
          f"| ${s['cost']:.6f} | {s['outcome']} |")

    # ── verdict ──
    verdict = "PASS" if not flagged else "FLAG"
    L("\n## Verdict\n")
    if not flagged:
        L("**PASS** — every model's in/out tokens reconcile to the provider within tolerance. "
          "The ledger faithfully captured this turn.")
    else:
        L(f"**FLAG** — ledger under/over-counts for: **{', '.join(flagged)}**. "
          "Provider made calls the ledger did not fully bill (or vice-versa). Likely a "
          "manual-aggregation gap (deep_search) or a `record_call` outside the capture "
          "scope. The stages using a flagged model are listed above — to pin the exact "
          f"dropped sub-call, run `/convo-monitor {meta['conversation_id']}` (it walks the "
          "parent chain and attributes every `chat` span to its stage).")
    if abs(tot_prov["reason"] - tot_led["reason"]) > TOL_ABS:
        L(f"\n> Note: the ledger's reasoning column differs from the provider by "
          f"{tot_prov['reason']-tot_led['reason']:+,} tokens. Reasoning is reported *within* "
          "output_tokens (e.g. router.classify out=317 of which reason=253), so — when in/out "
          "reconcile — this is a reasoning-capture nuance in the per-call usage plumbing, NOT a "
          "dropped sub-call. It does not change the billed in/out totals.")

    _write_and_print(out_dir, lines, verdict, flagged)


def _write_and_print(out_dir: str, lines: list[str], verdict: str, flagged: list[str]) -> None:
    report = os.path.join(out_dir, "report.md")
    with open(report, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nWROTE {report}")
    print(f"VERDICT: {verdict}" + (f"  flagged_models={flagged}" if flagged else ""))


def main() -> None:
    ap = argparse.ArgumentParser(description="Validate the llm_calls ledger against Logfire chat spans.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("resolve", help="resolve a turn, dump the ledger side, print the Logfire SQL")
    r.add_argument("--turn", default="last", help="message_id, conversation_id, or 'last'")
    r.set_defaults(func=cmd_resolve)
    rec = sub.add_parser("reconcile", help="reconcile ledger.json against chat_spans.json in --dir")
    rec.add_argument("--dir", required=True, help="the out_dir printed by resolve")
    rec.set_defaults(func=cmd_reconcile)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
