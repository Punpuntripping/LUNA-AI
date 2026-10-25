# Reranker decider engine: d1-ultra → jev-1.14 → qwen

Status: **BUILT 2026-10-26, not committed, not deployed** (both flags default `llm`; migration 186
APPLIED to prod 2026-10-26 — see "Build notes" at the end). Two phases, one shared engine, a separate flag for each reranker:

| Phase | Reranker | Today | Decider rules | Answer A/B result |
|---|---|---|---|---|
| **A** | Regulations + compliance (`reg_compliance_search/reranker.py`) | qwen3.7-flash | 5-level score, keep ≥ 1.5, high ≥ 2.5 | **d1-ultra won 15–11** (4 ties, 30 turns) |
| **B** | Case law (`case_search/reranker.py`) | qwen3.5-flash | **lenient v2** (per-channel rubric, score × P(same mechanism), top-3 floor) | **draw 42–42** (4 ties, 88 turns, 2 sets) |

**Both phases are built together in one pass; the flags are switched on separately.** Phase A
goes live first, and phase B follows once A is stable. The owner chose the lenient v2 for case
law on 2026-10-25, and the strict v3 is parked (F1). Owner review on 2026-10-25 settled D11–D14. Evidence: memory
`project_reranker_decision_models`; data in `agents_reports/reranker_model_ab/` (`cases_30`
for A; `case_ab/` and `case_ab2/` for B); results pages
https://claude.ai/artifact/EnsTBKkTWodRvM29eTsB63 and https://claude.ai/artifact/B7Sye58HTzMWJbAi3ZA54E.

## Why

| Per deep_search turn | Regulations: qwen3.7-flash → d1-ultra | Case law: qwen3.5-flash → d1-ultra |
|---|---|---|
| Reranker cost | $0.0069 → $0.0034 | $0.021 (one heavy user; $0.0145 all users) → $0.0025–0.0036 |
| Reranker stage time (median) | ~73 s → ~1.5–2 s | ~59 s → ~2 s (0.6–0.9 s per sub-query) |
| Answer quality, blind judges | d1-ultra 15, qwen 11, 4 ties | d1-ultra 42, qwen 42, 4 ties |

jev-1.14 is the backup because d1-ultra has one provider (Liquid) and no failover. qwen stays
as the last resort, unchanged, for both rerankers.

## Decisions (owner, 2026-10-23 / 2026-10-25)

| # | Decision |
|---|---|
| D1 | Fallback runs **per sub-query**. One turn may mix engines. |
| D2 | **Failure** means an error response, a timeout, 429/402/5xx, a request too long for the model, or an answer with any candidate's score missing. Each decider gets one try plus one retry (8 s timeout each), then the next engine. A 402 (credits or in-flight budget) skips jev, because it bills the same account, and goes straight to qwen. 400/404/413 are not retried. |
| D3 | **"Not covered" signal (phase A):** one extra yes/no question in the same request. `sufficient = p ≥ 0.5`, with a fixed Arabic note when it's false. It must pass validation step A2 first, otherwise ship without it (always sufficient, no note, which is what the A/B ran). **Phase B ships without it** (the case A/B neutralised sufficiency in both arms). |
| D4 | **Zero keeps are accepted** as a valid answer. The decider path has no reconsider retry. |
| D5 | **Rollout:** switch straight to deciders behind a flag, and log the engine that served each sub-query. No shadow week. |
| D6 | **Dedicated OpenRouter key** `OPENROUTER_RERANKER_API_KEY` (exists on Railway backend since 2026-10-24, $50/week cap). If it's missing, the decider engine is skipped and qwen runs. |
| D7 | **Case law joins as phase B with the lenient v2 rules** (owner, 2026-10-25). The strict v3 is parked (F1). |
| D8 | **One request per sub-query** carrying all its candidates (batch), as tested. |
| D9 | **Phase A thresholds** (5-level score 0–4, the same for both deciders): keep if score ≥ 1.5, high if ≥ 2.5. Checked on the 30 test turns: d1-ultra keeps 617, jev 656, qwen 649; "high" 235 for d1-ultra and 236 for jev. |
| D10 | **Phase B rules** (per sub-query): rank = score × P(same mechanism). d1-ultra keeps if rank ≥ **1.7**, high if ≥ **2.23**. If fewer than 3 are kept, top up to 3 from the best-ranked with raw score ≥ 1.0. Ties go to the better search position; cap = the sub-query's `max_keep` (≤ 7). |
| D11 | **Case-law fallback is d1-ultra → jev-1.14 → qwen,** the same chain as regulations. jev's case thresholds: keep ≥ **0.75**, high ≥ **1.50**, calibrated by keep count on set 1 (920 vs d1's 923; 345 vs 352 high; 73% overlap). Not answer-tested yet; F5 checks it after go-live. |
| D12 | **The variable keep limit is accepted untested.** The case A/B used a flat 7; production's `max_keep` = min(7, ceil(result_budget / N)) can be 4–5, where the top-3 fill takes most of the room. |
| D13 | **No "not covered" notes for case law** (`sufficient = True`, empty `summary_note`), exactly as the A/B ran. |
| D14 | **Mis-citation is watched by a scheduled judge job, not a manual spot-check** (build step 8). It gets a qwen-era baseline before phase B goes live. |

