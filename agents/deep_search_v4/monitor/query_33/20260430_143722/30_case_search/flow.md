# case_search — Expander → Search → Reranker Flow

- **status**: success
- **duration**: 108.6s
- **outer rounds**: 1

## Round 1

### Expander → 1 queries  (tokens: 0)

1. سوابق قضائية في الفصل التعسفي

### Search → 30 unique services (1 queries run)

| q# | count | query |
|----|-------|-------|
| 1 | 30 | سوابق قضائية في الفصل التعسفي |

### Reranker → kept=0, sufficient=[False], weak_axes=0

## Reranker Results (RQR) — 1 sub-queries

| # | query | sufficient | kept | dropped | note |
|---|-------|------------|------|---------|------|
| 1 | سوابق قضائية في الفصل التعسفي | False | 0 | 15 | جميع النتائج المستردة تتعلق بدفع إجرائي هو "سبق الفصل في الدعوى" (قضية محكوم فيه… |

## Token Cost

| input | output | total |
|-------|--------|-------|
| 8,624 | 2,573 | 11,197 |
