# Sector Picker Agent — parallel sector decision

**Status:** PLANNED · **Date:** 2026-05-24 · **Driver:** conv `faa3b71e` regression diagnosed in `agents_reports/convo_faa3b71e/focused_companies_law_investigation.md` §D–H.

## 1. Why

The single `planner_decider` LLM call picks mode + support + sectors + brief + context_labels in one shot. The sectors field is the highest-blast-radius output (a wrong label drops the controlling law via the post-RPC `_filter_by_sectors` step) yet the decider sees **only a flat numbered list of 38 sector names** — no examples, no descriptions. For a "أبدا مؤسسة وأحولها لشركة" question it picked `المعاملات التجارية`; `نظام الشركات` is tagged `['حوكمة الشركات والاستثمار', 'المهن المرخصة', 'المنظمات غير الربحية']`. Sets disjoint → every chunk from `92b8d296` dropped → second-order reranker error compounded the failure. Diagnosed in §C–F of the focused report.

## 2. Goal

Move sector selection out of `planner_decider` into a dedicated **`sector_picker`** agent (Layer 3 Task, tier_2 deepseek-flash primary) that fires **in parallel with the expanders**, with the post-RPC filter step awaiting its output before passing chunks to the reranker.

User-confirmed decisions (2026-05-24):
- **Replace**, not augment — `PlannerDecision.sectors` is dropped entirely; `sector_picker` is the sole source.
- **All modes** fire the picker (`reg_led` / `case_led` / `compliance_led` / `full`).
- **Inputs** = same surface as expander: `query` + `planner_brief` + `context_blocks` (`case_brief`, `prior_search_lessons` when present).
- **Failure** = drop the filter (run unfiltered). No fallback to decider's pick.
- **Output bounds: min 2, max 5 sectors, or `null`.** Inclusivity beats accuracy: the diagnosed failure was over-narrowing (one sector, wrong one). A 3- or 4-sector AND-set that contains the right one is better than a 1-sector set that misses it — the post-filter candidate pool still gets semantic-ranked, so over-inclusion costs nothing but under-inclusion is fatal. If the model judges the question needs 6+ sectors to cover, return `null` (filter is useless above that breadth).

## 3. Where it fires (concurrency design)

Critical finding from the trace: `search_chunk_titles` RPC does **not** take sectors (`reg_search/search.py:108`). The sector filter is applied post-RPC in `_filter_by_sectors` (`reg_search/search.py:137-162`). Same pattern in `case_search`. **`compliance_search` is the exception** — `hybrid_search_services` does take `filter_sectors` as an RPC param (`compliance_search/search.py:166-167`).

Join point per executor:

| Executor | RPC takes sectors? | Earliest point that needs sectors |
|---|---|---|
| reg_search | No | `_filter_by_sectors` post-RPC, before reranker |
| case_search | No | `_filter_by_sectors` post-RPC, before reranker |
| compliance_search | **Yes** | `hybrid_search_services` RPC call |

So in reg/case the expander → embed → RPC chain can fully overlap the sector picker. In compliance, the picker must finish before the search RPC starts. In all three, the **reranker** is what awaits both.

Implementation pattern: `asyncio.Task` shared via a Future on `FullLoopDeps`.

- `run_retrieval` (in `agents/deep_search_v4/orchestrator.py`) spawns `sector_picker_task = asyncio.create_task(run_sector_picker(...))` BEFORE calling `run_full_loop`. The task is stashed as `FullLoopDeps.sectors_future: asyncio.Future[list[str] | None]`.
- The current `FullLoopDeps.sectors_override: list[str] | None` is **removed**. The field becomes `sectors_future`.
- Each executor phase awaits `sectors_future` at the point it needs sectors:
  - `reg_search/loop.py` SearchNode: await before calling `search_regulations_pipeline` (or, better, push the await into pipeline step 6 — see §6).
  - `case_search/loop.py`: same pattern as reg.
  - `compliance_search/loop.py`: await **before** the `search_compliance_raw` call (line 200) so `filter_sectors` is materialized.

`asyncio.Future` is the right primitive here: the executors await it; the picker task sets it once on completion (or sets `None` on failure/timeout). Multiple awaiters get the same value.

## 4. Layer & Tier