## Flow per sub-query (both phases)

```
candidates + user message (built exactly as today, planner brief included)
  │
  ├─ flag {REG|CASE}_RERANKER_ENGINE != "decider", or model_override set ─► qwen path (today's code)
  │
  ├─ d1-ultra: estimated tokens > 28k? skip it ─► next decider (or qwen)
  │     ok (every candidate scored) ─► apply that reranker's rules ─► classification
  │     fail ─► jev-1.14   (402 ─► qwen directly)
  │                ok ─► rules (jev thresholds) ─► classification
  │                fail ─► qwen path (today's code, unchanged)
  ▼
the reranker's existing post-processing → RerankerQueryResult
```

The decider list comes from each reranker's own setting, so either chain can be changed by
editing one variable.

- **Phase A** builds a `RegRerankerClassification`: one `RegKeep(label, relevance,
  reasoning="")` per kept label, `sufficient` from D3, and `summary_note` empty or the fixed
  note. Label normalisation, dedup, cap, `_assemble_result` and `RerankerQueryResult` run
  unchanged.
- **Phase B** builds a `CaseRerankerClassification`: `keeps = [{position, relevance,
  reasoning: "", satisfies_axes: []}]` already capped and in rank order, `query_axes = []`,
  `sufficient = True` and `summary_note = ""`. `_build_from_classification`, the high-first cap
  and the result are unchanged. `loop.py` re-applies the cap to `decision_log`, but the decider
  path never emits more than `max_keep` keeps, so that step is a no-op. **No change to
  `loop.py`.**

URA, aggregator and frontend are untouched in both phases.

## Build

### 1. `shared/config.py`: settings

Follow the `RESPONDER_UNIFIED_FAMILIES` pattern: the env var name equals the field name, with
no `validation_alias`.

```python
# ── Reranker decider engine — .claude/plans/reranker_decider_engine.md ──
REG_RERANKER_ENGINE: str = "llm"     # "llm" | "decider". Default = today's behaviour.
CASE_RERANKER_ENGINE: str = "llm"    # phase B; flip only after phase A is stable
REG_RERANKER_DECIDERS: str = "liquid/d1-ultra,typesafe/jev-1.14"   # order = fallback order
CASE_RERANKER_DECIDERS: str = "liquid/d1-ultra,typesafe/jev-1.14"  # same chain (D11)
OPENROUTER_RERANKER_API_KEY: Optional[str] = None                   # dedicated key (D6)
```

🚨 `shared/config.py` already has uncommitted edits from another session. Diff the file
before `git add` and stage only these lines.

### 2. Shared module `agents/deep_search_v4/shared/decider.py`

The engine both rerankers call. It holds no reranker-specific wording.

