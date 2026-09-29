# Expander query cap — `cap` on the Blog-Post API

**Status:** planned, not built
**Supersedes:** the uncommitted `complexity` work in the working tree (see §2)
**Scope:** the editorial/marketing API only. The in-app path cannot reach this — §3.

---

## 1. What this is

One integer on a blog-post job request that says how many sub-queries each executor's
expander may produce on this run.

```jsonc
POST /internal/blog-post-jobs
{ "idempotency_key": "...", "question": "...", "cap": 2 }
```

`cap: 2` ⇒ the reg-compliance expander and the case-search expander are each told to
produce **at most 2** queries, and anything past 2 is dropped in emitted order.

It is an **operator** decision carried on a headless job — the editor's call about how
wide a question deserves to be searched, and therefore what the article costs. The
planner never picks it, never sees it, and is not capped by it.

---

## 2. Why this replaces `complexity` rather than extending it

The working tree currently holds an uncommitted feature doing nearly this, keyed on a
three-word band (`simple`/`medium`/`complex` → `(2,4)`/`(3,6)`/`(5,9)`). It is
well-built and it works. It is being replaced for two reasons:

1. **It cannot express the ask.** The tightest band is `simple`, whose ceiling is 4.
   There is no way to say "at most 2".
2. **Nothing shipped, so replacing is free.** The feature is uncommitted;
   `BlogPostJobRequest` has no `model_config`, so deployed Pydantic defaults to
   `extra="ignore"` and **production silently drops `complexity` today** — no 400, no
   pin, the run goes unpinned. The dashboard's display copy was written against a spec
   document, not a live endpoint. There is no client to keep working and no data to
   migrate.

⚠ Do not "preserve `complexity` for compatibility". There is nothing to be compatible
with. Two fields that both mean "how wide to search" is the outcome to avoid.

**The replacement is net-subtractive on the agents side** — it deletes a lookup table,
a vocabulary, and a `Literal` from `planner/`, and narrows every hop from
`tuple[int, int] | None` to `int | None`.

---

## 3. The in-app boundary — structural, not conventional

Verified, not assumed:

- **`PinnedPlan` is constructed in exactly one place in the codebase** —
  `backend/app/api/deepsearch_api/generate.py:134`, the headless API. Nothing in-app
  builds one.
- The cap is minted in exactly one place — `planner/apply.py`,
  `build_retrieval_config(cap=…)`. In-app callers pass nothing, so it stays `None`.
  Every other site in the thread is pass-through.
- With `None`, no block renders into either expander's user message and no clamp
  installs. Byte-identity is already pinned by
  `case_search/tests/test_expander.py::test_unpinned_user_message_is_byte_identical`
  and `reg_compliance_search/tests/test_expander_prompts.py::test_the_whole_user_message_is_byte_identical_when_unpinned`.
  **These two tests are the boundary. Keep them green through the rename.**

The shared *code* is imported by executors that also run in-app; the shared *behaviour*
is gated on a value only the API can mint.

---

## 4. Decisions (locked)

| # | Decision | Rationale |
|---|---|---|
| D1 | Raw integer `cap`, not a band | The editor says the number. No level→range table to keep in sync with a dashboard. |
| D2 | `cap` **replaces** `complexity` | §2. One concept on the wire. |
| D3 | **Ceiling only** — the block says "at most N" | The word is *cap*. No floor, nothing invented, no under-floor warning. The model may return fewer than N and that is a valid outcome. |
| D4 | **Per expander CALL**, not per run | reg keeps `max_rounds=3`, so a capped job may spend up to `N × rounds` reg searches. Accepted — a retry round exists because round 1 was insufficient, and starving it would defeat the retry. |
| D5 | **Minimum 2**, below that is a 400 | At `hi == 1` the sectioned case path cannot satisfy its ≥2-channel rule and `clamp_queries`' channel-rescue no-ops. Flooring at 2 closes that hole by construction. |
| D6 | **No upper bound** — `cap: 50` is accepted | Both expander prompts top out at 10, so a cap above that simply never binds. Validation that can never change an outcome is noise. |
| D7 | One value, **both expanders** | A job's width is a property of the question, not of the executor. `reg_compliance_led` + `support` runs two executors and both get `cap` — not `cap` split between them. |
| D8 | `null`/absent ⇒ unpinned, byte-identical | Never coerced to a default. An absent cap is a real answer: each expander judges from its own prompt guidance, exactly as before this existed. |

---

## 5. Evidence the mechanism actually binds

14 live expander calls, same deliberately sprawling labour-law question
(termination + notice + end-of-service + unfair dismissal + probation + tribunal
deadlines), `prompt_1` reg / `prompt_3` case:

| pin | reg emitted | case emitted | complied |
|---|---|---|---|
| none | **9** | **10** | — |
| (2,4) | 4 | 3 | ✅ |
| (2,2) ×3 | 2, 2, 2 | 2, 2, 2 | ✅ 6/6 |
| (1,1) ×3 | 1, 1, 1 | 1, 1, 1 | ✅ 6/6 |

The prompt block is doing the work; `clamp_queries` never had to fire. **That is the
expected steady state and not a reason to drop the clamp** — it is the guarantee that
makes the number a cap rather than a request, and it is the only thing standing between
a bad generation and an unbounded search bill.

At (1,1) all three case trials returned a single `principle` query — the ≥2-channel
rule broken, exactly as D5 predicts.

---

## 6. The thread

```
BlogPostJobRequest.cap  (int | None)
  → metadata._editorial.cap        (job row)
  → generate_answer_headless(cap=)
  → PinnedPlan.cap
  → build_retrieval_config(decision, cap=)
  → RetrievalConfig.expander_query_cap
  → FullLoopDeps.expander_query_cap
  → LoopState.expander_query_cap   (both executors)
  → render_cap_block()  +  clamp_queries()
```

Every hop is `int | None`, default `None`. Same shape as today, one element narrower.

⚠ `cap` stays **out of** `PinnedPlan.is_fully_pinned`, `.decision()` and `.overlay()`.
It says nothing about mode or support, so a job pinning only `cap` must still run
phase 1 in full.

---

## 7. File-by-file

### 7.1 `agents/deep_search_v4/shared/query_range.py` → rewrite

Rename the module to **`query_cap.py`**. The contents shrink:

- **DELETE `level_for_range()`** — no levels exist any more. (It is also the source of
  the `"the editor rated this question **custom**"` wart when the range was off-table.)
- **DELETE `warn_if_below_floor()`** — D3, there is no floor.
- **`render_query_range_block(lo, hi, level)` → `render_cap_block(cap)`**, new text:

```
---
## Query count for this run — capped by the editor

Produce **at most {cap}** queries in this run. This caps the "Number of queries"
guidance in your instructions for this run only.
Order your queries by importance: if you emit more than {cap}, only the first {cap} are used.
```

- **`clamp_queries(items, hi, *, channel_of=None, executor="")` — KEEP AS IS.** It
  already takes a bare ceiling, truncates in emitted order, and carries the
  channel-rescue. Only its docstring's references to levels/floors change.

⚠ Two invariants carry over verbatim and must survive the rewrite:

1. **The block goes in the USER message, never the system prompt.** The system prompt is
   the DashScope prefix-cache key; a per-run block there would miss the cache on every
   expander call in the product — in-app runs included — to serve a field only editorial
   jobs set.
2. **The block goes LAST in the message.** It overrides the prompt's own "Number of
   queries" guidance, and an instruction that overrides another must be read after it.

### 7.2 Backend API

| File | Line | Change |
|---|---|---|
| `models.py` | 136 | `complexity: Optional[str]` → `cap: Optional[int]`, description rewritten |
| `router.py` | 65, 71 | delete `_COMPLEXITIES` |
| `router.py` | 141–146 | `if req.cap is not None and req.cap < 2:` → 400 `قيمة cap غير صالحة`. **No upper bound** (D6). |
| `service.py` | 209–228 | `_opt_complexity()` → `_opt_cap()`: `None` stays `None`; a non-int or `< 2` on an old row degrades to `None` + WARNING, never raises — a job that cannot be capped should still run. The 400 is earned at the boundary, not at dispatch. |
| `service.py` | 243 | `editorial_config`: `"cap": req.cap` |
| `service.py` | 274 | `read_editorial_config`: `"cap": _opt_cap(raw.get("cap"))` |
| `service.py` | 647–650 | `process_job`: `cap=cfg["cap"]` |
| `generate.py` | 95, 110–116, 137 | signature `cap: Optional[int] = None`, docstring, `PinnedPlan(cap=…)` |
| `generate.py` | 188 | span attr `pinned_complexity` → `pinned_cap=pinned_plan.cap or 0` (`0` reads as "not pinned"; there is no cap of 0) |

### 7.3 Planner

| File | Line | Change |
|---|---|---|
| `models.py` | 77, 433 | **DELETE** the `Complexity` literal and its `__all__` entry |
| `models.py` | 359–363, 379 | `PinnedPlan.complexity` → `cap: int \| None = None`; docstring updated, orthogonality note kept |
| `apply.py` | 8–11 | module docstring: the ONE exception is now the `cap` pin |
| `apply.py` | 30 | drop `Complexity` from the import |
| `apply.py` | **44–56** | **DELETE `EXPANDER_QUERY_RANGES` entirely** |
| `apply.py` | 165 | `RetrievalConfig.expander_query_range: tuple[int,int] \| None` → `expander_query_cap: int \| None` |
| `apply.py` | 172, 190–200 | `build_retrieval_config(…, cap: int \| None = None)`; docstring |
| `apply.py` | 242 | `expander_query_cap=cap` — a straight pass-through, no lookup |
| `apply.py` | 248 | drop `EXPANDER_QUERY_RANGES` from `__all__` |
| `runner.py` | 253 | span attr → `pinned_cap=pinned.cap or 0` |
| `runner.py` | 509 | `cap=pinned.cap if pinned is not None else None` |

