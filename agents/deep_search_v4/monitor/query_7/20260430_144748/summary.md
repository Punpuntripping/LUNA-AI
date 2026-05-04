# Summary -- query_id=7

- **status**: CRASHED
- **wall_time_s**: 421.33
- **category**: أحوال شخصية - رفض الرجعة

## Counts

- reg sub-queries (RQRs): 5
- compliance sub-queries (RQRs): 0
- case sub-queries (RQRs): 0
- URA high results: 17
- URA medium results: 13
- URA dropped: 0
- aggregator references: 0
- aggregator confidence: -
- aggregator model_used: -
- aggregator prompt_key: -

## Tokens (sum across phases)

- total_tokens_in: 87966
- total_tokens_out: 48481

## Per-phase log mirror status

- **reg**: OK -- mirrored from `C:\Programming\LUNA_AI\agents\deep_search_v3\reg_search\reports\query_7\20260430_144801`
- **compliance**: FAILED -- log dir was not captured (phase may have crashed before logger init) (rqr_table.md still written)
- **case**: FAILED -- log dir was not captured (phase may have crashed before logger init) (rqr_table.md still written)

## Error

```
ValidationError: 1 validation error for AggregatorOutput
validation
  Input should be a valid dictionary or instance of ValidationReport [type=model_type, input_value=ValidationReport(passed=T...nesty_ok=True, notes=[]), input_type=ValidationReport]
    For further information visit https://errors.pydantic.dev/2.12/v/model_type
```