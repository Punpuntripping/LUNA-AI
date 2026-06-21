# Reranker Reliability + Calibration Plan

**Date:** 2026-06-21
**Scope:** deep_search_v4 rerankers (reg_search, case_search, compliance_search)
**Source diagnosis:** `agents_reports/reranker_assessments/` (convo_1b877b8e baseline + validation_2026-06-21). Empirical: **0 false-drops**; failures are over-inclusion (scope-leak, miscalibration) + a structured-output truncation bug.

This plan was produced from a 7-agent exploration of the schemas, runtime application path, salvager, prompts, downstream consumers, tests/callers/persistence, and a cross-domain calibration design pass. File:line refs are approximate — verify at edit time.

---

## Decisions locked with the user

| # | Decision |
|---|---|
| 1 | **Keep-only output contract** for all 3 rerankers — the LLM emits only what it keeps; code derives the drop set by set-difference (`drops = candidate_labels − kept`). |
| 2 | **Delete the `unfold` action** — reg becomes single-pass binary keep/drop (like case/compliance). |
| 3 | **Derive drops by difference** for all 3 (restores forensic drop visibility, now populated for every domain). |
| 4 | **Miscalibration rubric** — two-gate (on-mechanism + operative) for reg; ported to case (as a forcing-function) and compliance (as an ancillary taxonomy, re-anchored). |
| 5 | **Scope: reg only, light touch.** Root cause was the sector picker (fixed separately); case/compliance are not subject. |

---

## The unifying insight

Every HIGH-severity failure today resolves the same way: an incomplete/inconsistent **full-coverage** decision set is silently reconciled by **dropping** the affected candidates. The keep-only contract **removes the full-coverage invariant entirely**, so the whole "count mismatch / undecided / truncation" failure class stops existing rather than being guarded. The elaborate validate-and-recall loop discussed earlier becomes largely unnecessary — a short keep-list is *valid*, not an error.

Residual risk after keep-only: **under-keeping** (model forgets a good candidate) becomes undetectable by design. Accepted, because the data shows 0 false-drops — the model's keep-judgment is reliable; it was serialization and over-keeping that failed.

---

# Workstream A — Keep-only output contract + integrity gate (all 3)

### A1. Schema changes (`*/models.py`)

