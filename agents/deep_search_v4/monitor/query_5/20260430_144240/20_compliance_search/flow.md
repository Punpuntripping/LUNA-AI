# compliance_search — Expander → Search → Reranker Flow

- **status**: success
- **duration**: 67.0s
- **outer rounds**: 1

## Round 1

### Expander → 1 queries  (tokens: 2,595)

1. خدمة لإشعار عامل منتهية مدة عقده بعدم الرغبة في التجديد يستفيد منها صاحب العمل بهدف إنهاء العلاقة التعاقدية نظاميًا قبل الانتهاء بشهر.

### Search → 20 unique services (1 queries run)

| q# | count | query |
|----|-------|-------|
| 1 | 20 | خدمة لإشعار عامل منتهية مدة عقده بعدم الرغبة في التجديد يستفيد منها صاحب العمل بهدف إنهاء العلاقة ال… |

### Reranker → kept=4, sufficient=True, weak_axes=0

## Reranker Results (RQR) — 1 sub-queries

| # | query | sufficient | kept | dropped | note |
|---|-------|------------|------|---------|------|
| 1 | خدمة لإشعار عامل منتهية مدة عقده بعدم الرغبة في التجديد يستفيد منها صاحب العمل ب… | True | 4 | 16 |  |

## Token Cost

| input | output | total |
|-------|--------|-------|
| 7,607 | 3,398 | 11,005 |
