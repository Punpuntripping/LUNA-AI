# Monitor session -- query_id=7

- **status**: CRASHED
- **wall_time_s**: 459.73
- **category**: أحوال شخصية - رفض الرجعة
- **confidence**: -
- **references**: 0

## Pipeline flow

```
  reg_search ─┐
             │
  compliance ┼─── parallel ───→  URA merge  ───→  Aggregator  ───→  Output
             │     (gather)        (40_ura)       (50_*)            (summary)
  case_search┘
```

## Files (in pipeline order)

| Stage | File | What's in it |
|-------|------|--------------|
| 00 | [00_query.md](00_query.md) | Query text + executor flags + per-phase log_ids |
| 10 | [10_reg_search/](10_reg_search/) | Reg phase mirror -- expander + search + reranker. *OK -- mirrored from `C:\Programming\LUNA_AI\agents\deep_search_v3\reg_search\reports\query_7\20260430_142952`* |
| 20 | [20_compliance_search/](20_compliance_search/) | Compliance phase mirror. *FAILED -- log dir was not captured (phase may have crashed before logger init) (rqr_table.md still written)* |
| 30 | [30_case_search/](30_case_search/) | Case phase mirror. *FAILED -- log dir was not captured (phase may have crashed before logger init) (rqr_table.md still written)* |
| 40 | [40_ura.md](40_ura.md) | Merged UnifiedRetrievalArtifact (high + medium tiers) |
| 50 | [50_aggregator/](50_aggregator/) | AggregatorInput, prompt(s), raw LLM, thinking, synthesis, validation |
| 60 | [60_runtime/](60_runtime/) | SSE events, per-executor stats |
| -- | [summary.md](summary.md) | Final tally, tokens, validation report |

## What lives in each phase mirror

Every per-domain mirror is a **verbatim copy** of the report dir written
by that phase, so opening it is the same as opening the source. Useful
files inside each:

- `run.md` -- human overview (focus, queries, timeline, file index)
- `run.json` -- machine-readable everything (events, inner_usage, step_timings)
- `expander*/round_N.md` -- expander LLM I/O per round, with token usage
- `expander*/reasoning_round_N.md` -- expander internal rationale per sub-query
- `search/round_N_qX_*.md` -- raw DB hit list per sub-query (RRF positions)
- `reranker/round_N_qX_*.md` -- reranker LLM input + classification output
- `reranker/summary.json` -- aggregated reranker decisions
- `flow.md` -- **monitor-only**: expander queries → search hit counts → reranker kept/dropped per sub-query in one file
- `rqr_table.md` -- monitor-only table of `RerankerQueryResult` objects
  exactly as they entered the URA merger (post-adapter)

## What lives in 50_aggregator/

Captured by `AggregatorLogger` which the monitor injects into the run:

- `input.md` -- structured AggregatorInput (URA + sub_queries) before prompt render
- `prompt_single.md` (or `prompt_draft.md` / `prompt_critique.md` / `prompt_rewrite.md`
  for DCR / `prompt_fallback_single.md` if the primary failed) -- exact system prompt
  + user message sent to the LLM, byte-for-byte
- `llm_raw_*.txt` -- raw model completion per stage, before parsing
- `thinking.md` -- stripped `<thinking>` block
- `synthesis.md` -- final synthesis + reference block
- `references.json` -- structured Reference objects
- `validation.json` -- post-validate report (dangling cites, unused refs, coverage)
- `run.md` -- per-aggregator-run summary
- `output.md` -- pretty-printed AggregatorOutput

## Crash

See [CRASH.md](CRASH.md). Error: `KeyError: "Unknown aggregator prompt key: 'prompt_reg_only'. Available: ['prompt_1', 'prompt_2', 'prompt_3_critique', 'prompt_3_draft', 'prompt_3_rewrite', 'prompt_4']"`
