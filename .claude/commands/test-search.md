---
name: test-search
description: Test deep_search v2/v3/reg agents on random queries from test_queries.json
user_invocable: true
---

# /test-search — Test Deep Search Agents

You test deep_search_v2, deep_search_v3, and/or reg_search agents by running them against random queries from `agents/test_queries.json`.

## Argument: $ARGUMENTS

Parse the arguments as: `[version] [count] [--mock] [--seq|--batch N|--parallel] [reg_search options...]` or a utility command.

- **version**: `v2`, `v3`, `reg`, or `both` (default: `v2`). `both` = v2 + v3. `reg` = reg_search only.
- **count**: number of random queries to test (default: `5`, max: `30`)
- **--mock**: if present, pass `--mock` flag to v2 or reg CLI
- **--seq**: run queries one at a time (sequential)
- **--batch N**: run queries in batches of N concurrent processes (default mode, N=5)
- **--parallel**: run all queries concurrently (equivalent to `--batch {count}`)

### reg_search-specific options (only when version is `reg`):
- **--expand-only**: run expander + search only, skip aggregator
- **--aggregate-only LOG_ID**: re-run aggregator on search results from a previous expand-only log (e.g. `query_23/20260405_140000_expand`)
- **--expander-prompt KEY**: expander prompt variant (default: `prompt_1`)
- **--aggregator-prompt KEY**: aggregator prompt variant (default: `prompt_1`)
- **--rerank**: enable Jina reranker (off by default)
- **--score-threshold N**: minimum score to include a result (default: `0.005`). RRF scores range ~0.001-0.016.
- **--verbose**: print extra debug info
- **--query-id ID**: run a specific query by its ID from test_queries.json (instead of random)

### Utility commands (no tests run):
- **clear-logs [target]**: delete log files, keep folder structure (see below)
- **list-prompts**: show available expander and aggregator prompt keys
- **list-logs**: show recent reg_search log entries

If no execution mode is specified, default is `--batch 5`.

Examples:
- `/test-search v2 5` — test v2 on 5 random queries (batch of 5)
- `/test-search v3 10 --seq` — test v3 on 10 queries, one at a time
- `/test-search v2 10 --batch 3` — test v2 on 10 queries, 3 at a time
- `/test-search v3 15 --parallel` — test v3 on 15 queries, all at once
- `/test-search both 5` — test both v2 and v3 on same 5 queries
- `/test-search v2 5 --mock` — test v2 with mock results on 5 queries
- `/test-search reg 5` — test reg_search on 5 random queries (full loop)
- `/test-search reg 5 --expand-only` — test reg_search expander+search only
- `/test-search reg 3 --expander-prompt prompt_2 --score-threshold 0.2` — reg_search with custom prompt and threshold
- `/test-search reg 5 --rerank --score-threshold 0.1` — reg_search with Jina reranker
- `/test-search reg 5 --mock` — reg_search with mock results
- `/test-search reg --query-id 23 --expand-only` — reg_search on specific query #23
- `/test-search reg --aggregate-only query_23/20260405_140000_expand` — re-aggregate previous results
- `/test-search` — test v2 on 5 random queries (batch 5, all at once)
- `/test-search list-prompts` — show available prompt keys
- `/test-search list-logs` — show recent log entries
- `/test-search clear-logs` — clear all agent logs
- `/test-search clear-logs v2` — clear only v2 logs
- `/test-search clear-logs v3` — clear only v3 logs
- `/test-search clear-logs reg` — clear only reg_search logs
- `/test-search clear-logs old` — clear only legacy logs (logs/deep_search, logs/regulation_executor)

If `$ARGUMENTS` is empty, use defaults: v2, 5 queries, batch 5, no mock.

## Utility: list-prompts

If the first argument is `list-prompts`, run:
```bash
cd C:/Programming/LUNA_AI && PYTHONUTF8=1 python -m agents.deep_search_v3.reg_search.cli --list-prompts
```
Print the output and exit. No tests run.