- `async def decide(state: str, questions: dict, *, required: list[str], models: list[str]) -> DeciderReply | None`
  - Tries each model in `models` order (the caller passes `REG_` or `CASE_RERANKER_DECIDERS`)
    under the D2 rules.
  - `required` = the question keys that must all come back scored; anything missing is a failure.
  - Returns `None` when every decider fails, and the caller then runs qwen.
  - `DeciderReply` holds: `model` (the configured id, which picks the thresholds),
    `model_served` (e.g. `liquid/d1-ultra-20261015`), `answers` (raw), `usage_entry` and
    `fallback_from` (one short reason per failed model).
- **Token guard:** estimate tokens from characters using the ratio measured on the test
  requests. Above 28k, skip d1-ultra (32,768 limit) and go to jev (262k limit). The largest
  real requests are 12.1k (regulations) and about 15k (case law).
- One module-level `httpx.AsyncClient` (created lazily, timeout 8 s). Logfire already
  instruments httpx (`shared/observability.py:416`), so every call shows up as a span.
- Usage entry: `{"agent": "reranker", "model": "liquid/d1-ultra" | "typesafe/jev-1.14",
  "reranker_round": 1, "requests": n, "input_tokens": usage.input_tokens, "output_tokens": 0,
  "engine": "decider", "fallback_from": [...]}`.

### 3. Phase A request builder: `reg_compliance_search/decider_rules.py`

- `RUBRIC5` / `LEVELS5` copied verbatim from `scripts/reranker_decisions_ab.py:64-79` (the
  tested instructions). One `score` question per `[Cn]`, plus the D3 `__sufficient` noul.
- Rules: keep if score ≥ 1.5, high if ≥ 2.5, for both deciders (D9). Returns the
  `RegRerankerClassification`.

### 4. Phase B request builder: `case_search/decider_rules.py`

- Copy `RUBRIC_V2_HEAD`, `RUBRIC_V2_CHANNEL`, `LEVELS_V2` and `MECH_Q` verbatim from
  `scripts/reranker_case_decider_ab.py`, the tested v2 instructions. Per position `p`: `R{p}`
  is a score with the head plus the channel text plus "Rate ONLY the ruling whose header is
  `### [p]`", and `M{p}` is the "same mechanism?" noul. `required` = every `R{p}` and `M{p}`.
- State = `build_reranker_user_message(query, rationale, raw_markdown,
  planner_brief=planner_brief)` with **no `max_keep` line**, which is how it was tested. The
  channel comes from the rationale prefix `[principle|facts|basis]`.
- Rules (D10): pick thresholds by `reply.model` (d1-ultra's from D10, jev's from D11),
  rank = score × P(mech), keep / high / floor / cap, ties to the lower position. The logic is the same as `_arm_keeps("d1u_v2", …)` in
  `scripts/reranker_case_answer_ab.py`. Returns the `CaseRerankerClassification` above.

### 5. Call sites: one insertion point per reranker

- **Phase A, `reg_compliance_search/reranker.py`:** `run_reranker_for_query(..., engine=None)`.
  After `user_msg` is built (around line 395): if the engine is `"decider"`, `model_override is
  None` and the key is set → `decide(...)`. On success, set `classification`, extend
  `usage_entries`, and skip the LLM loop. Keep dicts carry `score`. `_cap_sort_key` becomes
  `(tier, -score, -rrf)`, with score 0 on the LLM path so qwen's ordering is unchanged.
- **Phase B, `case_search/reranker.py`:** `run_reranker_for_query(..., engine=None)`. After
  `user_msg` / `pos_to_block` are built (around line 315): the same gate with
  `CASE_RERANKER_ENGINE`. On success, run `_build_from_classification` on the decider
  classification and skip `_run_once` and the zero-keep nudge (D4). Usage entry
  `model` = the decider id.
- **Both:** create the qwen agent only when the LLM path actually runs. `round_trace` records
  `engine`, `model_served`, the scores, P(mech) and `fallback_from`, so the forensic dumps still
  say what happened.

### 6. Migration `shared/db/migrations/186_decider_reranker_pricing.sql` (185 was taken by app push tokens)

