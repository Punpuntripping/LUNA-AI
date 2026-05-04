# Deep Search V4 — Planner Agent + Pipeline Design

Status: cut-1.5 implemented (planner schema slimmed; phases 1–6 done; reset sweep done)
Scope: insert a Planner agent in front of the parallel executors, drive executor selection + focus, route invoke-aware aggregator prompts, decide on directory shape and multi-URA / multi-aggregator structure.

> **Cut-1.5 revision** (current): the planner LLM emits only `invoke` + `focus` + `sectors` + `rationale`. Concrete numeric caps and the aggregator prompt key are derived in code (`apply.FOCUS_PROFILES`, `apply.INVOKE_TO_AGG_PROMPT`). The original cut-1 schema with `mode` / `aggregator_prompt_key` / nested caps / RRF thresholds was **rejected** — too much tuning surface in the prompt, and the LLM had no business picking numbers it couldn't measure. See §3, §4.1, §4.4 for the live shape; §A.1 (appendix) for the discarded version.

---

## 1. Goals

1. **One front-of-pipeline LLM call** that decides only what the model can usefully judge from the query text:
   - Which executors to invoke (`reg` / `compliance` / `cases`).
   - Per-invoked-executor **focus level** (`high` / `default` / `low`) — the LLM picks the level; numeric caps live in a code-side table.
   - Sectors (legal-domain pre-filter) — moved up from reg's expander.
2. **Programmatic derivation** for everything else:
   - Aggregator prompt key derived from the invoke set (table in `apply.INVOKE_TO_AGG_PROMPT`).
   - Expander + reranker caps derived from `(executor, focus)` (table in `apply.FOCUS_PROFILES`).
   - RRF / score thresholds remain caller-set fields on `FullLoopDeps` — not planner-driven.