## Utility: list-logs

If the first argument is `list-logs`, run:
```bash
cd C:/Programming/LUNA_AI && PYTHONUTF8=1 python -m agents.deep_search_v3.reg_search.cli --list-logs
```
Print the output and exit. No tests run.

## Clear Logs Mode

If the first argument is `clear-logs`, skip all test steps and instead clear log files.

### Target directories

| Target | Directories |
|--------|-------------|
| `v2` | `agents/deep_search_v2/logs/` |
| `v3` | `agents/deep_search_v3/logs/` |
| `reg` | `agents/deep_search_v3/reg_search/logs/` |
| `old` | `agents/logs/deep_search/`, `agents/logs/regulation_executor/` |
| *(empty/all)* | All of the above |

### What to delete
- All `.json` files in the target log directories (recursively)
- All `.md` files in subdirectories (recursively — includes nested `query_{id}/{timestamp}/` dirs for reg)
- For `reg`: delete all `query_*` subdirectories inside `agents/deep_search_v3/reg_search/logs/`
- For `v2`/`v3`: delete files in legacy subdirectories (`aggragator_md/`, `similiraty_search/`, `executor_md/`, `trace/`, `search_results/`)
- Also delete `agents/logs/tmp_queries/` contents if clearing `old` or all

### What to preserve
- The directories themselves (keep folder structure)
- `.gitkeep` files
- `.gitignore` files
- `comparison_report.md` files (from /compare command)

### Execution
1. Show what will be deleted (directory list + file count per directory).
2. Ask for confirmation before deleting.
3. Use `find ... -name "*.json" ! -name ".gitignore" -delete` or equivalent bash commands.
4. After deletion, print summary: `Cleared {N} files from {dirs}. Folder structure preserved.`

## Step 1: Load & Sample Queries

1. Read `agents/test_queries.json`.
2. From the `queries` array, randomly select `count` entries.
   - **For `reg` mode**: prefer entries that have a `text` field (exclude ids 24, 29 which only have `sub_queries`), since `--query-id` requires `text`. If the user explicitly requests a sub_query entry, fall back to temp-file approach.
3. For each selected query:
   - If it has a `text` field, use that as the query.
   - If it has `sub_queries` instead (e.g. ids 24, 29), concatenate all sub_query `.text` values with a newline.
4. Display the selected queries:

```
Selected {count} queries:
  #{id} [{category}] {first 60 chars}...
  #{id} [{category}] {first 60 chars}...
  ...
```

## Step 2: Run Tests

For each selected query, run the appropriate CLI from the project root (`C:\Programming\LUNA_AI`):

- **v2**: `python -m agents.deep_search_v2.cli [--mock] "{query}"`
- **v3**: `python -m agents.deep_search_v3.cli "{query}"`
- **reg**: `python -m agents.deep_search_v3.reg_search.cli --query-id {id} [--expand-only] [--expander-prompt KEY] [--aggregator-prompt KEY] [--rerank] [--score-threshold N] [--mock] [--verbose]`

**IMPORTANT for `reg`**: Always use `--query-id {id}` instead of passing raw query text. This avoids shell-escaping issues with Arabic text and enables the CLI's built-in query resolution and log organization (`logs/query_{id}/{timestamp}/`). Temp files are NOT needed for `reg`.

For `reg`, pass through all reg_search-specific flags that were parsed from the user's arguments.

### Special case: --aggregate-only

If `--aggregate-only LOG_ID` is specified, skip query selection entirely. Run a single command:
```bash
cd C:/Programming/LUNA_AI && PYTHONUTF8=1 python -m agents.deep_search_v3.reg_search.cli --aggregate-only LOG_ID [--aggregator-prompt KEY]
```
Then jump to Step 3 to collect results. This re-runs the aggregator on saved search results from a previous expand-only run.

### Execution modes:

**Sequential (`--seq`)**:
- Run queries one at a time.
- Before each run, print: `[{i}/{count}] Running {version} on query #{id}...`

