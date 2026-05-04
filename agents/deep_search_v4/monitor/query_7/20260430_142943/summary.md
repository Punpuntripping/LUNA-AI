# Summary -- query_id=7

- **status**: CRASHED
- **wall_time_s**: 459.73
- **category**: أحوال شخصية - رفض الرجعة

## Counts

- reg sub-queries (RQRs): 5
- compliance sub-queries (RQRs): 0
- case sub-queries (RQRs): 0
- URA high results: 22
- URA medium results: 9
- URA dropped: 0
- aggregator references: 0
- aggregator confidence: -
- aggregator model_used: -
- aggregator prompt_key: -

## Tokens (sum across phases)

- total_tokens_in: 90457
- total_tokens_out: 49609

## Per-phase log mirror status

- **reg**: OK -- mirrored from `C:\Programming\LUNA_AI\agents\deep_search_v3\reg_search\reports\query_7\20260430_142952`
- **compliance**: FAILED -- log dir was not captured (phase may have crashed before logger init) (rqr_table.md still written)
- **case**: FAILED -- log dir was not captured (phase may have crashed before logger init) (rqr_table.md still written)

## Error

```
KeyError: "Unknown aggregator prompt key: 'prompt_reg_only'. Available: ['prompt_1', 'prompt_2', 'prompt_3_critique', 'prompt_3_draft', 'prompt_3_rewrite', 'prompt_4']"
```