- **Layer 3 Task** — does not talk to the user, does not write workspace_items. Pure transformer like aggregator / expander.
- **Tier 2** with `deepseek` as primary (per user spec). New slot in `agents/utils/agent_models.py:89`:

  ```python
  "sector_picker": ModelPolicy("tier_2", primary="deepseek"),
  ```

  Cost: deepseek-v4-flash ≈ 10× cheaper than tier_1. One call per deep_search invocation. Cost-tracking via the existing tier_2 rate.

## 5. New files

```
agents/deep_search_v4/sector_picker/
├── __init__.py          # exports run_sector_picker, SectorPickerOutput
├── agent.py             # create_sector_picker() → Pydantic AI Agent
├── deps.py              # SectorPickerDeps (query, planner_brief, context_blocks, supabase optional for telemetry)
├── models.py            # SectorPickerOutput(sectors: list[str] | None, rationale: str)
├── prompts.py           # System prompt with ENRICHED sector list (each sector + 5-10 example regulation titles)
├── runner.py            # run_sector_picker(query, planner_brief, context_blocks, deps, *, model_override) → SectorPickerOutput | None  (None on failure)
└── tests/
    ├── __init__.py
    ├── conftest.py
    └── test_sector_picker.py   # FunctionModel + canonicalize integration
```

### 5.1 Prompt: enriched sector list

The prompt's centerpiece is a **descriptive** sector list, not a flat list. Format per sector:

```
N. <اسم القطاع>
   مثال: <title>, <title>, <title>, …  (5–8 representative regulation titles)
```

Pre-generated once at module import from `regulations_v2` via a small static dump (`agents/deep_search_v4/sector_picker/sector_examples.py` — exported `SECTOR_EXAMPLES: dict[str, list[str]]`). The dump script (`scripts/build_sector_examples.py`) queries `regulations_v2` grouped by sector and writes the top-N titles per sector by chunk count (or alphabetical if no chunk count). Re-run as the corpus evolves.

Rationale block in prompt focuses on:
- The 38 canonical names are exhaustive — never invent.
- Companies Law specifically lives in `حوكمة الشركات والاستثمار` (call this out — the diagnosed bug).
- **Output bounds: 2–5 sectors, or `null` if 6+ would be needed.** Anything above 5 means the question is too broad for sector filtering — return `null` so the search runs over the full corpus.
- **Inclusivity over accuracy** — this is the load-bearing instruction. The filter is `sectors[] && {picked}` (Postgres array-overlap): a regulation passes if it carries **any one** of the picked sectors. Adding an extra adjacent sector only widens the candidate pool — the semantic ranker still surfaces the best matches inside that pool. Missing the right sector is fatal; including an extra one is free. **When in doubt, include more, not fewer.** Concretely: if the question touches Companies Law and could plausibly also touch Commercial Transactions and Licensed Professions, return **all three** — let the ranker decide which actually matches.
- Examples in the prompt should demonstrate this: e.g. "تأسيس مؤسسة وتحويلها لشركة" → `["حوكمة الشركات والاستثمار", "المعاملات التجارية", "المهن المرخصة"]` (3 sectors), not just `["حوكمة الشركات والاستثمار"]`.

## 6. Modified files

### 6.1 `agents/deep_search_v4/planner/models.py`
- **Remove** `PlannerDecision.sectors` field and both validators (`_coerce_sectors`, `_validate_sectors_size`).
- Update docstring on `PlannerDecision` to point sector responsibility at `sector_picker`.

### 6.2 `agents/deep_search_v4/planner/prompts.py`
- Remove `_SECTOR_NUMBERED_LIST`, the `## sectors` section, the `sectors` mention in the JSON output schema, and the sector field from every example in `## أمثلة`.
- Remove the `from .sector_vocab.regulations import VALID_SECTORS` import.
- Update the closing JSON schema to drop `sectors`.

### 6.3 `agents/deep_search_v4/planner/runner.py`
- Remove `_canonicalize_decision_sectors` and its two call sites.
- Drop `"sectors"` from the `EVENT_DECIDED` payload (Logfire dashboards that read this key need to be updated — see §8).
- Drop the `"planner.sectors"` span attribute.

### 6.4 `agents/deep_search_v4/orchestrator.py` — `run_retrieval`
- Spawn `sector_picker_task = asyncio.create_task(run_sector_picker(query, decision.planner_brief, context_blocks, sector_deps))` AS THE FIRST THING after `build_retrieval_config` returns.
- Stash on `FullLoopDeps.sectors_future` (replaces `sectors_override`).
- Pass `model_override=deps.model_override` to keep the CLI / monitor `--model qwen|deepseek|alibaba|openrouter` switch coherent for the new slot.