**Batch (`--batch N`, default N=5)**:
- Split queries into batches of N.
- Within each batch, run all N queries concurrently as background processes.

For **v2/v3** (still need temp files for raw query text):
- Write each query to its own temp file: `agents/deep_search_{ver}/logs/tmp/q_{id}.txt`
- Launch using subshells for proper CWD inheritance on Windows:
  ```bash
  (cd C:/Programming/LUNA_AI && PYTHONUTF8=1 python -m agents.deep_search_{ver}.cli "$(cat agents/deep_search_{ver}/logs/tmp/q_{id}.txt)" > agents/deep_search_{ver}/logs/tmp/out_{id}.txt 2>&1) &
  # ... repeat for each query in batch ...
  wait
  ```

For **reg** (use --query-id, no temp files needed):
- Launch using subshells:
  ```bash
  (cd C:/Programming/LUNA_AI && PYTHONUTF8=1 python -m agents.deep_search_v3.reg_search.cli --query-id {id} [flags...] > agents/deep_search_v3/reg_search/logs/tmp/out_{id}.txt 2>&1) &
  # ... repeat for each query in batch ...
  wait
  ```
- Create `agents/deep_search_v3/reg_search/logs/tmp/` for output capture only.

- After each batch completes, read all output files and print a batch progress line:
  `Batch {b}/{total_batches} complete ({N} queries)`
- Then proceed to the next batch.

**Parallel (`--parallel`)**:
- Same as `--batch {count}` — all queries run concurrently in a single batch.

### Common rules (all modes):
- Set a **5-minute timeout** per query (300000ms).
- Capture both stdout and stderr to output files.
- If a run fails or times out, record the error and continue to remaining queries.
- **v2/v3**: The query text may contain newlines and special characters. Always write the query to a temp file and read it back via `$(cat ...)`.
- **reg**: Use `--query-id {id}` — no temp files needed for the query itself. Only use temp dir for stdout/stderr capture.
- **Windows CWD fix**: Always wrap background processes in subshells: `(cd C:/Programming/LUNA_AI && PYTHONUTF8=1 python -m ...) &`. Without subshells, background processes lose their CWD on Windows/Git Bash.
- Clean up the `tmp/` directory after the run completes.
- Agent logs are saved automatically by each CLI into their own log folder:
  - `agents/deep_search_v2/logs/`
  - `agents/deep_search_v3/logs/`
  - `agents/deep_search_v3/reg_search/logs/query_{id}/{timestamp}/` (new nested structure)

### If version is `both`:
- Run v2 first for all queries, then v3 for the same queries (same order, same execution mode).
- This makes comparison easier in the summary.
- `both` only covers v2 + v3. To include `reg`, run it separately.

## Step 3: Collect Results

After each run, extract from the output:
- **Status**: success or error (look for "Result type:" in output for v2/v3 success, or "Quality:" for reg full loop, or "Total:" for reg expand-only)
- **Duration**: parse from log if available, or note the wall-clock time
- **Result snippet**: first 200 chars of the Response/Summary section
- **SSE event count**: parse from "SSE Events (N):" line (v2/v3 only)
- **Log file path**: parse from "Full log:" or "Log dir:" line at the end

For `reg` version (full loop), also extract:
- **Quality**: parse from "Quality:" line (strong/moderate/weak/pending)
- **Rounds**: parse from "Rounds:" line
- **Citations**: parse from "Citations:" line count
- **Queries used**: parse from "Queries:" line count

For `reg` version (expand-only), extract:
- **Queries**: parse from "Queries (N):" line
- **Results**: parse from "Total: N queries, M results" line
- **Expander time**: parse from "Expander done in Ns" line
- **Search time**: parse from "Search done in Ns" line
- **Total duration**: parse from "Duration: expander Ns + search Ns = Ns" line
- **Token usage**: parse from "Expander usage: N in / N out / N req" line
- **RPC errors**: count lines containing "RPC failed"
- **Log dir**: parse from "Log dir:" line (format: `agents/deep_search_v3/reg_search/logs/query_{id}/{timestamp}_expand/`)