### 7.4 Orchestrator

| Line | Change |
|---|---|
| 138 | `FullLoopDeps.expander_query_range` → `expander_query_cap: int \| None = None` |
| 359–361 | reg phase span attr → `expander_query_cap=deps.expander_query_cap or 0` (the `f"{lo}-{hi}"` formatting goes away) |
| 394, 617 | pass-through rename, both executors |
| 677–679 | case phase log-record attr → same `or 0` convention, **same key as the reg span attr** so the two stay queryable together |
| 1172 | `expander_query_cap=config.expander_query_cap` |

### 7.5 Executors — symmetric across both

| File | Change |
|---|---|
| `reg_compliance_search/models.py:461`, `case_search/models.py:~508` | `LoopState.expander_query_range` → `expander_query_cap: int \| None` |
| `reg_compliance_search/prompts.py:232–276` | `build_expander_dynamic_instructions(…, cap: int \| None = None)`; render `render_cap_block(cap)` LAST |
| `case_search/prompts.py:541–590` | `build_expander_user_message(…, cap: int \| None = None)`; block appended after `<context_blocks>` |
| `reg_compliance_search/loop.py:148,167–176,227` | pass `cap=`, clamp on `cap`, **delete the `warn_if_below_floor` call**. Keep the `rationales` trim — it is positional and 1:1 with `queries`; untrimmed, every downstream log line attributes the wrong reason to the wrong query. |
| `case_search/loop.py:255,~262,314` (flat) | same, no `channel_of` — the legacy path's queries carry no channel |
| `case_search/loop.py:604,~611,681` (sectioned) | same, **with `channel_of=lambda q: q.channel`**. `TypedQuery` carries its own rationale, so there is no parallel list to trim. |
| `case_search/loop.py:1439,1498` | `run_case_search(…, expander_query_cap=…)` |
| both `logger.py` (reg 221–246, case 152–178) | `query_range` → `cap: int \| None`; header line becomes `**Query cap (editorial):** N — emitted X, kept Y (clamped, Z dropped)`. **Keep `emitted_count`** — `output` is the post-clamp object, so without it the log shows the truncated list with no trace anything was dropped. |

⚠ In both loops the clamp mutates the **instance**, never the schema. `ExpanderOutput` /
`ExpanderOutputV2` stay uncapped so the "expander failed → one query" fallback below each
`try` is untouched and an unpinned run costs nothing.

---

## 8. Tests

| File | Refs | Action |
|---|---|---|
| `agents/deep_search_v4/shared/tests/test_query_range.py` | 21 tests | → `test_query_cap.py`. Drop the `level_for_range` and floor suites; keep and re-point: exact block text, truncation **in emitted order**, the ≥2-channel rescue, `cap=None` renders nothing. |
| `backend/tests/test_editorial_publishing.py` §9 | 19 tests | Rewrite for `cap`. Keep the shape: unpinned-by-default, round-trip through the job row, unknown value on an old row degrades to `None` + logs, `null` passes validation. Replace the `hard`/`banana` 400 cases with **`cap: 1`, `0`, `-1` → 400** and add **`cap: 50` → accepted** (D6). |
| `reg_compliance_search/tests/test_expander_prompts.py` | 14 | Re-point. **`test_the_whole_user_message_is_byte_identical_when_unpinned` is load-bearing — §3.** |
| `case_search/tests/test_expander.py` | 8 | Re-point. Same note for `test_unpinned_user_message_is_byte_identical`. |
| `planner/tests/test_apply_modes.py` | 32 | Re-point; delete the `EXPANDER_QUERY_RANGES` table tests, add "a cap passes straight through, no lookup". |
| `planner/tests/test_run_retrieval.py` | 13 | Re-point to `expander_query_cap`. |
| `case_search/tests/test_loop.py` | 6 | Re-point. |
| `planner/tests/test_planner_models.py` | 7 | **Missed on the first pass.** Holds the `PinnedPlan` orthogonality suite — cap-alone-is-not-fully-pinned, never-reaches-`decision()`, not-overlaid. These are the §6 ⚠ invariant, so they are load-bearing, not incidental. |
| `backend/tests/test_deepsearch_api.py` | 5 | **Missed on the first pass.** Held a cross-layer test asserting the router's word list equalled `EXPANDER_QUERY_RANGES`. Both sides are deleted, so it is re-pointed to assert the **inverse**: neither side has a lookup table any more, so the drift class it guarded is structurally gone rather than merely checked. |