Insert `model_pricing` rows for `liquid/d1-ultra` and `typesafe/jev-1.14`: prompt $0.042/M,
completion $0, provider `openrouter`. Verified: 10,559 tokens × $0.042/M = $0.000443, which
equals the cost OpenRouter reported. `_emit_executor_ledger` (`orchestrator.py:269`) already
writes one `llm_calls` row per model under `deep_search.reranker.reg_compliance` and
`deep_search.reranker.case`. Apply the migration to production **before** flipping either
flag, or the rows get billed at $0.

### 7. Tests

`shared/tests/test_decider.py`, using `httpx.MockTransport`:
- d1 succeeds;
- d1 500 twice → jev;
- both fail → `None`;
- d1 402 → `None` without calling jev;
- a missing required key → failure;
- the token guard skips d1;
- no key → `None` with no HTTP calls.

Per phase:
- `reg_compliance_search/tests/test_decider_rules.py`: D9 rules; zero keeps accepted; flag
  `llm` → no HTTP calls; `model_override` → LLM path; the cap sort is unchanged on the LLM path.
- `case_search/tests/test_decider_rules.py`: D10 for both models; the floor at raw ≥ 1.0; the
  cap at `max_keep` (including 4 and 5, D12); ties broken by position; the channel parsed from
  the rationale; the classification shape accepted by `_build_from_classification`; d1-ultra
  failing → jev answering with jev's thresholds.

### 8. Scheduled citation audit (D14): `scripts/case_citation_audit.py`

- **What it does:** each week, sample 20 deep_search answers from the last 7 days that cited
  judgments. Use the stored workspace item and its `workspace_item_references`.
- **The check:** for every cited ruling, have a blind judge (Sonnet, the same rubric as
  `agents_reports/reranker_model_ab/judge_prompt_case_ab.md`, reduced to one answer) decide
  whether the cited ruling supports the sentence it's attached to, using the ruling's one-line
  subject.
- **Output:** the mis-citation rate (cited rulings that don't support their sentence ÷ all cited
  rulings) and the share of answers with at least one. Each run writes
  `agents_reports/case_citation_audit/<date>.md`, plus one summary row in its own log.
- **Scheduling:** a Claude Code scheduled routine (`/schedule`), so it needs no backend change.
  Mask names and case numbers in the report, the same way as the case A/B pages.
- **Baseline:** run it once on qwen-era answers before phase B goes live. Then alert the owner
  if a weekly decider-era rate is more than 1.5× the baseline, or the share of answers with a
  mis-citation goes above 25%.
- **Why a rate, not one sample:** the same qwen answers were flagged 12% and 23% by two judge
  groups. Compare runs against the baseline from the same job, never across different judging
  setups.

## Validate before each flag flip

**Phase A** (~$0.25):
1. **Parity:** run the new code path over `cases_30.jsonl` (247 sub-queries) and compare the
   keep sets with `decisions_raw_30.jsonl`. Target: ≥ 90% identical keeps per sub-query.
2. **"Not covered" question (D3):** compare `sufficient` with qwen's recorded flag. Ship only if
   agreement is ≥ 75% and its "not covered" rate is within 0.5–2× qwen's.
3. **Local smoke test:** one real deep_search turn with the flag on. Check the Logfire spans,
   that the `llm_calls` row has `model = liquid/d1-ultra` and a non-zero cost, and that the
   answer renders.

**Phase B** (~$0.30):
1. **Parity:** the new code path over `case_ab2/cases.jsonl` (348 sub-queries, briefs in
   `briefs.txt`) against `raw.jsonl` arm `liquid/d1-ultra@v2` plus `_arm_keeps("d1u_v2")`.
   Target: ≥ 90% identical keeps per sub-query.
2. **Citation-audit baseline (D14):** run step 8 once on qwen-era case-law answers and record
   the baseline rate.
3. **Local smoke test:** one case-law turn with `CASE_RERANKER_ENGINE=decider`. Check that
   every sub-query has its keeps, the `deep_search.reranker.case` ledger rows, and that the
   aggregator cites the judgments.

## Rollout

0. **Done:** `OPENROUTER_RERANKER_API_KEY` is on Railway backend. **Owner, before A:** top up
   OpenRouter credit (about $4 left on 2026-10-24) and replace the dead main
   `OPENROUTER_API_KEY` (401 since 2026-10-23).
