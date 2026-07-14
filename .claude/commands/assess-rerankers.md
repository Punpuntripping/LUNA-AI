---
name: assess-rerankers
description: Grade all reranker runs of one conversation via @reranker-assessor and relay verdicts + prompt-fix proposals
user_invocable: true
allowed-tools: Read, Write, Bash, Glob, Grep, Agent
---

# /assess-rerankers — grade a conversation's reranker decisions

Given a **conversation_id**, you get every reranker LLM call of that
conversation judged (false-drops, false-keeps, scope-leaks, miscalibration)
and concrete prompt-fix proposals produced. The heavy lifting is done by the
**@reranker-assessor** agent (which fans out one @reranker-run-judge per
run); your job is to resolve the input, make sure the ground-truth dumps
exist, invoke the assessor correctly, and relay its findings faithfully.

## Argument: $ARGUMENTS

Parse `$ARGUMENTS` (trimmed):
- A UUID-shaped string (full or partial) → the conversation_id.
- `last` / `recent` / empty → the most recently modified
  `agents_reports/agentic_monitor/convo_*` folder; if none exists, ask the
  user for a conversation_id (you cannot guess one).
- `random N` (e.g. `random 5`) → pass the sampling mode through to the
  assessor; default is **all** runs.

## Hard facts (read once)

- Ground truth = the agentic_monitor dumps:
  `agents_reports/agentic_monitor/convo_<id>/llm_calls/*_reranker_*.md` —
  one file per reranker LLM call. Under the **keep-only** contract the dump
  output lists only the kept candidates; drops are *derived by difference*
  (input pool − keeps). Family is in the filename: `reg` / `case` /
  `compliance`.
- If the dumps are missing, they must be produced first from Logfire (the
  assessor invokes @logfire-monitor-agent itself). Two traps when that
  happens:
  - **Scrubbed conversation_id**: Logfire's scrubber can redact the UUID
    value, so `attributes->>'conversation_id'='<uuid>'` matches nothing —
    the spans must be joined by `trace_id` instead. If the monitor comes
    back empty-handed for a convo you know exists, pass this hint on.
  - **Freshness**: Logfire retention ages spans out — a convo older than
    ~14 days may be unextractable. Say so instead of retrying.
- Reports land in `agents_reports/reranker_assessments/convo_<id>/`:
  `_assessment.md` (run inventory, error tallies, patterns) and
  `_prompt_fixes.md` (verbatim clause → proposed replacement, per pattern).
- Known systemic issue for context (do not pre-judge with it, but recognize
  it): the reg reranker is blinded to the original question, so
  adjacent-scope regs (govt procurement, sector licensing, city bylaws)
  leak into private-contract answers — the "matter-frame" fix is designed
  but not built.

## Workflow

### Step 1 — Resolve the convo folder
Glob `agents_reports/agentic_monitor/convo_*<id>*` (or pick the newest
`convo_*` for `last`). Tell the user which conversation you resolved.

### Step 2 — Check the dumps
List `<folder>/llm_calls/*_reranker_*.md`. Report the run count and family
split (e.g. "9 runs: 6 reg, 2 case, 1 compliance").
- Dumps present → continue.
- Folder or dumps missing → warn the user that a Logfire extraction will
  run first (it is slow and query-heavy), then proceed — the assessor
  handles the extraction. Include the scrubbed-id hint from Hard facts in
  the assessor prompt when the convo id looks like a full UUID.
- Extraction impossible (no spans / aged out) → report that plainly, stop.

### Step 3 — Invoke the assessor
Spawn **@reranker-assessor** via the Agent tool. The prompt must contain:
the conversation_id AND the resolved folder path, the mode (`all` or
`random N` verbatim), and any hints from Step 2. Let it run to completion —
it fans out judges concurrently on its own; do not spawn judges yourself.

### Step 4 — Relay
Read `_assessment.md` and `_prompt_fixes.md`, then report back concisely:
- runs judged (and sampling coverage if `random N` — never present a sample
  as full coverage),
- headline error tallies by type and family,
- the 2–3 dominant patterns with one concrete example each,
- the single highest-value proposed prompt fix (quote the target clause),
- both report file paths.

## Rules
- You orchestrate and relay — the judging is the judges' job, the synthesis
  is the assessor's. Never re-score candidates yourself.
- Prompt fixes are **proposals** in `_prompt_fixes.md`. Never edit live
  prompt files or pipeline code from this skill, even if a fix looks
  obvious.
- Never invent verdicts or counts — everything you report must come from
  the assessor's reports.
- If the assessor returns nothing usable (no reranker activity in the
  convo), say exactly that; a convo with no deep_search turns legitimately
  has zero reranker runs.