### 6.5 `agents/deep_search_v4/orchestrator.py` — `FullLoopDeps`
- Remove `sectors_override: list[str] | None = None` (line 133).
- Add `sectors_future: asyncio.Future[list[str] | None] | None = None`.
- `run_full_loop` reads `await deps.sectors_future` before logging `sector_filter` / `sector_source`. `sector_source` becomes `"picker"` on success, `"none"` on null/failure.

### 6.6 Per-executor loops
- `reg_search/loop.py` SearchNode (lines 225-258): replace `state.sectors_override` read with an awaitable. **Two options:**
  - (A) Resolve sectors **at SearchNode entry** — single await, then current code path runs unchanged.
  - (B) Push the await into `search_regulations_pipeline` step 6 so the RPC starts in parallel with the picker. Recommended — recovers up to ~1s for reg/case.
- `case_search/loop.py:545-548`: same pattern as reg.
- `compliance_search/loop.py:200-211`: `await deps.sectors_future` before the RPC call. No choice here — compliance must block.

### 6.7 `agents/utils/agent_models.py`
- Add `"sector_picker": ModelPolicy("tier_2", primary="deepseek")` (after the existing `compliance_search_reranker` entry).
- Cost-tracking already covers any tier_2 call (`tier_of_subagent` returns tier_2 for unknown role names? — verify; if not, add `"sector_picker": "tier_2"` to `_SUBAGENT_TIER`).

### 6.8 `agents/deep_search_v4/shared/sector_vocab/regulations.py`
- No change needed — `canonicalize_sectors` is still used by `sector_picker.runner` to sanitize LLM output before resolving the Future.

## 7. Failure mode

Picker task wrapped in `asyncio.wait_for(task, timeout=SECTOR_PICKER_TIMEOUT_S)` inside `run_retrieval`. Default `SECTOR_PICKER_TIMEOUT_S = 15`. On any of:

- `asyncio.TimeoutError`
- LLM exception (provider error, validation error after retries)
- Picker returned an empty list

→ Future resolves to `None`. Each executor's filter step treats `None` as "no filter" exactly the same way as today (`filter_sectors=None` → unfiltered). Existing safety net in `reg_search/search.py:152` (drop filter if it empties the set) stays as belt-and-suspenders.

Logfire span `deep_search.sector_picker` records `kind="ok" | "timeout" | "error" | "empty"`. Dashboards alert on the timeout/error ratio.

## 8. Telemetry & log migration

- **New span:** `deep_search.sector_picker` — attributes: `query_id`, `conversation_id`, `mode` (from decision), `sectors`, `rationale_chars`, `duration_s`, `kind`, `model`, `tokens_in`, `tokens_out`, `cost_usd`.
- **Decider span:** `planner.sectors` attribute removed; `planner.mode`/`planner.support`/`planner.planner_brief_chars`/`planner.context_labels` stay.
- **`run_full_loop` span:** `sector_filter` attribute now sourced from the awaited future; `sector_source` is `"picker" | "none"` (was `"planner" | "none"`).
- **Logfire dashboards** that currently filter on `EVENT_DECIDED.sectors` or `planner.sectors` need to switch to `deep_search.sector_picker.sectors`. List + update before deploying.
- **Forensic / monitor reports** under `agents_reports/` reference `planner_decider.final_result.sectors` — these are historical reads and stay valid for pre-cutover conversations.

## 9. Tests

### 9.1 Unit (FunctionModel)
- `sector_picker/tests/test_sector_picker.py`: FunctionModel returns canned sectors; runner canonicalizes; output schema validated.
- Negative cases: empty list (→ None), 1 entry (→ None — below the min-2 floor), 6+ entries (→ None — above the breadth ceiling), invented sector names (→ dropped via `canonicalize_sectors`, then re-checked against the 2–5 bound), JSON-stringified list.
- **Inclusivity bias smoke test**: for the diagnosed companies-law query, FunctionModel returns `["حوكمة الشركات والاستثمار", "المعاملات التجارية", "المهن المرخصة"]` and the runner accepts it (3 sectors, all canonical).