1. ~~Apply migration 186 to production.~~ Done 2026-10-26.
2. Deploy with both flags unset. Nothing changes.
3. **Phase A:** set `REG_RERANKER_ENGINE=decider` on Railway (the variable edit triggers a
   redeploy). Monitor for 48 h.
4. **Phase B,** once A meets its success bar: run the B validation, then set
   `CASE_RERANKER_ENGINE=decider` and monitor for 48 h.
5. **Monitor,** then weekly:
   - `llm_calls` for `deep_search.reranker.reg_compliance` and `deep_search.reranker.case`, by
     model and day; the share of turns with a qwen row is the fallback rate;
   - Logfire: each reranker's stage duration and the decider fallback warnings;
   - the deep_search error rate, and turns where every sub-query came back empty;
   - **phase B:** the weekly citation audit (step 8) against its qwen-era baseline. Mis-cited
     judgments were this rule set's weak spot (see Risks).

**Success, per phase:** ≥ 95% of sub-queries served by a decider, a reranker stage median
under 10 s, reranker cost ≤ $0.004 a turn, and no rise in empty or errored turns.

**Rollback, per phase:** set that flag to `llm` (or delete it) on Railway. That goes back to qwen
only in one redeploy (~3–5 min). Nothing else needs undoing.

## Risks

