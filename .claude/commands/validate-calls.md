---
name: validate-calls
description: Validate the llm_calls cost ledger against Logfire provider ground truth for one turn
user_invocable: true
allowed-tools: mcp__logfire__query_run, Bash, Read, Write
---

# /validate-calls — Is the cost ledger faithful for this turn?

Given a turn (a **message_id**, a **conversation_id**, or `last`), you check
whether the `llm_calls` ledger billed exactly what the LLM provider actually
charged. The ledger and Logfire are twins (written from one usage dict in
`agents/utils/tracking.py`); the raw `chat <model>` spans are the independent
provider ground truth. A token gap means a sub-call was dropped from the ledger —
the deep_search "manual aggregation misses a phase" bug class.

You run two `scripts/validate_llm_calls.py` calls with ONE Logfire MCP query in
between. The script owns all the Supabase reads, the reconciliation math, and the
exact Logfire SQL — you just execute that SQL and save the rows.

## Argument: $ARGUMENTS

Trim `$ARGUMENTS`. A UUID → use it (the script tries it as message_id then
conversation_id). `last` / `recent` / empty → the script auto-picks the most
recent real turn.

## Workflow

### Step 1 — Resolve the turn + dump the ledger side
```bash
cd C:/Programming/LUNA_AI && PYTHONUTF8=1 python scripts/validate_llm_calls.py resolve --turn "<ARG or last>"
```
This prints a JSON block and writes `<out_dir>/ledger.json`. Capture from the JSON:
`out_dir`, `conv_prefix`, `window_start`, `window_end`, and `logfire_sql`.
If `sibling_turns_in_window` is non-empty, note it — provider totals may include
a neighbouring turn (the report will mark deltas as upper bounds).

### Step 2 — Run the printed Logfire SQL (provider ground truth)
Run the **exact** `logfire_sql` string from Step 1 via `query_run`, passing the
window so the 30-minute default doesn't clip it:
```
query_run(
  project="rihan",
  start_timestamp=<window_start>,
  end_timestamp=<window_end>,
  query=<logfire_sql>,
)
```
The result is a small per-model aggregation (≤ a handful of rows, returns
inline). **Write it to `<out_dir>/chat_spans.json`** with the Write tool — keep
the whole `{"columns":[...],"rows":[...]}` object verbatim (the script reads
`rows`). If the result auto-saved to a file instead, read that file and write its
JSON to `<out_dir>/chat_spans.json`.

If the query returns **zero rows**, retry once with a wider window
(`window_start` − 10 min). Still empty → the conversation prefix didn't match or
the spans aged out; save the empty result anyway and let Step 3 report "NO DATA".

### Step 3 — Reconcile and report
```bash
cd C:/Programming/LUNA_AI && PYTHONUTF8=1 python scripts/validate_llm_calls.py reconcile --dir "<out_dir>"
```
This writes `<out_dir>/report.md` and prints a per-model 3-way table, a cost
reconciliation, the per-stage ledger, and a `VERDICT: PASS|FLAG|NO DATA`.

### Step 4 — Report back
Summarize concisely:
- the verdict (PASS / FLAG / NO DATA),
- the total ledger vs provider tokens and the cost Δ,
- for a FLAG: which model(s) are off, by how much, and the candidate stages,
- the report path.

When a model is flagged and the user wants the exact dropped sub-call, point
them at `/convo-monitor <conversation_id>` — it walks the parent chain and
attributes every `chat` span to its stage (this command stays per-model for
speed; convo-monitor is the heavyweight per-stage forensic dump).

## Rules
- You only **query** Logfire and **run the script** (which reads Supabase + writes
  under `agents_reports/llm_validation/`). Never modify pipeline/app code.
- Never invent numbers — every figure comes from the ledger rows or the spans.
- Logfire project is always **`rihan`**. If `query_run` errors "No project
  specified", add `project="rihan"`.
- `conversation_id` is PII-scrubbed in Logfire when its first segment is all
  digits (`[Scrubbed due to '<digits>']`) — that's why the SQL matches with
  `LIKE '%<prefix>%'` instead of `=`. Do not "fix" it to an equality match.
- The window is anchored to the ledger flush time (turn end); provider spans sit
  a few minutes before it. Trust the window the script computed.