### 9.2 Integration (live, opt-in)
- New labelled set in `agents/deep_search_v4/sector_picker/tests/validate_live.py` — ~20 queries with ground-truth sectors (computed from Supabase: for each query, the union of `regulations_v2.sectors[]` over the regulation_ids the user expected). Drawn from the focused report + 10 known-good queries from `agents/test_queries.json`.
- Score: per-sector precision/recall, picker-vs-decider diff on the same queries (until decider's sector emission is removed).

### 9.3 Concurrency
- `tests/test_sector_picker_concurrency.py`: a fake `run_full_loop` that asserts the reg/case `search_chunk_titles` call started before the picker future resolved (proves true parallelism).

### 9.4 Failure
- Picker times out → executors run unfiltered, no exception propagates to user. Span records `kind="timeout"`.
- Picker raises → same as timeout.

## 10. Migration / rollout

Two waves to keep blast radius small:

**Wave A — add picker, keep decider's sectors as inert echo (1 PR):**
- New `sector_picker/` package + `agent_models.py` slot + orchestrator wiring + Future-based join.
- `PlannerDecision.sectors` stays in the schema but is **ignored** by `run_retrieval` (it reads only the picker future).
- Lets us A/B in production logs: log both `decision.sectors` (decider's pick) and `picker.sectors` (picker's pick) on the same span tree for ~1 week.
- Logfire query: percentage of queries where the two disagree, and which produces better aggregator confidence.

**Wave B — remove decider's sectors (1 PR):**
- Strip the field + validators + prompt sections per §6.1–6.3.
- Update the redesign memory entry (`project_planner_redesign.md`) and `feedback_layer_vs_tier.md` references.
- Migrate the Logfire dashboards.
- Burn-down of the agents_reports references is unnecessary — those are historical.

## 11. Open implementation choices (decide during build)

1. **Picker prompt: include `mode`?** Probably yes — the picker should know whether to bias toward case-corpus sector intuitions or regulation-corpus ones. Different sector distributions per corpus (`cases.legal_domains[]` is a 36-entry subset).
2. **Per-corpus sector vocab vs unified vocab?** Currently all three corpora share `VALID_SECTORS`. The picker can stay on the unified vocab; per-corpus narrowing happens at filter time. No prompt complexity added.
3. **Cache the picker output by `hash(query + planner_brief)`?** Probably not worth it — picker latency is 1-2s, and the same query rarely repeats verbatim within a conversation. Punt to a later optimization.
4. **Where to store `SECTOR_EXAMPLES`?** Option A: hardcoded Python module (re-generated by a script). Option B: cached JSON loaded once. Pick A for simplicity and to make changes reviewable in git.

## 12. Risks

- **Latency regression.** Picker adds an LLM call. Mitigated by parallelism with expanders (reg/case overlap fully; only compliance blocks). Worst case: +1-2s on compliance_led pure runs.
- **Cost regression.** One extra tier_2 call per deep_search. At deepseek-flash rates this is ~1/10 of a tier_1 call — negligible against the existing 6-12 LLM calls per run.
- **Wrong picker is worse than no picker.** Fallback to unfiltered on any uncertainty (6+ sectors emitted → null; <2 after canonicalize → null; failure → null). The HARD AND filter is the danger; soft fallback is safe.
- **Over-inclusion is a feature, not a risk.** With min-2 and an inclusivity-first prompt, the picker is expected to lean wide. This regresses neither precision (the semantic ranker still picks the best matches inside the wider pool) nor cost (post-filter chunk count is bounded by the top-K cut). Calibrate via the live validation set in §9.2 — if avg sector count creeps to 5 on simple queries, tighten the examples; if it stays low on cross-sector queries like the diagnosed one, the prompt is working.
- **Compliance executor MUST block.** If we forget to await `sectors_future` before the compliance RPC, it sees `None` and runs unfiltered — soft regression, not a crash. Catch in integration tests.

## 13. Done criteria

- `PlannerDecision.sectors` removed (Wave B).
- `sector_picker` span appears in every deep_search trace tree.
- Re-running conv `faa3b71e` T1 + T2 picks a 2–5 sector set that **includes** `حوكمة الشركات والاستثمار` (any reasonable adjacent picks like `المعاملات التجارية`, `المهن المرخصة` are fine and expected — inclusivity over accuracy) and produces an aggregator result that cites `92b8d296` or one of its satellites.
- Live validation set: recall@sectors improves vs the decider baseline (target: +20pp recall on the failure cases). Precision is a non-goal — over-inclusion is by design.
- Output-bound conformance: 100% of non-null picker outputs have 2–5 sectors after canonicalization; any out-of-bound outputs degrade to null cleanly.