New coverage worth adding:

- **D5 as a unit**: `clamp_queries(items, 2, channel_of=…)` where all of the first 2 share
  a channel ⇒ the rescue swaps and the result carries 2 distinct channels. (Exists; keep
  it and make it explicit that cap ≥ 2 is what makes the rescue reachable.)
- **D7**: one `FullLoopDeps.expander_query_cap` reaches *both* executors' `LoopState`
  under `reg_compliance_led` + `support=True`.

---

## 9. Docs

| File | Section | Change |
|---|---|---|
| `.claude/plans/blog_post_api_protocol.md` | §7.1 request table | row `complexity` → `cap` / int / no / `null` |
| " | §11.1 | rewrite: the table of three levels becomes the cap's semantics — at most N **per expander call, to every executor in the run**; min 2 (a 400 below); no max; `null` ≠ a default. |
| " | §17 versioning | ⚠ the line currently reads **"v2.1: `complexity` added"**. Rewrite to announce `cap` — as written the doc promises a field that will never exist. |
| `agents/deep_search_v4/planning/MODE_PROFILES.md` | §7 | rewrite: "Editorial complexity pin" → "Editorial query cap". Drop the level→range table. **Keep** the four sub-points that are still true and still easy to get wrong: user-message-not-system-prompt, block-goes-last, truncation in emitted order + the channel rescue, and `null` ⇒ byte-identical. Drop the "nobody pads the floor" bullet (no floor exists). |

---

## 10. Ship gate

1. **`.gitignore`** — `tests/` is ignored repo-wide (`.gitignore:14`), and **seven of the
   eight test files this change touches were untracked**, not just the new one. Four are
   durable enough to re-include, and all four now are:
   - `shared/tests/test_query_cap.py` — the only guard on the truncation logic and on the
     block's exact prompt text.
   - `reg_compliance_search/tests/test_expander_prompts.py` and
     `case_search/tests/test_expander.py` — the two byte-identity tests §3 calls **the
     boundary**. Untracked, a clean clone ships the in-app guarantee with nothing holding
     it, and the refactor that breaks it (rendering the block unconditionally, or moving
     it into the system prompt) breaks it silently.
   - `case_search/tests/test_loop.py` — exercises the clamp *through the node*. Drop
     `channel_of` from the sectioned call site and every unit test still passes while the
     ≥2-channel rule dies on every truncated editorial run. Nothing else holds that wiring.

   The remaining four (`planner/tests/test_apply_modes.py`, `test_run_retrieval.py`,
   `test_planner_models.py`, `backend/tests/test_deepsearch_api.py`) are left untracked
   per existing repo policy. ⚠ `test_planner_models.py` is the closest call — it holds the
   orthogonality suite (a cap-only job must still run phase 1), and losing it means a
   future edit could let `cap` leak into `is_fully_pinned` with nothing to catch it.
   Worth revisiting if that invariant is ever touched again.
2. **Diff every file before `git add`** — the tree is always dirty here, and this change
   touches 18 files across two subsystems. Confirm `query_cap.py` is actually tracked.
3. **Backend-only change** ⇒ the frontend service will not redeploy, which is correct.
4. **Post-deploy verification** — submit one real job with `cap: 2` against the deployed
   API and confirm in Logfire that `pinned_cap=2` on the planner span and that each
   executor's expander emitted ≤ 2. A run whose span shows `pinned_cap=0` means the
   field was dropped — the exact §2 failure, and the thing this deploy is meant to fix.

---

## 11. Traps

- ⚠ **Never default an absent `cap`.** `null` means "each expander decides". Coercing it
  to a number would cap every editorial job that never asked to be capped, with nothing
  in any response or log to say so.
- ⚠ **Do not move the block into the system prompt.** §7.1, invariant 1 — it would cost
  the DashScope prefix cache on every expander call in the product.
- ⚠ **Do not re-order the truncation.** Emitted order is the model's own importance
  ranking, and the block tells it so. Dropping the longest, sampling, or re-ranking makes
  which angle of the question survives a coin toss.
- ⚠ **Do not add a floor back.** D3. Padding to reach a number means inventing a query
  nothing in the question called for and spending a real search on it.
- ⚠ **`cap` is not a planner cap.** `build_retrieval_config` still never caps the expander
  on its own; "the planner no longer caps the expander's sub-query count" in `apply.py`
  stays true.
- ⚠ **Watch the two byte-identity tests** (§3). They are what proves the in-app path is
  untouched, and a rename that quietly breaks them breaks the only guarantee this feature
  makes to the product.
