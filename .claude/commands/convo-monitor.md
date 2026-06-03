---
name: convo-monitor
description: Forensic Logfire report for one Luna conversation by conversation_id (convo_ffdf format)
user_invocable: true
allowed-tools: mcp__logfire__query_run, Bash, Read, Write
---

# /convo-monitor — Forensic report for one conversation

Given a **conversation_id**, you turn raw Pydantic Logfire telemetry into a
self-contained forensic report under
`agents_reports/agentic_monitor/convo_<conversation_id>/`, then summarize it for
the user. The output **must match the `convo_ffdf6546-…` report layout exactly**
(model-named LLM-call files, a `summary.md` with timing waterfall + TRACKING GAPS,
a singular `final_answer.md`) — that layout is produced mechanically by
`scripts/convo_monitor_extract.py`, which is the only script you run.

## Argument: $ARGUMENTS

The argument is a **conversation_id**, or the word `last` / `recent` to
auto-pick the most recent conversation. Parse `$ARGUMENTS` (trimmed):
- A UUID-shaped string → use it as the conversation_id.
- `last` / `recent` / empty → discover the most recent conversation (Step 1b).

## Logfire facts (read once — these are hard constraints)

- Project is **`rihan`** — always pass `project="rihan"` to `query_run`.
- `query_run` applies a **default 30-minute window**. For anything older, pass
  explicit `start_timestamp`/`end_timestamp` params. A SQL `WHERE` time filter is
  *intersected* with the window and will NOT widen it — always pass the params for
  lookbacks. Max range 14 days.
- **`query_run` returns at most 100 rows per call** (hard cap, regardless of SQL
  `LIMIT`). You MUST paginate large span dumps with `LIMIT 100 OFFSET …` and a
  stable `ORDER BY start_timestamp, span_id`. Large results auto-save to a file —
  capture the path from the result message (`Output has been saved to <PATH>`).
- `conversation_id` is stamped **only on pipeline spans** (`message.stream`,
  `router.classify`, `dispatch.specialist`, `deep_search.*`, `publish.*`,
  `summarize_workspace_item`, `agent_runs.record`). The child spans (`chat <model>`,
  `agent run`, `run node …`, HTTP `GET`/`POST`) do **NOT** carry it — they join to
  a conversation by **`trace_id`**.
- One user turn can **detach** into a second trace (the memory/summarize webhook
  runs in env=production under its own trace_id). Only **2 spans** of that detached
  trace carry `conversation_id` (`summarize_workspace_item` + memory
  `agent_runs.record`). Pull **only those stamped spans** — do NOT dump the full
  detached trace, or its `chat` span would be miscounted as a pipeline LLM call.

## Workflow

### Step 1 — Resolve the conversation_id
- **1a.** If a UUID was given, use it. Set a generous time window around it
  (default: a few hours; if the user names a date, bracket that day).
- **1b.** If `last`/`recent`/empty, discover it (pass an explicit `start_timestamp`
  a few hours back):
  ```sql
  SELECT attributes->>'conversation_id' AS conv, trace_id, max(start_timestamp) AS last
  FROM records WHERE span_name='message.stream'
  GROUP BY conv, trace_id ORDER BY last DESC LIMIT 10
  ```
  Pick the most recent `conv` and tell the user which one you chose.

### Step 2 — Find the main trace_id(s)
Every `message.stream` row for the conversation (a turn = one main trace):
```sql
SELECT trace_id, start_timestamp FROM records
WHERE span_name='message.stream' AND attributes->>'conversation_id'='<CONV>'
ORDER BY start_timestamp LIMIT 20
```
Use the surrounding timestamps to set a tight window (e.g. 15 min before the
first to 20 min after the last) for the dump queries below.

### Step 3 — Dump each main trace fully (paginated, 100/page)
For **each** main `trace_id`, page until a page returns fewer than 100 rows
(`OFFSET` 0, 100, 200, …):
```sql
SELECT start_timestamp,end_timestamp,duration,span_name,service_name,deployment_environment,
       trace_id,span_id,parent_span_id,level,is_exception,attributes
FROM records WHERE trace_id='<TID>'
ORDER BY start_timestamp, span_id LIMIT 100 OFFSET <N>
```
Each call auto-saves to a file — keep every saved path. A trace of ~330 spans is
4 pages. (Issue the offset pages in parallel for speed; you know the page count
after the first page tells you the trace is full.)

### Step 4 — Pull the detached (memory) spans
Capture the `conversation_id`-stamped spans that live outside the main trace(s):
```sql
SELECT start_timestamp,end_timestamp,duration,span_name,service_name,deployment_environment,
       trace_id,span_id,parent_span_id,level,is_exception,attributes
FROM records WHERE attributes->>'conversation_id'='<CONV>'
  AND trace_id != '<MAIN_TID>'        -- repeat AND trace_id != … for each main trace
ORDER BY start_timestamp, span_id LIMIT 100
```
This is usually tiny (the 2 summarize spans) and returns **inline**, not saved to a
file. When it returns inline, write the JSON to a file yourself so the extractor can
read it, e.g.:
```bash
# Save the inline {"columns":[…],"rows":[…]} result to a file the extractor can read.
# (Write the exact tool JSON to this path with the Write tool — keep the "rows" key.)
agents_reports/agentic_monitor/_detached_<CONV>.json
```
If there are no detached spans, skip this file.

### Step 5 — Run the extractor (the only script you run)
Quote every path (Windows paths contain backslashes/spaces):
```bash
cd C:/Programming/LUNA_AI && PYTHONUTF8=1 python scripts/convo_monitor_extract.py \
  --conv <CONV> \
  --traces "<page0>" "<page1>" "<page2>" "<page3>" "<detached_file>"
```
The script merges all dump files by `span_id`, recomputes cost (the pipeline stores
`cost_usd=NULL`), and writes the full report to
`agents_reports/agentic_monitor/convo_<CONV>/`:
`summary.md`, `final_answer.md`, `cost_estimate.md`, `llm_calls.csv`,
`llm_calls/NN_<model>_<span_id>.md`, `stage_timeline.csv`, `spans_index.csv`,
`raw_spans.json`, `README.md`.

### Step 6 — Read and report back
Read the generated `summary.md`. Then report back concisely:
- the **question / task_label** and route,
- timing waterfall headline (total + the big stages),
- **total LLM calls + tokens + recomputed cost**,
- the `agent_runs.record` rollup status (note `cost_usd=NULL` if so),
- any TRACKING GAPS the script flagged.
Give the report folder path. Keep it tight — the detail lives in the files.

## Rules
- You only **query** Logfire and **write** report files under
  `agents_reports/agentic_monitor/`. Never modify pipeline/app code.
- Never invent numbers — everything comes from the spans. If the conversation has
  only HTTP spans (no `agent run` — e.g. the user only viewed it), say so plainly.
- If `query_run` errors with "No project specified", add `project="rihan"`.
- The timing-waterfall template in the script is tuned for **deep_search** turns;
  for a writing/memory-only turn some stages simply won't render — that's expected.
- HTTP `GET`/`OPTIONS` polling spans age out of Logfire retention within days; an
  older conversation may show slightly lower HTTP counts in the span inventory than
  a fresh one. This does not affect the agentic numbers (LLM calls, tokens, cost,
  stage timings) — note it if relevant, don't treat it as an error.
- Clean up any `_detached_<CONV>.json` scratch file when done (its rows are baked
  into `raw_spans.json`).