- **Data location:** d1-ultra runs only in the US, and ZDR holds only through OpenRouter's
  agreement (Liquid's own policy allows training). That clashes with a future KSA-hosting move
  (memory `project_ksa_data_residency_migration`); at that point, flip to `llm` or self-host
  pplx-decider-v1.2.
- **Alpha API:** `/api/alpha/decisions` may change shape. Strict parsing makes any change a
  failure, which falls back to qwen, so a change costs speed, not answers.
- **Silent model updates:** `liquid/d1-ultra` is an alias (served as `-20261015`). Log the
  served version, and re-run the parity checks when it changes.
- **No per-keep reasoning:** `reasoning` is empty for decider keeps. The URA stores `""` and the
  aggregator prompt doesn't render it. The forensic dumps lose it; the scores are logged instead.
- **Phase B citation trust:**
  - In one judge pass on set 2, answers built from the lenient v2 keeps had unsupported
    citations more often than qwen's (25% vs 12%); on set 1 they were level (20% vs 20%).
  - The cause is the top-3 floor: on set 2 it supplied 39% of the keeps, 336 of them rated
    "low" by d1-ultra.
  - That flag is noisy between judge groups (the same qwen answers scored 12% and 23%), but the
    risk is real. That's why the weekly audit exists, and why v3 is the fix.
- **Phase B with a lower keep limit:** production's `max_keep` can be 4–5, which the A/B never
  tested (D12). The top-3 fill then takes most of the room, so weak rulings make up a larger
  share of what the aggregator receives. The audit is the guard.
- **jev-1.14 on case law is count-calibrated only (D11):** it overlaps with d1-ultra on 73% of
  keeps, but its answers were never judged. It serves only when d1-ultra fails, so its share
  should stay small. The audit and F5 cover it.
- **Phase B loses qwen's "not covered" notes,** since `sufficient` is always true. The
  aggregator's programmatic gaps for case sub-queries disappear. The A/B ran this way in both
  arms.
- **The case corpus is the bigger limit:** 72% of judgments are commercial-court rulings, 35 are
  labour rulings and 1 is a personal-status ruling. Family and labour questions get
  commercial or general-court judgments whichever reranker runs.
- **Test bias:** the gold data was drawn from production qwen keeps, so comparisons lean
  towards qwen.

## Follow-ups

- **F1, case law strict v3 (parked 2026-10-25):** no top-3 fill; keep if scaled score ≥ 2 and
  P(mech) ≥ 0.5; a court-fit factor from a per-sub-query `choice` question (decider `forum` step);
  a turn-level safety net.
  - It halved unsupported citations (12% vs 23% for qwen in the same pass) and raised accuracy,
    but lost on coverage: qwen won 51–34 with 3 ties.
  - Code: `--arm` / `d1u_v3` in `scripts/reranker_case_*_ab.py`.
  - Next ideas: a d1-ultra shortlist (top 10 of 15) with qwen deciding (keeps 92–94% of qwen's
    picks offline), or v3 with a medium-coverage floor.
- **Case A/B history:**
  - Round 1, v1 rubric, cut 2.0: qwen 25–15.
  - Round 2, v2: draw 19 / 18 / 3.
  - Set 2, v2 held-out, 21 users: draw 24 / 23 / 1.
  - Round 3, v3 strict: qwen 51–34.
  - Judge prompt: `agents_reports/reranker_model_ab/judge_prompt_case_ab.md`.
- **F2:** Logfire alert when either reranker's qwen fallback share goes above 20% over 6 h.
- **F3:** KSA residency path: self-hosted pplx-decider-v1.2 as a third decider.
- **F4:** add personal-status and labour judgments to the case corpus. It would help case-law
  answers more than any reranker change.
- **F5, jev-1.14 case check (after go-live, not a gate):**
  1. Run jev v2 over `case_ab2` and confirm the D11 thresholds land near d1-ultra's keep count
     there too.
  2. Run a blind answer A/B of jev v2 against d1-ultra v2 on one set (reuse d1-ultra's saved
     answers).
  3. If jev is clearly worse, remove it from `CASE_RERANKER_DECIDERS`, so a d1-ultra failure
     goes straight to qwen.
- **No changes to:** `mobile/`, the web frontend, the backend API, the aggregator, or `loop.py`.

## Build notes (2026-10-26)

| File | What |
|---|---|
| `agents/deep_search_v4/shared/decider.py` | `decide()` + token guard (`_CONTEXT_GUARD`: d1-ultra 3.3 chars/token, skip > 28k; jev 1.5, skip > 240k; measured on 1,127 / 779 saved requests) |
| `reg_compliance_search/decider_rules.py`, `reranker.py` | phase A; `ASK_SUFFICIENT = False` (D3 wording + Arabic note drafted, needs owner review and A2) |
| `case_search/decider_rules.py`, `reranker.py` | phase B; decider only when the rationale has a known `[channel]` prefix (legacy `RerankerNode` stays on qwen) |
| `shared/db/migrations/186_decider_reranker_pricing.sql` | idempotent, APPLIED to prod 2026-10-26 (both rows verified) |
| `scripts/case_citation_audit.py` | `sample` → Sonnet judges per batch file → `tally`; trial `sample --limit 3` ran, no judging yet |
| tests | `shared/tests/test_decider.py` (11), reg `test_decider_rules.py` (26), case `test_decider_rules.py` (42). All three are git-ignored paths → `git add -f` |

Offline parity (recorded answers replayed, no API calls): phase A 247/247 keeps + tiers, questions byte-identical
(prod state differs only by «نتيجة» vs «مقطعاً» in the header + a trailing newline); phase B 614/614 (both sets),
state + questions identical. Live parity A1/B1 not run: the local `.env` has no `OPENROUTER_RERANKER_API_KEY`.

Deviations from the text above:
- 401 also stops the chain (shared key); 403/422 go to the next model without a retry.
- Phase B thresholds 1.7 / 2.23 match the judged fixtures; the harness file's own defaults (1.2 / 2.4) are stale.
- The phase B floor REPLACES the keep list with the top 3 (raw ≥ 1.0), as `_arm_keeps` did, rather than topping
  up. Same result for d1; for jev it could drop a kept ruling with raw < 1.0 (0 cases on the recorded data).
- When every decider fails, `round_trace.fallback_from` only says "no answer"; per-model reasons are in the
  `decider fallback —` log warnings.
- The ledger writes `requests = 1` per row regardless of retries (same as the qwen path).
- **Citation audit:** the 25% "answers with ≥1 mis-citation" alert doesn't fit per-citation judging (answers cite
  ~16 rulings, so nearly every answer has one) → owner picks a new `--share-max`. < 20 qualifying answers/week.
  A cloud `/schedule` routine needs the Supabase secret key and has no `log.jsonl` (agents_reports is git-ignored),
  so pass `--baseline-rate` / `--baseline-share` to `tally`.
