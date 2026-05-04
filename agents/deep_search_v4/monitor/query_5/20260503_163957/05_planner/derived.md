# 05/derived -- caps + aggregator prompt

| executor | invoked | reranker_max_high | reranker_max_medium | expander_max_queries |
|----------|---------|-------------------|---------------------|----------------------|
| reg | True | 12 | 6 | 7 |
| compliance | True | 6 | 4 | 3 |
| cases | False | 6 | 4 | - |

- **aggregator_prompt_key**: `prompt_1`
- **sectors_override (forwarded to URA)**: `['العمل والتوظيف']`
