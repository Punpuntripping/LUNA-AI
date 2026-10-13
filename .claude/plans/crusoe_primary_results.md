# Crusoe primary — Day-3 test & results

Companion to `crusoe_primary_migration.md`. That plan says **what** we built; this one says
**how we judge it** after 3 days in production, and holds the results.

Status: **BUILT 2026-09-30, waiting for deploy.** LUNA: 5 commits (8baaeb4..a50232e), dashboard: 687a120, both on branch `crusoe-primary`
(worktrees `C:\Programming\LUNA_AI_crusoe`, `C:\Programming\marketing_crusoe`). Build
reports: `agents_reports/crusoe_build_2026-09-30_{luna,dashboard}.md`.

| Milestone | Date | Notes |
|---|---|---|
| Migration 169 applied | 2026-09-30 | provider columns + Crusoe price row |
| Deploy (Day 0) | _TBD_ | merge SHA: _ |
| Day-3 review | Day 0 + 3 | run §2–§4 below, fill §5 |

## 0. Gates before Day 0 (from migration plan §9)

- [ ] **P1** Crusoe direct-customer data terms (no storage / no training) confirmed + DPA
- [ ] **P6** Card on the Crusoe account (free credit was $4.77)
- [ ] **P2** Rate limits asked of Crusoe
- [x] Build reports reviewed, unit tests green, live checks passed (2026-09-30)
- [ ] `CRUSOE_API_KEY` on Railway `luna-backend` and `dashboard-api`

## 1. Baseline — 7 days before the switch (captured 2026-09-30, prod `llm_calls`)

| Metric | Baseline (Alibaba-first) |
|---|---|
| Deep searches / week | 86 |
| DeepSeek+rerank cost per deep search — p50 / avg | $0.0201 / $0.0218 (all LLM stages, excl. search fee) |
| Cached share of input, deep search | 9.3 % |
| LLM cost / day (all agents, excl. search fee + OCR) | $0.334 |
| `router.classify` duration p50 / p95 | 10.3 s / 45.2 s |
| `writer.plan` p50 / p95 | 38.2 s / 77.2 s |
| `writer.execute` p50 / p95 | 168.8 s / 234.1 s ⚠ |
| `artifact_summarizer.run` p50 / p95 | 10.0 s / 13.3 s |
| `convo_compactor.run` p50 / p95 | 25.9 s / 34.6 s |

⚠ `writer.execute` p95 (234 s, Alibaba, max) is close to the 300 s max-effort wall-clock
cap in §5.2 of the migration plan. When Crusoe fails over to Alibaba, a long writer run
could hit the cap. Watch `timeout_wallclock` failovers on `agent_writer` in particular.

Deep-search stage rows carry no `duration_ms` → deep-search latency comes from Logfire
(§3), not the ledger.

## 2. Ledger queries (Supabase `llm_calls`, Day 0 → Day 3)