## Step 4: Summary Report

After all runs complete, print a summary table:

For **v2** or **v3**:
```markdown
## Test Results: deep_search_{version}

| # | Query ID | Category | Status | Duration | Events | Log |
|---|----------|----------|--------|----------|--------|-----|
| 1 | #{id} | {cat} | ok/err | {Ns} | {N} | {log_path} |
| 2 | #{id} | {cat} | ok/err | {Ns} | {N} | {log_path} |
...

**Summary**: {passed}/{total} passed, avg duration: {N}s
```

For **reg** (full loop):
```markdown
## Test Results: reg_search

| # | Query ID | Category | Status | Quality | Rounds | Citations | Duration | Log |
|---|----------|----------|--------|---------|--------|-----------|----------|-----|
| 1 | #{id} | {cat} | ok/err | strong | 1 | 5 | {Ns} | {log_path} |
...

**Summary**: {passed}/{total} passed, avg duration: {N}s, avg quality: {strong/moderate/weak}
```

For **reg** (expand-only):
```markdown
## Test Results: reg_search (expand-only)

| # | Query ID | Category | Status | Queries | Results | Expander | Search | Total | Tokens (in/out) | RPC Err | Log |
|---|----------|----------|--------|---------|---------|----------|--------|-------|-----------------|---------|-----|
| 1 | #{id} | {cat} | ok/err | 4 | 120 | 12s | 96s | 108s | 824/347 | 0 | {log_dir} |
...

**Summary**: {passed}/{total} passed, avg duration: {N}s, avg queries: {N}
```

### If version is `both`, show a comparison table:

```markdown
## Comparison: v2 vs v3

| Query ID | Category | v2 Status | v2 Duration | v3 Status | v3 Duration |
|----------|----------|-----------|-------------|-----------|-------------|
| #{id} | {cat} | ok/err | {Ns} | ok/err | {Ns} |
...
```

## Step 5: Save Report

Write the full report (including query texts, result snippets, and the summary table) to:
- **v2 only**: `agents/deep_search_v2/logs/test_report_{timestamp}.md`
- **v3 only**: `agents/deep_search_v3/logs/test_report_{timestamp}.md`
- **reg only**: `agents/deep_search_v3/reg_search/logs/test_report_{timestamp}.md`
- **both**: write to both v2 and v3 directories (same report)

where `{timestamp}` is `YYYYMMDD_HHMMSS` format.

## Rules

- Always run from project root `C:\Programming\LUNA_AI` so Python module imports work.
- **reg_search**: Always use `--query-id {id}` to pass queries. This avoids all shell-escaping issues with Arabic text and enables proper log organization under `logs/query_{id}/`.
- **v2/v3**: The query text is Arabic and may contain special characters, quotes, newlines. Write the query to a temp file and read it back via `$(cat ...)`.
- Do NOT modify test_queries.json or any agent code.
- For Windows compatibility, ensure UTF-8 output by setting `PYTHONUTF8=1` env var **inline in each subshell** (not just exported in parent shell, as background processes may not inherit it on Windows).
- **Windows CWD fix**: Always wrap background processes in subshells: `(cd C:/Programming/LUNA_AI && PYTHONUTF8=1 python -m ...) &`. Plain `cmd &` without subshells loses the CWD on Windows/Git Bash.
- If all runs fail with the same import/config error, stop early and report the root cause instead of repeating the same failure.
- **Sub-query entries (ids 24, 29)**: These have `sub_queries` instead of `text`. `--query-id` will NOT work for them (the CLI looks up `q["text"]` which doesn't exist). For these, concatenate the sub_query texts in Step 1 and pass as raw text via a temp file. When randomly selecting queries, **exclude ids 24 and 29** from `--query-id` mode for `reg`, or use the temp-file fallback for them.
