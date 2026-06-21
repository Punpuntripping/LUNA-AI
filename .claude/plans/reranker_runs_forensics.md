# Plan — `reranker_runs` forensic-layer fixes

**Status:** BUILT 2026-06-20 (local, type-clean, offline-verified; migration 072
NOT yet applied to prod; NOT deployed) · **Authored:** 2026-06-20
**Scope:** Fix the per-sub-query forensic rows written by
`backend/app/services/retrieval_artifacts_service.py::save_reranker_runs`.

## Build notes (2026-06-20)
- All 14 source files in the manifest implemented. `python -m py_compile` clean
  across the set; `scripts/verify_reranker_forensics.py` exercises all 3
  adapters → `_build_row` offline and asserts bare-UUID `ref_id`, `source_table`
  ∈ {chunks,cases,services}, fetched `title`, and `dropped_results` (llm+cap).
  Compliance `ref_id` is the real `services.id` (NOT the sha1 citation hash).
- Case drops are reconstructed in `SectionedRerankerNode._process_one` from
  `decision_log` + `cands` + survivors (markdown reranker is id-blind); cap
  detection guarded on a non-empty `survived` set (fallback path can't tell).
- Compliance threads `state.per_query_dropped` (mirrors `per_query_service_refs`)
  → `compliance_to_rqr(per_query_dropped=...)`; reranker return widened 5→6-tuple
  (only caller updated). `service_id` added to `RerankedServiceResult`.
- Migration 072 dry-run validated; uses `WITH ORDINALITY`+`ORDER BY` so the
  title-join can't scramble the ranked array. Applying it rewrites 1,743
  historical prod rows → left for explicit go-ahead.

## Goals (locked with user 2026-06-20)

1. **Store dropped results** — `dropped_results` is currently always `[]`.
   Capture **LLM-dropped** results (with the reranker's Arabic reasoning) **and
   cap-truncated** results (reason: exceeded per-sub-query keep cap). Internal
   drops (undecided/auto, dedup, neighbour-boundary) are **out of scope**.
2. **Fix `ref_id` + add `source_table` + fetch `title`** — replace the prefixed
   `"reg:<uuid>"` with: a **bare real target-table UUID**, a **`source_table`**
   tag ∈ `{chunks, cases, services}`, and the human-readable **title**. Applies
   to both kept and dropped results.
3. **Sub-query embedding — DEFERRED.** Not built now. §7 records what it would
   take so the next pass is cheap.

## Confirmed current state (live DB, 1,743 rows, 2026-06-20)

| Observation | Evidence |
|---|---|
| Drops never stored | `0 / 1743` rows have `dropped_results`; `_build_row` hardcodes `"dropped_results": []` (line 143, `# TODO`). Domain rerankers return only `dropped_count: int`. |
| `ref_id` is prefixed | e.g. `"reg:b9b463fd-e358-52b3-beb9-dfbd62bcc3d7"`. |
| `title` empty for reg + compliance | only `254/1743` rows (the case rows) have a title. `RegURAResult`/`ComplianceURAResult` have no `title` attr → `getattr(result,"title","")` → `""`. |
| Embeddings uniform | reg/case/compliance all embed with Alibaba text-embedding-v4 @ **1024-dim**. |

### The cross-domain id inconsistency (the crux of Issue 2)

| Domain | citation `ref_id` seed | real table UUID available? | title field |
|---|---|---|---|
| reg → `chunks` | `chunks_v2.id` (uuid) | ✅ it **is** the seed | `chunks_v2.title` — computed in reranker (`RerankedResult.title`), **dropped at `reg_to_rqr`** |
| case → `cases` | `case_ref` (text) | ⚠️ `cases.id` exists upstream (`case_id` in search rows) but adapter uses `case_ref` | `CaseURAResult.title` ✅ |
| compliance → `services` | `sha1(service_ref)` (**hash**) | ⚠️ `services.id` selected by RPC (search.py:68) but dropped; **hash is irreversible** | `ComplianceURAResult.service_name` ✅ |

**Consequence:** the compliance hash cannot be reversed at save-time, so a real
`services.id` (and a clean `cases.id`) **must be carried from the adapters**,
where the original search row (with `id` + title) is still in hand. Save-time DB
resolution alone is insufficient. This dictates the design below.

> The citation `ref_id` (`reg:`/`case:`/`compliance:`) is **load-bearing** for
> the aggregator's `[n]` citations + `references_service` and is **NOT touched**.
> All changes live in a parallel forensic descriptor.

## Target row shape

`kept_results[i]` and `dropped_results[i]` become:

```jsonc
// kept
{ "source_table": "chunks",                       // chunks | cases | services
  "ref_id":  "b9b463fd-e358-52b3-beb9-dfbd62bcc3d7", // bare real UUID, no prefix
  "title":   "المادة (٥) إجراءات تقويم المطابقة",
  "relevance": "high", "source_type": "chunk",
  "reasoning": "…" }

// dropped
{ "source_table": "chunks",
  "ref_id":  "…uuid…",
  "title":   "…",
  "drop_reason": "llm",            // "llm" | "cap"
  "reasoning": "…Arabic note…",    // "" for cap drops
  "source_type": "chunk" }
```

`source_table` is technically derivable from row-level `agent_family`, but is
stored per-result so each result object is self-describing when flattened.

## Design — carry a forensic descriptor from the adapters

The adapters (`agents/deep_search_v4/ura/{reg,case,compliance}_adapter.py`) are
the one place that sees **both** the typed result **and** the original search
row (uuid + title) for kept results — and, after the reranker change, the
dropped rows too. They build self-contained forensic dicts; the service writes
them verbatim. This keeps forensic shape fully decoupled from the URA/citation
types.

### A. `shared/models.py` — `RerankerQueryResult`
Add two stored-only, forensic-only fields (additive, default empty):
```python
kept_forensic: list[dict] = field(default_factory=list)     # 1:1 with results
dropped_forensic: list[dict] = field(default_factory=list)  # LLM + cap drops
```
`results` (typed URA objects feeding the merger) is unchanged.

### B. Domain rerankers — retain dropped rows (Issue 1)
Each reranker already has the dropped row + reasoning in hand; today it only
does `total_dropped += 1`. Accumulate the rows instead.

- **reg** `reg_search/reranker.py::run_reranker_for_query`
  - `else:` branch (action drop, ~line 358): append
    `{"row": block["chunk"], "title": block["unfolded"].get("title") or block["chunk"].get("title",""), "reasoning": dec.reasoning, "drop_reason": "llm"}`.
  - cap-truncation (~line 499): the `all_kept[max_keep:]` slice → append each as
    `drop_reason="cap"`, `reasoning=""`.
  - Skip undecided/dedup/neighbour drops (out of scope).
  - Add `dropped_results: list[dict]` to reg `RerankerQueryResult`
    (`reg_search/models.py`); populate it.
- **case** `case_search/reranker.py` — same: `action=="drop"` rows (carry
  `case_id` uuid + title) + cap slice. Add `dropped_results` to case
  `RerankerQueryResult` (`case_search/models.py`).
- **compliance** `compliance_search/reranker.py::run_reranker_for_query` — single
  pass per sub-query: `action=="drop"` rows (carry row `id` + `service_name_ar`)
  + the per-query cap slice. Surface via the loop so it reaches
  `compliance_to_rqr` (drops attach to the originating sub-query naturally).

### C. Carry the real UUID for kept results (Issue 2)
- **reg** — `RerankedResult` already has `db_id` (chunks_v2.id) + `title`. No
  model change; `reg_to_rqr` reads them.
- **case** — add `db_uuid: str` to `RerankedCaseResult` (`case_search/models.py`),
  set from the search row's `case_id` in the reranker assembly (the row carries
  `case_id UUID`; see `case_search/search.py:491`). `case_ref` stays as the
  citation seed.
- **compliance** — add `service_id: str` to `RerankedServiceResult`
  (`compliance_search/models.py`), set from `row.get("id")` in
  `assemble_service_result` (the RPC already selects `id`, search.py:68).

### D. Adapters build the forensic dicts
- `reg_to_rqr`: per kept result → `{"source_table":"chunks","ref_id":r.db_id,"title":r.title,"relevance":r.relevance,"source_type":r.source_type,"reasoning":r.reasoning}`; map `sq.dropped_results` → `dropped_forensic` (`source_table="chunks"`, `ref_id=row["id"]`).
- `case_to_rqr`: `source_table="cases"`, `ref_id=r.db_uuid`, `title=r.title`.
- `compliance_to_rqr`: `source_table="services"`, `ref_id=r.service_id`, `title=service_name_ar`. Build dicts in `_service_to_ura`/the per-query loop so per-sub-query attribution is preserved.

### E. `retrieval_artifacts_service.py`
- `_build_row`: write `kept_results = rqr.kept_forensic` and
  `dropped_results = rqr.dropped_forensic` directly when present; keep the old
  `_kept_result_row` derivation as a fallback for any caller that didn't set
  them (defensive — both prod callers go through the adapters).
- Drop the `dropped_results: []` hardcode + its TODO.

### F. Migration 072 (OPTIONAL — historical backfill only)
No DDL needed (both columns are JSONB; embedding deferred). Optional one-shot to
fix the 1,743 existing rows: strip the prefix, set `source_table` from
`agent_family`, and backfill `title` by joining `chunks_v2`/`cases`/`services`
(skip compliance rows — their stored ref_id is the irreversible hash, so those
keep an empty title until naturally re-written). Gated behind user approval —
historical forensic rows may not be worth a backfill.

## §7 — Deferred: sub-query embedding (recorded, not built)
- DDL: `ALTER TABLE reranker_runs ADD COLUMN sub_query_embedding vector(1024);`
  (nullable). ~4 KB/row.
- Plumbing: return the computed `embedding` from `*/search.py` →
  domain `RerankerQueryResult` → adapter → `SharedRQR` → `_build_row`. One
  embedding per sub-query = one per row (natural fit). The lumped-compliance
  fallback path stores `NULL`.
- Use case: sub-query clustering / dedup / semantic drift analysis. Build when a
  concrete consumer exists.

## Risk / blast radius
- **Low** for Issue 2 (additive forensic dicts; citation path untouched).
- **Medium** for Issue 1 (drop-retention logic added to all 3 rerankers).
- Persistence stays best-effort (`save_reranker_runs` swallows failures) — a bug
  here cannot break a user turn.
- URA/citations, aggregator, `references_service`, `unfold_workspace_item`:
  **unaffected** (no change to `ref_id` or the typed URA results).

## Test plan
1. Unit: each adapter emits correct `kept_forensic`/`dropped_forensic` (bare
   uuid, right `source_table`, non-empty title) for reg/case/compliance.
2. Unit: reg + case rerankers populate `dropped_results` for `action=="drop"`
   and cap-truncation; counts reconcile with `dropped_count`.
3. Integration: one real deep_search turn → assert `reranker_runs` rows have
   bare-uuid `ref_id`, populated `title`, `source_table` set, and ≥1 row with
   `dropped_results`.
4. Regression: citations `[n]` + references unchanged for the same turn.

## File manifest

| File | Change |
|---|---|
| `agents/deep_search_v4/shared/models.py` | +`kept_forensic`, +`dropped_forensic` on `RerankerQueryResult` |
| `agents/deep_search_v4/reg_search/models.py` | +`dropped_results` on reg `RerankerQueryResult` |
| `agents/deep_search_v4/reg_search/reranker.py` | retain LLM + cap drops |
| `agents/deep_search_v4/case_search/models.py` | +`db_uuid` on `RerankedCaseResult`, +`dropped_results` |
| `agents/deep_search_v4/case_search/reranker.py` | set `db_uuid`; retain drops |
| `agents/deep_search_v4/compliance_search/models.py` | +`service_id` on `RerankedServiceResult` |
| `agents/deep_search_v4/compliance_search/reranker.py` | set `service_id`; retain drops |
| `agents/deep_search_v4/compliance_search/loop.py` | thread per-query drops to adapter |
| `agents/deep_search_v4/ura/reg_adapter.py` | build `kept_forensic` + `dropped_forensic` |
| `agents/deep_search_v4/ura/case_adapter.py` | build forensic dicts |
| `agents/deep_search_v4/ura/compliance_adapter.py` | build forensic dicts |
| `backend/app/services/retrieval_artifacts_service.py` | write forensic dicts; drop `[]` hardcode |
| `shared/db/migrations/072_*.sql` | OPTIONAL historical backfill |