Set `:d0` to the deploy timestamp. First backfill `provider` for rows written between
migration 169 and the deploy (the old code didn't stamp it):

```sql
UPDATE llm_calls SET provider = CASE
  WHEN model ILIKE 'mistral%' THEN 'mistral'
  WHEN model ILIKE 'deepseek/%' OR model ILIKE 'or-%' THEN 'openrouter'
  WHEN model ILIKE 'crusoe-%' THEN 'crusoe'
  WHEN model ILIKE 'gemini%' THEN 'google'
  WHEN model IS NULL OR model = 'none' THEN NULL
  ELSE 'alibaba' END
WHERE provider IS NULL AND created_at < :d0;
```

**Q1 — route share** (target: Crusoe ≥ 95 % of DeepSeek calls):
```sql
SELECT provider, model, count(*) n, round(sum(cost_usd)::numeric,4) cost
FROM llm_calls WHERE created_at >= :d0 AND agent NOT LIKE '%search_fee%'
GROUP BY 1,2 ORDER BY n DESC;
```

**Q2 — unstamped rows** (target: 0 LLM rows with `provider` NULL after Day 0):
```sql
SELECT agent, model, count(*) FROM llm_calls
WHERE created_at >= :d0 AND provider IS NULL AND agent NOT LIKE '%search_fee%'
GROUP BY 1,2;
```

**Q3 — cost per deep search, and cache share** (expect ~1.5× baseline; flag > 2×):
```sql
WITH ds AS (
  SELECT message_id, sum(cost_usd) c,
         sum(tokens_cached)::float / nullif(sum(tokens_in),0) cache_share
  FROM llm_calls WHERE created_at >= :d0 AND agent LIKE 'deep_search.%'
    AND agent NOT LIKE '%search_fee%' AND message_id IS NOT NULL
  GROUP BY message_id)
SELECT count(*), percentile_cont(0.5) WITHIN GROUP (ORDER BY c) p50, avg(c), avg(cache_share)
FROM ds;
```

**Q4 — latency per agent** (same shape as baseline §1):
```sql
SELECT agent, provider, count(*) n,
  percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms) p50,
  percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) p95
FROM llm_calls WHERE created_at >= :d0 AND duration_ms IS NOT NULL
GROUP BY 1,2 ORDER BY n DESC;
```

**Q5 — reasoning actually on** (target: every DeepSeek agent has reasoning > 0 on ≥ 98 % of
rows; aggregators/writer.execute ≫ others):
```sql
SELECT agent, provider, count(*) n,
  round(avg(tokens_reasoning)) avg_reasoning,
  count(*) FILTER (WHERE tokens_reasoning = 0) zero_reasoning
FROM llm_calls WHERE created_at >= :d0 AND model ILIKE '%deepseek%'
GROUP BY 1,2 ORDER BY n DESC;
```

**Q6 — daily cost and quota burn**:
```sql
SELECT date_trunc('day', created_at + interval '8 hours') day_sg, provider,
  round(sum(cost_usd)::numeric,3) cost_usd, round(sum(cost_usd)::numeric*100,1) points
FROM llm_calls WHERE created_at >= :d0 - interval '7 days'
GROUP BY 1,2 ORDER BY 1,2;
```
(User's day = UTC+8.) Also check Crusoe console spend vs `sum(cost_usd) WHERE provider='crusoe'`
— they should agree within ~10 % (the eval matched $0.23 vs $0.25).

## 3. Logfire queries (Day 0 → Day 3)

Event names fixed in the build (migration plan §5): `llm.route.failover`
(slot, route, next_route, reason, duration_ms), `llm.route.empty_thinking`
(slot, route, step, enforced), `llm.route.breaker_open` / `llm.route.breaker_close`.
Use the logfire MCP (`query_run`); remember the 30-min clamp on some queries — page by day.

- **L1 failover rate** = failover events ÷ DeepSeek LLM calls, grouped by `reason` and
  `route`. Target **< 2 %** overall; any `http_403` = a request-shape bug (thinking param),
  fix immediately.
- **L2 breaker** — count of `breaker_open` per day and total open time. Target: 0–1 per day.
- **L3 empty thinking** (log-only phase) — count by slot, first step only. Decides whether
  to set `LLM_EMPTY_THINKING_ENFORCE=true`: enforce only if first-step empties are real
  and < 1 % of calls.
- **L4 deep-search wall time** — the deep_search orchestrator span duration, p50/p95,
  Day 0–3 vs the 7 days before. Eval expectation: ~225 s vs ~253 s median.
- **L5 429s** — `http_429` failovers per hour; spikes → P2 (rate limits) is real.
- **L6 timeouts** — `timeout_idle` vs `timeout_wallclock` by slot; wall-clock on
  `agent_writer` = see the ⚠ in §1.

## 4. Quality re-run (3-arm A/B)

Same 10 questions (`agents_reports/crusoe_eval_2026-09-29/questions.json`) through the
**real code path** on the branch/prod code, via `scripts/ab_run.py` with the rebinding
removed, one run per question per arm:

| Arm | How | Why |
|---|---|---|
| A — Crusoe-first (prod) | `LLM_PRIMARY_PROVIDER=crusoe` | what shipped |
| B — Alibaba-first + new thinking policy | `LLM_PRIMARY_PROVIDER=alibaba` | separates "Crusoe is better" from "more thinking is better" — the 2026-09-29 A/B conflated them |
| C — 2026-09-29 Alibaba results | reuse `ab_out/qNN_alibaba.json` | old production baseline |

Blind-judge A vs B with the same 5 lenses (`build_packets.py`, `judge_results/`). Record per
lens and combined. ~$0.2 of LLM spend + judge runs.

Reading it: A ≫ B → Crusoe earns its place on quality. A ≈ B → the gain was the thinking
policy; Crusoe stays for speed (D1 stands) but cost/cache (P4) becomes the lever to push.

## 5. Results (fill on Day 3)

| # | Check | Target | Result | Pass? |
|---|---|---|---|---|
| Q1 | Crusoe share of DeepSeek calls | ≥ 95 % | | |
| Q2 | Unstamped LLM rows after Day 0 | 0 | | |
| Q3 | Cost per deep search (p50) | ≤ 2× $0.0201 | | |
| Q3 | Cache share | report (baseline 9.3 %) | | |
| Q4 | `router.classify` p95 | ≤ 1.5× 45.2 s | | |
| Q4 | `writer.execute` p50 | < 168.8 s | | |
| Q5 | DeepSeek rows with reasoning = 0 | ≤ 2 % | | |
| Q6 | LLM cost/day | report (baseline $0.334) | | |
| Q6 | Crusoe console vs ledger | within 10 % | | |
| L1 | Failover rate | < 2 %, zero 403 | | |
| L2 | Breaker opens / day | ≤ 1 | | |
| L3 | First-step empty thinking | report → enforce decision | | |
| L4 | Deep-search wall time p50 | < baseline | | |
| L5 | 429 bursts | none sustained | | |
| A/B | A vs B combined score | report | | |
| Dash | Dashboard ledger: provider=crusoe, no double-billed reasoning | yes | | |

### Decision
- **Keep** if Q1, Q2, Q5, L1 pass and nothing user-visible broke.
- **Tune** (no rollback) for Q3/Q4/L4 misses: per-slot policy changes (P8), cache work (P4).
- **Roll back** (`LLM_PRIMARY_PROVIDER=alibaba` on Railway → redeploy from master) on:
  sustained failover > 10 %, any 403 storm, a data-terms problem, or user-visible failures.

Decision taken: _ · by: _ · date: _

### Known from the build (2026-09-30) — check these on Day 3
- **Expander + sector_picker rows are labelled by slot, not by the served route** (they
  don't read the response model). A failover there is ledgered as Crusoe. Q1 route share
  is therefore slightly optimistic for those agents — cross-check with L1 failover events.
  One-line fix per loop file, deferred (main checkout has uncommitted edits there).
- **Every Alibaba cell now streams with a 45 s idle limit — including the Qwen reranker
  heads.** Watch L6 `timeout_idle` on `reg_compliance_reranker` / `case_search_reranker`.
- **OpenRouter reports reasoning > completion** (279 vs 149 in a live call) → OpenRouter
  rows are probably under-billed. Pre-existing; matters only when OR serves.
- At merge: update `simple_search/tests/test_synthesizer.py` (asserts `medium`, now `high`);
  `agents/tests/test_cost_ledger.py` imports removed `tier_of_subagent`.
- Build-time live numbers (1 deep search, q04): 226.5 s, 5/5 DeepSeek calls on Crusoe,
  aggregator 5,501 reasoning tokens, DeepSeek cost $0.0107; bad key → breaker opened after
  3; hung stub → failover in 46.7 s.

### Notes / surprises
_