Replace the per-candidate "decision" models with **keep-only** models. Every emitted entry *is* a keep, so `action` disappears; `relevance` becomes **required** (closes the silent `keep→medium` coercion, failure #7).

**reg** (`agents/deep_search_v4/reg_search/models.py:122-172`):
```python
class RegKeep(BaseModel):
    label: str
    relevance: Literal["high", "medium"]          # REQUIRED now
    reasoning: str
    satisfies_axes: list[int] = Field(default_factory=list)

class RegRerankerClassification(BaseModel):
    sufficient: bool
    query_axes: list[str] = Field(default_factory=list)
    keeps: list[RegKeep] = Field(default_factory=list)   # renamed from `decisions`
    summary_note: str

    @field_validator("keeps", mode="before")              # NEW — closes failure #3
    @classmethod
    def _coerce_keeps(cls, v):
        # Reuse the ExpanderOutputV2._coerce_queries / PlannerDecisionV2._coerce_sectors
        # pattern: a JSON-stringified array "[{...}]" → list, deterministically, no retry.
        if isinstance(v, str):
            if v.strip() == "": return []
            try:
                import json as _json
                parsed = _json.loads(v)
                if isinstance(parsed, list): return parsed
            except Exception: pass
        return v
```
- **Delete** `action` and `direction` fields (direction also dies with unfold — Workstream B).
- **Delete** the `_relevance_only_on_keep` validator (moot — every entry is a keep).

**case** (`case_search/models.py:117-175`) and **compliance** (`compliance_search/models.py:59-94`): same shape — `CaseKeep{position, relevance(req), reasoning, satisfies_axes}`, `ServiceKeep{position, relevance(req), reasoning, satisfies_axes}`; field `keeps`; add the `_coerce_keeps` before-validator; compliance keeps `weak_axes`. Drop the now-moot `_relevance_only_on_keep` validators.

### A2. Application-path rewrite (`*/reranker.py`)

Replace "iterate all decisions, branch on action" with "iterate keeps, derive drops by difference." Sites (from the decision-application map):
- **reg** `reranker.py:336-410` (decision loop + undecided block) → iterate `classification.keeps`; build `all_kept`; **derive** `dropped_labels = set(by_label) - {k.label for k in keeps}`; build `all_dropped` from those (title/db_id from the candidate block, `reasoning=""`, `drop_reason="derived"`). The whole "undecided/completeness" block (386-410) is **deleted** — there is no completeness invariant.
- **case** `reranker.py:284-343` → same; derive drops from `pos_to_block` keys − kept positions. Delete the undecided block (325-343).
- **compliance** `reranker.py:224-242` → same; derive drops from `range(len(rows))` − kept indices. Adds the drop forensics compliance currently lacks.

**Integrity gate over the (small) keep set**, applied in all 3 right after parse:
- invalid label/position (not in candidates) → skip + `logger.warning` (hallucinated keep; safe to drop).
- duplicate label/position → keep first, skip rest + log.
- `relevance` missing → already a schema error → `ModelRetry` (the required field does the work).
- `kept == 0` of N>0 candidates → **one** retry with a dynamic note ("you kept 0 of N; if truly none apply, return empty — otherwise reconsider"); accept empty on the second pass. (Only retained retry signal.)

**Keep the existing salvager** (`agents/utils/structured_output.py`, TextOutput union, `retries=2`) as the parse-error backstop. The new `_coerce_keeps` before-validator fixes the array-as-string case *without* a retry.

### A3. Forensic drop derivation → persistence

The derived `all_dropped` flows through the existing adapter → `dropped_forensic` → `save_reranker_runs` chain. **NOTE:** the persistence layer (`backend/app/services/retrieval_artifacts_service.py`, adapters) appears to be mid-change to `kept_forensic`/`dropped_forensic` (your separate reranker_runs work). **Verify the current forensic field names at edit time** and have the derived drop set populate whatever the adapters now read. Update `backend/tests/test_retrieval_artifacts_service.py:404` (`dropped_results == []`) once drops are populated.

---

# Workstream B — Delete the `unfold` action (reg only)

Reg becomes single-pass keep/drop. **Important:** `unfold_reranker.py` renders the chunk context-window *view* (`unfold_chunk_precise`/`simple`/`format_chunk`) shown to the reranker — that **stays**. Only the neighbor-*fetch* + multi-round loop dies.

### DELETE (reg `reranker.py`)
- `MAX_RERANKER_ROUNDS` (~62), `_NEIGHBOUR_RRF_DECAY` (~66).
- The round loop `for round_num in range(...)` (~288-496) → collapse to a single classify pass.
- The unfold branch in the decision loop (~366-377), `to_unfold`, `seen_chunk_ids` (~283), `total_unfolds` (~280).
- The neighbor-fetch pipeline + `_fetch_neighbour` (~434-496) and the `fetch_chunk` import.
- The sufficiency-as-loop-terminator (~422-432) → just classify once.

### KEEP
- `_make_block` + `unfold_chunk_precise`/`unfold_chunk_simple`/`format_chunk` (the candidate-view rendering). Consider renaming `unfold_reranker.py` → `chunk_view.py` to remove the misleading name (optional).

### Schema / carriers
- Remove `RegRerankerDecision.direction` (already gone in A1's `RegKeep`).
- `RerankerQueryResult.unfold_rounds`/`total_unfolds` (reg `models.py:290-291`): stop populating; leave shared-model fields defaulted `0` for adapter compatibility (downstream reads are observational only — confirmed zero control-flow dependence).

### Prompt (reg `reg_search__reranker__prompt_1.md` / `prompts.py`)
- Delete the `### 3. unfold` section (~57-62), the multi-round explanation (~27-29 / prompts.py ~250-254), and the `direction` output rule (~75).
- Delete the **"Full coverage is mandatory / one decision per chunk"** rule (~79) and the `### 2. drop` section — replace with keep-only instructions (Workstream A copy below).

### Logging / CLI / monitor (observational — update, don't gate)
- `reg_search/logger.py` (~360-361, 433, 440, 443-456, 509, 517-532), `loop.py` (~453), `cli.py` (~739-740, 848), `monitor/run_monitor.py` (~264-267): remove unfold/direction columns + round/db-unfold stats.

### Recall tradeoff (flag honestly)
Unfold was the only mechanism pulling adjacent (prev/next) chunks; `search.py` (`TOP_K=15`) does **not** over-fetch contiguous chunks. Removing it means a neighbor article appears only if it independently ranks top-15. **Empirically supported** (unfold_miss/wasted_unfold were rare in the assessment, and the one observed unfold delivered no value). **Optional cheap mitigation** (default: skip): have `search.py` add the immediate prev/next neighbors of the top-K hits into the candidate pool so the reranker can keep them directly in one pass. Recommend removing now and adding neighbor-prefetch only if a recall regression shows up in re-validation.

---

# Workstream C — Miscalibration rubric (high vs medium)

Tightening `high` shifts the high/medium split, which is **load-bearing downstream**: relevance tier decides the URA bucket (`merger.py:249-252`, caps `MAX_HIGH_PER_SUBQUERY=12`/`MAX_MEDIUM=4`), citation numbering (high cited first, `preprocessor.py:407-409`), and the aggregator's confidence rubric. Expect more `medium` citations and possibly lower self-rated confidence — **re-validate after** (not a breakage).

### C1 — reg (origin of the two-gate)
Replace the relevance line (`reg_search__reranker__prompt_1.md:50-52`) with the **two-gate**: `high` requires BOTH (A) ON-MECHANISM (the specific doctrine, not the broad area/parent law) and (B) OPERATIVE (the governing rule that decides the issue, not definitions/scope/procedure/penalty-tables/closing provisions); else `medium`. Add the **mechanism-naming forcing-function** to the `reasoning` rule ("name the mechanism the chunk covers vs. the mechanism the sub-query asks; if they differ, not high") and a **scarcity** note (≈1-3 high per sub-query). (Full clause text already drafted in the assessment's `_prompt_fixes.md` Fix 3.)

### C2 — case (forcing-function, mostly already covered)
Case already encodes the gates (primary-axis + procedural-only downgrade + appeal-authority + overclaim). **Genuinely new** additions only:
- **Mechanism-naming forcing-function** in `reasoning` (catches انفساخ-vs-فسخ — the family-level axis match misses it). 
- **Explicit obiter/ratio clause** (a ruling that *recites* the doctrine but decides on another ground is not OPERATIVE) — extend the existing "Overclaim prevention" section (~54-57).
- **Explicit scarcity number** (≈1-3), reconciled with the `max_keep` ceiling.
- Phrase gate (A) as "**a** primary axis" (compound queries) and judge OPERATIVE on the reasoning **that is present** (truncation guard). Defer to the existing "Purely procedural rulings" section as authoritative to avoid contradiction.
Target: `case_search__reranker__prompt_1.md:41-43` + the Overclaim section.

### C3 — compliance (highest-value: new ancillary taxonomy; **re-anchor** gate B)
**Do NOT copy reg's "decides the issue" wording** — services don't decide; re-anchor gate (B) to **performs-the-act vs. informs-about-it**:
- **(A) ON-ACT:** the service performs the specific executive act the `focus_instruction` goal requires — not merely a service in the same sector/entity that does a *different* act.
- **(B) OPERATIVE:** the service **carries out the procedure and produces its legal effect**. **Ancillary → never `high`** (medium at most, often drop): استعلام / متابعة حالة / حجز موعد / بوابة معلومات (information-only), general portals, supporting sub-steps. (Guard: if the goal *itself* is an inquiry, the inquiry service passes gate A.)
- Add the **forcing-function** (name the act the service performs vs. the act the goal needs).
- **Do NOT add the scarcity number** — compliance already caps `high` more tightly ("≤2 high", ~line 248).
Target: `compliance_search/prompts.py:237-242` (keep/relevance block) + Output rules (~270).

---

# Workstream D — Scope (reg only, light)

Root cause was the sector picker (polluted candidate pool), now fixed separately. So: **minimal**.
- Keep/strengthen only the **reasoning forcing-function**: each `reasoning` must state the scope verdict (already implied at `reg_search__reranker__prompt_1.md:46` — make it explicit in the output rule).
- Optionally one sentence: "contracting regime is part of scope — a government-only / sector-authority regime does not govern a purely private matter." (Single line, **not** the full drop-rule blocks from the earlier proposal.)
- **Re-validate the residual scope_leak rate after the sector-picker fix lands.** Only escalate to the matter-frame anchor if a regime-leak residual remains. Case/compliance: no change (not subject).

---

# Cross-cutting

### Tests to update (from the test map)
- `case_search/tests/test_aggregator.py:154-156, 181-185` (assert `decisions[].action`) → update to keep-only `keeps`.
- `test_aggregator.py:201-202` (`unfold_rounds==0/total_unfolds==0`) → keep or drop with the field decision.
- `case_search/tests/test_loop.py:44-71` (`_make_reranker_result` helper) → align to keep-only RQR.
- `backend/tests/test_retrieval_artifacts_service.py:404` (`dropped_results == []`) → derived drops now populated.
- Add new tests: array-as-string coercion (`_coerce_keeps`), drop-by-difference correctness, invalid/duplicate label skip, `kept==0` retry, relevance-required schema error.

### Assessor agents (the tooling that found all this)
Update `.claude/agents/reranker-run-judge.md` parsing: keep-only dumps will show only keeps in the output; the judge must derive the drop set from **input candidates − keeps** (it already reads the input candidate pool). The "reranker stated a scope verdict per drop" check goes away (no per-drop reasoning); the judge re-judges drops independently as before.

### Open items to verify at edit time
1. `unfold_ura.py` — confirm it's fully orphaned by unfold removal (agent was inconclusive); delete only if no live importers.
2. The current forensic field names in the persistence layer (`kept_forensic`/`dropped_forensic` vs `dropped_results`) — your in-flight reranker_runs work; align the derived-drop wiring to it.
3. Whether to keep `action` anywhere (recommend: remove fully — keep-only makes it redundant).

---

# Suggested sequencing

1. **A (keep-only) + B (unfold removal) together** for reg — they touch the same `reranker.py` rewrite; do as one coherent pass. Then A for case + compliance.
2. **C (calibration)** prompts — independent; can land in parallel with A/B since it's prompt-only (no schema change — `relevance` enum unchanged).
3. **D (scope)** — trivial prompt tweak; land with C.
4. **Tests + assessor update.**
5. **Re-validate** with `@reranker-assessor` on a fresh sample (ideally after the sector-picker fix is in the data) to confirm: truncation gone, miscalibration down, scope_leak residual.

# Net effect
- Truncation / count-mismatch / undecided failure **class removed** (not guarded).
- Array-as-string fixed deterministically (no retry).
- Silent `keep→medium` and missing-direction coercions closed at the schema.
- Drop forensics **restored and populated for all 3 domains** (by difference).
- reg simplified to single-pass; unfold machinery (minus view-rendering) deleted.
- high/medium calibration tightened per-domain with domain-appropriate anchors.
- Scope handled at root (sector picker); reranker change kept minimal.