3. **Invoke-aware aggregator behaviour** — different prompt per invoke combination; multiple aggregators when the combination demands domain separation.
4. **Stay close to the current 5-step LLM cost** (planner = 1 cheap call; subsume reg's sector-detection so net additional calls ≈ 0–1 on a fast model).
5. **No architectural lift for user clarification** in v4-cut-1 — design hooks for it, defer the UX path.

## 2. Non-goals

- No directory reorganization (see §8 — coupling audit shows ~0.2% reuse; rejected).
- No mid-flow user Q&A in cut-1 (deferred; surface designed but not wired).
- No replacement of the executor-internal expander/reranker chains. The planner picks knobs; the executors remain self-contained pydantic_graphs.
- **No LLM-set numeric caps** (rejected in cut-1.5). The planner does not pick `max_queries`, `max_high`, `max_medium`, or thresholds.

---

## 3. Invoke set → executor flags + aggregator prompt

The planner picks `invoke` (a subset of `{"reg", "compliance", "cases"}`, ≥1 entry). `apply_plan_to_deps` flips the existing `include_*` booleans on `FullLoopDeps` accordingly, and `derive_aggregator_prompt_key(plan)` looks up the prompt key from a static table:

| `invoke` (set)                              | include_reg | include_compliance | include_cases | aggregator key       |
|---------------------------------------------|:-----------:|:------------------:|:-------------:|----------------------|
| `{reg}`                                     | ✓           | ✗                  | ✗             | `prompt_reg_only`    |
| `{compliance}`                              | ✗           | ✓                  | ✗             | `prompt_comp_only`   |
| `{cases}`                                   | ✗           | ✗                  | ✓             | `prompt_cases_only`  |
| `{compliance, cases}`                       | ✗           | ✓                  | ✓             | `prompt_cases_focus` |
| `{reg, compliance}`                         | ✓           | ✓                  | ✗             | `prompt_1` (CRAC)    |
| `{reg, cases}`                              | ✓           | ✗                  | ✓             | `prompt_1` (CRAC)    |
| `{reg, compliance, cases}`                  | ✓           | ✓                  | ✓             | `prompt_1` (CRAC)    |

`include_reg` is **net-new** (pre-cut-1 reg was mandatory because it returned sectors used by the URA merger). See §5 for how we decouple it.

> The original cut-1 design exposed a `mode: Literal["reg", "reg+comp", "all", "cases+comp", "cases", "comp"]` field that the LLM picked. Cut-1.5 collapses the same six (plus `{reg, cases}`) to membership of `invoke` — purely a code-side derivation, no LLM choice. See §A.1 for why.

---

## 4. Planner agent — shape, model, integration

### 4.1 Output schema

```python
Executor   = Literal["reg", "compliance", "cases"]
FocusLevel = Literal["high", "default", "low"]

class PlannerOutput(BaseModel):
    invoke: list[Executor]                   # ≥1 entry; duplicates rejected
    focus:  dict[Executor, FocusLevel]       # one key per `invoke` entry
    sectors: list[str] | None = None         # 1–4 canonical sector names, or null
    rationale: str                           # short Arabic justification, logged

    # Cross-field validators: `focus` must include every executor in `invoke`;
    # `sectors` size 1–4. No `mode`, no caps, no thresholds, no prompt key,
    # no detail_level — all derived in `apply.py`.
```

The four-field shape is deliberate: the LLM commits only to choices it can make from the query text — *which* corpora are relevant, *how hard* to lean on each, and *what legal domain*. Numeric caps and the aggregator prompt are derived in code so we can re-tune them without retraining the prompt.

### 4.2 Model + cost

- Default: **`qwen3-flash`** (or `gemini-3-flash` / `haiku-4.5`) — single LLM call, ~0.5–1.5 s wall, low token cost.
- Override: `LUNA_PLANNER_MODEL` env var.
- The planner sees only the original query + a short "tools available" block. No corpus access, no embeddings.

### 4.3 Integration point

`orchestrator.py:495`, immediately before `asyncio.gather(...)`:

```python
# Existing
logger.info("orchestrator: launching reg + compliance + case in parallel")

# NEW
plan = await run_planner(query=query, deps=planner_deps)
deps = apply_plan_to_deps(deps, plan)        # pure function — see §4.4
emit(deps, {"event": "plan_ready", "plan": plan.model_dump()})

if plan.needs_clarification and deps.ask_user is not None:   # cut-2 hook
    ...

# Existing
results = await asyncio.gather(
    _run_reg_phase(query, query_id, deps) if deps.include_reg else _empty_reg(),
    _run_compliance_phase(query, query_id, deps),
    _run_case_phase(query, query_id, deps),
)
```

### 4.4 Plan-application (pure function)

`apply_plan_to_deps(deps, plan)` is a small in-place mutation:

- `include_reg`, `include_compliance`, `include_cases` ← membership of `plan.invoke`.
- For each executor in `plan.invoke`, look up `FOCUS_PROFILES[executor][plan.focus[executor]]` and write `*_max_high` / `*_max_medium` (reranker) onto `deps`. Disabled executors keep their default caps untouched.
- `deps.expander_max_queries: dict[str, int]` ← per-invoked-executor `expander_max_queries` from the same profile lookup. Disabled executors are absent from the dict; loops read with `.get(..., None)`.
- `deps.sectors_override: list[str] | None` ← `plan.sectors`.
- **Not touched** by apply: `detail_level` (caller-set), `reg_rrf_min_score` and `case_score_threshold` (caller-set; planner doesn't pick them), executor `expander_prompt_key`s.

Aggregator prompt key is **not** stored on deps. The orchestrator calls `derive_aggregator_prompt_key(plan)` after apply to compute the key.

`FOCUS_PROFILES` lives in `apply.py` and is the only place numeric caps are written down. `default` rows mirror the existing `FullLoopDeps` defaults exactly so a plan that picks `default` everywhere is byte-identical to a planner-disabled run.

```python
FOCUS_PROFILES = {
    "reg": {
        "high":    {"expander_max_queries": 7, "reranker_max_high": 12, "reranker_max_medium": 6},
        "default": {"expander_max_queries": 5, "reranker_max_high":  8, "reranker_max_medium": 4},
        "low":     {"expander_max_queries": 3, "reranker_max_high":  5, "reranker_max_medium": 2},
    },
    "compliance": {
        "high":    {"expander_max_queries": 5, "reranker_max_high": 10, "reranker_max_medium": 5},
        "default": {"expander_max_queries": 3, "reranker_max_high":  6, "reranker_max_medium": 4},
        "low":     {"expander_max_queries": 2, "reranker_max_high":  4, "reranker_max_medium": 2},
    },
    "cases": {
        "high":    {"expander_max_queries": 4, "reranker_max_high": 10, "reranker_max_medium": 6},
        "default": {"expander_max_queries": 2, "reranker_max_high":  6, "reranker_max_medium": 4},
        "low":     {"expander_max_queries": 1, "reranker_max_high":  4, "reranker_max_medium": 2},
    },
}
```

---

## 5. Decoupling reg from the URA sector-pipeline

**Today**: `_run_reg_phase` returns `sectors`; `build_ura_from_phases` consumes them. Setting `include_reg=False` breaks the URA build.

**Change**: the planner emits `sectors`. The orchestrator passes `plan.sectors` directly to `build_ura_from_phases`. Reg, when it does run, still emits its own sectors (used to log/compare against the planner's pick), but the URA never depends on reg's output for sectors.

**Side benefit**: reg's expander prompt can drop its sector-classification block. Saves thinking tokens, removes a duplicate concern.

---

## 6. Invoke set → aggregator prompt mapping

Implemented (cut-1.5): each invoke combination maps deterministically to a registered aggregator prompt key. The planner does **not** choose the key — `derive_aggregator_prompt_key(plan)` looks it up in `apply.INVOKE_TO_AGG_PROMPT`. The four mode-specialized prompts (`prompt_reg_only`, `prompt_cases_only`, `prompt_comp_only`, `prompt_cases_focus`) live alongside the original `prompt_1..prompt_4` and are validated by `aggregator/postvalidator.py:check_structure`.

| `invoke` (set)                   | Aggregator prompt | Notes |
|----------------------------------|-------------------|-------|
| `{reg}`                          | `prompt_reg_only`    | IRAC-leaning, regulatory framing |
| `{compliance}`                   | `prompt_comp_only`   | procedural / executable steps |
| `{cases}`                        | `prompt_cases_only`  | pure jurisprudence framing |
| `{compliance, cases}`            | `prompt_cases_focus` | case-led narrative, services as practical paths |
| `{reg, compliance}`              | `prompt_1` (CRAC)    | multi-source default |
| `{reg, cases}`                   | `prompt_1` (CRAC)    | multi-source default (rare combo) |
| `{reg, compliance, cases}`       | `prompt_1` (CRAC)    | full triangulation default |

> Considered and rejected: a per-domain multi-aggregator + meta-aggregator pattern (3 extra LLM calls + 1 meta on full triangulation, +6–10 s wall on Flash). Defer to cut-2 only if cross-domain coherence on cut-1.5 measurably fails. Single-aggregator with invoke-aware framing keeps latency bounded.

---

## 7. Implementation work — phased

All six phases are implemented as of cut-1.5. The phase status reflects the slimmed schema (no `mode`, no LLM-set caps, no LLM-set aggregator key).

### Phase 1 — planner skeleton ✅

1. New module `agents/deep_search_v4/planner/` with:
   - `models.py` — `PlannerOutput` (4 fields), `PlannerDeps` (3 fields).
   - `prompts.py` — Arabic system prompt: invoke rubric + focus rubric + sector vocab + 3 authentic example queries from `agents/test_queries.json`. No numeric caps in the prompt.
   - `agent.py` — Pydantic AI factory; default model `qwen3-flash` (aliased to Alibaba `qwen3.5-flash` in `agents/model_registry.py`).
   - `runner.py` — `async run_planner(query, deps) -> PlannerOutput`; degraded fallback returns `invoke=["reg","compliance","cases"]` + all-`default` focus on any LLM error.
   - `apply.py` — `apply_plan_to_deps(deps, plan)` + `derive_aggregator_prompt_key(plan)` + `FOCUS_PROFILES` + `INVOKE_TO_AGG_PROMPT`.
2. New fields on `FullLoopDeps`:
   - `include_reg: bool = True` ✓
   - `enable_planner: bool = False` ✓ (default OFF; flip to engage)
   - `planner_model: str | None = None` ✓
   - `expander_max_queries: dict[str, int] | None = None` ✓
   - `sectors_override: list[str] | None = None` ✓
   - `reg_rrf_min_score: float | None = None`, `case_score_threshold: float | None = None` ✓ (caller-set, not planner-driven post-cut-1.5)
   - `ask_user: Callable[[str], Awaitable[str]] | None = None` ✓ (cut-2 hook)
   - `_plan: PlannerOutput | None = None` ✓ (telemetry)
3. Planner call wired in `run_full_loop` behind `enable_planner` (default off).

### Phase 2 — wire plan into deps + orchestrator routing ✅

1. `apply_plan_to_deps` flips `include_*` + writes per-executor caps from `FOCUS_PROFILES`.
2. Orchestrator gates `_run_reg_phase` / `_run_compliance_phase` / `_run_case_phase` on their `include_*` flags. Each writes a zeroed `_per_executor_stats` entry when skipped so monitor logs stay consistent.
3. `build_ura_from_phases` receives `deps.sectors_override` (preferred) or reg's expander-emitted sectors (fallback). Mismatches are logged (`"planner sectors %s differ from reg sectors %s"`).
4. Aggregator prompt key picked via `derive_aggregator_prompt_key(plan)` after apply — overrides the caller-supplied `prompt_key` argument when planner ran successfully.

### Phase 3 — expander cap injection ✅

Static "1-4" / "1-5" / "2-10" guidance left intact in the prompts (it's now a *hint*, not a hard rule — Python comment added above each block). Runtime authoritative cap injected via dynamic instructions:

1. Arabic injection: `"اقتصر على عدد {N} من الاستعلامات الفرعية، ولا تتجاوز هذا الحد."`
2. `reg_search.prompts.build_expander_dynamic_instructions(weak_axes, round_count, *, max_queries=None)` — extended.
3. `compliance_search.prompts.build_expander_dynamic_instructions(weak_axes, *, max_queries=None)` — extended.
4. `case_search.prompts.build_expander_dynamic_instructions(*, max_queries=None)` — created (case_search had none).
5. `expander_max_queries: int | None` field added to `RegLoopState` / `ComplianceLoopState` / `CaseLoopState`. Orchestrator threads `deps.expander_max_queries.get(executor)` → state → builder.

### Phase 4 — invoke-keyed aggregator prompts ✅

1. Added `prompt_reg_only`, `prompt_cases_only`, `prompt_comp_only`, `prompt_cases_focus` to `aggregator/prompts.AGGREGATOR_PROMPTS`. Thin variants of CRAC/IRAC with absent-domain sections trimmed.
2. `derive_aggregator_prompt_key(plan)` picks the key from `INVOKE_TO_AGG_PROMPT`.
3. `aggregator/postvalidator.py:check_structure` validates required Arabic headings for each new key. 17 dedicated structure tests in `aggregator/tests/test_postvalidator_mode_prompts.py`.

### Phase 5 — sector decoupling ✅ (with one cut-2 carry-forward)

1. URA merger now prefers `deps.sectors_override` over reg's expander-emitted sectors (Phase 2 step 3).
2. Runtime suppression of reg's sector-classification block when `state.sectors_override` is set: dynamic instructions inject `"الصكاترات (المجالات القانونية) محددة مسبقاً من المُخطِّط: ... لا تحتاج لاختيار صكاترات جديدة."` and `SearchNode` bypasses `canonicalize_sectors()`.
3. **Carry-forward**: the *static* sector-classification block in `reg_search/prompts.py` (`prompt_2`) and `case_search/prompts.py` (`prompt_3`) is **not** physically deleted. We only suppress at runtime. Cut-2 work item: delete the static block and rely on `sectors_override` only.

### Phase 6 — clarification hook (designed-in, partially wired) ✅

1. `ask_user` field exists on `FullLoopDeps`.
2. Cut-1.5 dropped `needs_clarification` / `clarification_question` from `PlannerOutput` — the planner is forbidden from asking. The orchestrator's clarification path was removed. The deps field stays for cut-2.
3. **Carry-forward**: cut-2 will re-introduce a separate clarification step (likely a second tiny LLM call, not a field on the planner output) and wire CLI `ask_user = input` + HTTP duplex channel.

### Phase R — executor reset sweep ✅ (added post-cut-1)

After Phase 1–6 landed, an additional sweep removed redundancy between the planner and each executor:

| Reset | Reg | Compliance | Case |
|---|---|---|---|
| **R1** sectors_override skips internal classification | ✅ runtime suppression | n/a | ✅ runtime suppression in `prompt_3` |
| **R2** Reranker caps from `LoopState`, not function defaults | ✅ already correct (no-op) | ✅ moved deps→state (state authoritative, deps fallback) | ✅ `reranker_max_high/medium` added to `LoopState` |
| **R3** Threshold drop-floor in reranker | ✅ `min_score` kwarg drops `block.rrf < threshold` | n/a | ✅ `min_score` kwarg drops `r.score < threshold` |
| **R4** Static-prompt cap docstring | ✅ | ✅ | ✅ |

---

## 8. Directory reorganization — rejected

Considered: collapse `reg_search/`, `compliance_search/`, `case_search/` into `expander/`, `search/`, `reranker/`, `aggregator/` per pipeline stage.

**Audit findings** (per coupling explorer):

- Reuse ratio across the three executors' stage code: **~0.2%** (3 helper functions in `shared/`).
- Each `loop.py` imports 7–10 sibling modules from its own folder. Splitting them across 4 function folders means a single executor change touches **6 folders instead of 1**.
- Reranker file sizes: reg=888 LOC, comp=57 LOC, case=355 LOC. They share **no architectural structure**:
  - reg = multi-round LLM + DB unfold (article→siblings→sections).
  - compliance = single-pass on a flat deduped pool, weak_axes feedback to outer loop.
  - case = single-pass per-query with channel-fusion pre-stage.
- LoopState is ~40% shared, ~60% domain-specific. A unified state object would carry 6+ null fields per executor.
- The pipeline-stage names hide rather than expose the real architectural axis (domain, not function).

**Decision**: keep per-executor folders. Continue extracting genuinely shared helpers into `shared/` opportunistically (already happens — `shared/reranker_models.py`, `shared/reranker_loop.py`).

If a future change introduces real cross-executor reuse (e.g. a fully-shared reranker base class with three thin subclasses), revisit. Not the case today.

---

## 9. Latency budget

| Step                      | Today | With planner (cut-1) |
|---------------------------|------:|---------------------:|
| Planner LLM call          |     0 |              0.5–1.5 s |
| Reg expander+reranker     | 4–8 s |              4–8 s |
| Comp expander+reranker    | 4–8 s |              4–8 s |
| Case expander+reranker    | 4–6 s |              4–6 s |
| (above 3 in parallel)     | ≈ 4–8 s wall | ≈ 4–8 s wall |
| URA merge                 | <0.5 s |              <0.5 s |
| Aggregator                | 3–6 s |              3–6 s (1 call CRAC) or 9–18 s (DCR) |
| **End-to-end (CRAC)**     | **~10–15 s** | **~11–16 s** |

Net: **+0.5–1.5 s for the planner**. Trade-off: in single-mode runs (e.g. `comp` only), we *save* 4–8 s by skipping two executors entirely, so the planner pays for itself ≈10× on narrow queries.

---

## 10. Open questions — resolved

1. **Aggregator route**: Option B (invoke-keyed prompts). ✅ Implemented.
2. **`focus` parameter**: Promoted from "defer" to **core LLM choice** in cut-1.5. The planner picks `high` / `default` / `low` per invoked executor; `FOCUS_PROFILES` translates to numeric caps.
3. **Planner model**: `qwen3-flash` ✅ (aliased to Alibaba `qwen3.5-flash` in `agents/model_registry.py`).
4. **Sector vocabulary source**: `reg_search.sector_vocab.VALID_SECTORS` (39 entries) is the planner's canonical list. The `case_search/sector_vocab.py` (26 entries) merge into `shared/sector_vocab.py` is **deferred to cut-2** — current pragma works because the canonicalizer downstream tolerates substring matches.
5. **Fallback when planner errors**: full triangulation with all-`default` focus. ✅ Implemented in `runner._fallback_plan`.

### Cut-2 carry-forward

- ~~Merge `reg_search.sector_vocab` + `case_search.sector_vocab`~~ — **resolved differently in cut-1.5**: the two vocabs are non-mergeable (they index different DBs at different granularities — reg = ministry-level Saudi gov sectors on `regulations.sectors[]`, case = commercial-court case categories on `cases.legal_domains[]`). Both moved to `agents/deep_search_v3/shared/sector_vocab/{regulations,cases}.py` with thin re-exports at the original paths for backward compat. The planner uses the regulations vocab.
- Physically delete the static sector-classification block from `reg_search/prompts.py:prompt_2` and `case_search/prompts.py:prompt_3` — **deferred**. The block stays for the `enable_planner=False` path so legacy callers don't lose all sector filtering. Promote this when `enable_planner=True` becomes the default (planned for cut-2). Today the runtime suppression injected via dynamic instructions handles the planner-on case correctly.
- **Planner-↔-case sector vocab impedance** (new, surfaced in cut-1.5): the planner picks sectors from the *regulations* vocab; when those flow into `case_search` via `sectors_override`, they may not match the *cases* vocab. Today the case `SearchNode` bypasses canonicalization on override (per the executor reset), so non-matching tokens become a no-op filter. Cut-2 fix options: (a) re-canonicalize the override against the cases vocab inside the case loop, (b) have the planner emit two separate sector lists, or (c) use a cross-vocab map. Pick (a) by default — minimal surface change.
- Re-introduce the clarification step (likely a separate tiny LLM call, not a field on `PlannerOutput`); wire CLI `ask_user = input` and HTTP duplex channel (Phase 6 step 3).
- Optional: per-domain multi-aggregator + meta-aggregator (deferred Option C from §6) if cross-domain coherence on cut-1.5 measurably fails.

---

## 11. Acceptance criteria for cut-1.5

Code-level — verified locally:
- [x] Planner emits a valid `PlannerOutput` for representative invoke combinations (smoke-tested against the new schema in `apply_plan_to_deps` end-to-end).
- [x] Singleton-invoke runs (`{reg}`, `{compliance}`, `{cases}`) skip disabled executors; `_per_executor_stats` writes a zeroed entry for skipped phases.
- [x] Sector pre-filter flows from `deps.sectors_override` into `build_ura_from_phases` when reg is disabled; mismatch with reg's own sectors is logged.
- [x] Aggregator prompt key derived from `invoke` set and recorded on `AggregatorOutput.prompt_key`.
- [x] No regression on the v3 reg/compliance/case test suites (112/112 functional pass; 4 pre-existing failures — Python 3.13 parenthesized-CM bug + stale `response_tokens_limit` literal — unrelated).
- [x] No regression on the v4 aggregator suite (74/74 pass, including 17 new structure tests for the mode-specialized prompts).

Live-infrastructure — pending operator runs:
- [ ] Wall time on full triangulation regressed by no more than +1.5 s vs. v3 baseline (needs Supabase + LLM run).
- [ ] Smoke runs on q5/q10/q16/q27/q28 with `enable_planner=True` — confirm invoke selection matches a human read of each query.

---

## Appendix A — Discarded designs

### A.1 Cut-1 PlannerOutput (rejected)

The original schema had the LLM choose `mode`, full `expander_max_queries` triplets, full `reranker_caps` nested dict, optional `rrf_thresholds`, `aggregator_prompt_key`, `detail_level`, and `needs_clarification`. **Rejected** during cut-1.5 review:

- The LLM has no signal to pick concrete numeric caps (`{"high": 8, "medium": 4}`) from a text query — it would copy the defaults from the prompt 95% of the time, and the other 5% would be guesses.
- `aggregator_prompt_key` was a derived quantity already (`mode → key` was a fixed table); making the LLM emit it added a validator path and one more way for the model to disagree with the deterministic mapping.
- Stripping the numeric tuning out of the prompt cut its length roughly in half and let us re-tune `FOCUS_PROFILES` without touching prompt text or invalidating prior LLM behavior.

The cut-1.5 schema replaces all of the above with a single qualitative `focus` choice the LLM *can* meaningfully make from query text ("does this query lean hard on case law? then `cases: high`"), and the deterministic table converts that to numbers.
