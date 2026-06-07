---
name: model-consumption
description: Per-model LLM token + cost consumption for a given day from the llm_calls ledger
user_invocable: true
allowed-tools: Bash, Read
---

# /model-consumption — what each model consumed on a day

Given a **day**, you produce a per-model breakdown of LLM consumption from the
`llm_calls` ledger: calls, input / output / reasoning / cached tokens, total
tokens, and cost — for every model used that day.

The script (`scripts/model_consumption.py`) owns all the Supabase reads, the
day-window math, and the cost recompute. You just run it and relay the table.

## Argument: $ARGUMENTS

Parse `$ARGUMENTS`:
- A `YYYY-MM-DD` token is the day. Pass it through verbatim.
- "today" / empty → omit the day (the script defaults to today, UTC).
- "yesterday" / "last <weekday>" / relative → resolve it to `YYYY-MM-DD`
  yourself (today's date is in context) and pass that.
- Optional flags the user may add, pass straight through:
  - `--tz N`   day boundary at UTC+N (e.g. `--tz 3` = Riyadh calendar day).
  - `--agent PREFIX`  only count rows whose `agent` starts with PREFIX
    (e.g. `--agent deep_search` for just the deep_search pipeline).

## Workflow

### Step 1 — run the extractor
```bash
cd C:/Programming/LUNA_AI && PYTHONUTF8=1 python scripts/model_consumption.py <DAY> [--tz N] [--agent PREFIX]
```
It prints the window, the per-model table (sorted by cost), a TOTAL row, and a
data-quality note when any rows have an unreliable model label.

### Step 2 — report back
Relay the per-model table and the TOTAL (tokens split in/out/reasoning + cost).
Lead with the day's total cost and total tokens, then the per-model rows. If the
data-quality note is present, surface it briefly — do not drop it.

## How the numbers are derived (so you describe them faithfully)
- **Tokens** are the raw `llm_calls` counts, grouped by the `model` column.
  `total = input + output + reasoning` (cached is a subset of input, shown
  separately, not re-added).
- **Cost** is the CORRECTED per-call figure: recompute each call from the
  `model_pricing` table via the project's `cost_usd()` (reasoning billed at the
  output rate, cached subset at the cached rate), but fall back to the stored
  `cost_usd` for rows whose `model` is not a real priced id (memory slot labels
  like `artifact_summarizer:tier_2`) or the legacy bare `deep_search` rollup.
  This is the same method as `/` cost_for_day.py and matches the ledger.

## Rules
- You only **run the script** (it reads Supabase with the backend service key).
  Never modify pipeline/app code.
- Never invent numbers — every figure comes from the script's output.
- Rows marked `(!)` carry a model label that is NOT a priced model id (a memory
  slot label). Their cost is correct (from stored `cost_usd`), but their tokens
  actually ran on a tier_2 flash model — say so rather than presenting the slot
  label as if it were a real model. The legacy bare `deep_search` rollup also
  stamps one model on a multi-model turn, so the qwen3.6-plus token bucket can be
  inflated on older days; the data-quality note quantifies the affected tokens.
- See `project_llm_calls_reprice_traps` (memory) for the full background on the
  two label traps.
