---
name: compare
description: Compare agent responses across models/runs from logs. Produces a side-by-side report.
user_invocable: true
---

# /compare — Compare Agent Responses

You compare results from agent log files to evaluate model performance, output quality, and cost differences.

## Argument: $ARGUMENTS

The argument is the agent folder name inside `agents/logs/`, e.g. `regulation_executor`, `deep_search`.

If `$ARGUMENTS` is empty, list available agents by checking subdirectories in `agents/logs/` and ask which one to compare.

## Step 1: Discover & Load Logs

1. List all `.json` files in `agents/logs/{agent_name}/`.
2. Read each file and parse JSON. For large files, read the first 200 lines (enough for metadata + agent_output summary).
3. **Filter**: Keep only logs where `"status": "success"`.
4. Extract from each log:
   - `log_id`
   - `model`
   - `timestamp`
   - `duration_seconds`
   - `input.query` (or `input.message` for deep_search)
   - `usage` (requests, input_tokens, output_tokens, total_tokens, tool_calls)
   - `agent_output.quality` (if present)
   - `agent_output.summary_md` (first 500 chars for preview)
   - `agent_output.citations` count
   - `error` (if any)

## Step 2: Group by Similar Query

Group successful logs by their query text (`input.query` or `input.message`).

- **Exact match first**: Same query string = same group.
- **Shared run ID**: Logs with the same timestamp prefix in `log_id` (e.g. `20260330_125035_211959_gemini` and `20260330_125035_211959_minimax` share `20260330_125035_211959`) are the same run with different models — always group these together.
- If a query appears in only one successful log (no comparison partner), put it in a "Solo Runs" section at the end.

## Step 3: Build Comparison Report

For each query group, produce a comparison section:

### Per-Group Format

```markdown
### Query: "{first 80 chars of query}..."

| Metric | {model_1} | {model_2} | ... |
|--------|-----------|-----------|-----|
| Log ID | {id} | {id} | |
| Status | success | success | |
| Duration (s) | {n} | {n} | |
| Requests | {n} | {n} | |
| Input Tokens | {n} | {n} | |
| Output Tokens | {n} | {n} | |
| Total Tokens | {n} | {n} | |
| Tool Calls | {n} | {n} | |
| Quality | {quality} | {quality} | |
| Citations | {count} | {count} | |
| Est. Cost ($) | {est} | {est} | |

**Output Comparison:**

**{model_1}** (first 300 chars of summary_md)
---
**{model_2}** (first 300 chars of summary_md)
```

### Cost Estimation

Use approximate token pricing:
- `gemini-3-flash` / `gemini-3.1-flash`: input $0.10/M, output $0.40/M
- `gemini-3.1-pro`: input $1.25/M, output $10.00/M
- `or-minimax-m2.7`: input $0.30/M, output $1.10/M (OpenRouter)
- Other models: show "N/A" and note the model name

Formula: `(input_tokens * input_rate + output_tokens * output_rate) / 1_000_000`

## Step 4: Summary Table

After all groups, add an aggregate summary:

```markdown
## Overall Summary

| Metric | {model_1} | {model_2} |
|--------|-----------|-----------|
| Total Runs | {n} | {n} |
| Avg Duration (s) | {n} | {n} |
| Avg Output Tokens | {n} | {n} |
| Avg Tool Calls | {n} | {n} |
| Quality Distribution | strong: X, moderate: Y, weak: Z | ... |
| Total Est. Cost ($) | {sum} | {sum} |
| Avg Est. Cost ($) | {avg} | {avg} |
```

## Step 5: Write Report

Write the report to `agents/logs/{agent_name}/comparison_report.md`.

Print a brief summary to the user with key findings:
- Which model was faster on average
- Which produced more detailed outputs (token count, citations)
- Which was cheaper
- Any notable quality differences

## Rules

- Read log files directly — do NOT run Python scripts
- Only compare `"status": "success"` logs
- If there are more than 20 query groups, show the 10 most interesting (biggest differences) and summarize the rest
- For very large summary_md fields, truncate to 300 chars in the comparison
- Always show the full metrics table even if some fields are missing (use "—" for missing)
- Do NOT modify any log files — this is a read-only analysis command
- Report file goes inside `agents/logs/{agent_name}/`, not in `agents_reports/`
