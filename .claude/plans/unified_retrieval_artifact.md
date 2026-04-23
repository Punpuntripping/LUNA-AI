# Unified Retrieval Artifact

**Status:** DRAFT — awaiting sign-off before build
**Problem:** Every domain (reg_search, case_search, compliance_search) emits its own bespoke reranker output format. Downstream consumers (aggregator, router, UI) have to reimplement dedup, relevance-merging, and domain awareness for each. Citations in synthesized answers have no durable handle to the underlying DB record, so "tell me more about source 4" is impossible today.

**Proposal:** A single canonical artifact — the **Unified Retrieval Artifact (URA)** — produced after each query's retrieval pipeline, keyed by stable content IDs, usable by the aggregator for synthesis, by the router for cache-hit decisions, and by the frontend for side-panel source lookup.

## 1. Mental model

The URA is a **content-layer cache**. It represents *what the DB returned for this query*, independent of who asked or how the answer will be framed.

```
User query
    │
    ├──▶ Query expander → sub-queries (reg + cases only)
    │
    ├──▶ reg_search ──▶ reranker ──┐
    │  (case_search — deferred)    │
    │                              ▼
    │                      partial URA (reg only, cases slot reserved)
    │                              │
    │                              ▼
    └──▶ compliance_search ◀── (original query + reg hits from partial URA)
                                   │
                                   ▼
                            result_merger
                                   │
                                   ▼
                           full URA (all 3 domains)
                                   │
              ┌────────────────────┘
              │
    ┌─────────▼──────────┐
    │     Aggregator     │  ← pure synthesis, no tool calls
    │  (framing layer)   │
    └─────────┬──────────┘
              │
    ┌─────────▼──────────┐         ┌─────────▼──────────┐
    │  User A (lawyer)   │         │  User B (client)   │
    └────────────────────┘         └────────────────────┘
```

**Compliance is a dependent step** — it runs after reg+cases finish, using their results as targeting context. It is never a parallel domain in the query expander. The aggregator receives the complete URA and is a **pure synthesis step** — no tool calls, no retrieval.

The URA is constant across users. The aggregator is the **personalization layer** that turns URA bytes into user-facing Arabic synthesis. The router can reuse URAs to skip expensive search loops on repeat queries, or to invoke with more targeted sub-queries when it sees partial coverage.

URA is a **hidden artifact** — users never see the JSON. They see the synthesis and, on click, the side panel for a specific source.

## 2. Stable IDs — the findings

Verified via live Supabase query:

| Table | Stable ID | Type | Notes |
|---|---|---|---|
| `articles` | `id` | UUID | DB is not re-ingested — UUID is stable |
| `sections` | `id` | UUID | DB is not re-ingested — UUID is stable |
| `regulations` | `id` | UUID | DB is not re-ingested — UUID is stable |

**We use the UUID `id` as the stable handle.** The DB is ingested once; UUIDs do not change. `chunk_ref` and `regulation_ref` exist in the DB and may appear in `metadata` for agent-internal reasoning, but are **never surfaced to users** and are not the primary URA handle — they would expose internal chunking strategy.

For **compliance_search** (scraped gov URLs + government services): no DB row exists, so the stable handle is a content-normalized URL hash. Proposed format: `compliance:{url_sha1_first16}` + a `url` metadata field for human-readable cross-reference.

For **case_search**: use the UUID `id` from the cases table, same as regulations.

## 3. The Unified Retrieval Artifact — schema

```json
{
  "schema_version": "1.0",
  "query_id": 27,
  "log_id": "20260417_121028",
  "original_query": "…",
  "produced_at": "2026-04-18T14:30:00Z",
  "produced_by": {
    "expander_domains": ["regulations", "cases"],
    "compliance_triggered": true,
    "compliance_trigger_refs": ["reg:a3f8c2d1-9b4e-4f7a-8c3d-2e1f0a9b8c7d"],
    "expander_prompt": "prompt_2",
    "reranker_model": "qwen3.5-flash"
  },
  "sub_queries": [
    {
      "i": 1,
      "domain": "regulations",
      "text": "شروط إثبات هجر الزوج",
      "rationale": "مباشر",
      "sufficient": true,
      "summary_note": "…"
    }
  ],
  "results": [
    {
      "ref_id": "reg:a3f8c2d1-9b4e-4f7a-8c3d-2e1f0a9b8c7d",
      "domain": "regulations",
      "source_type": "article",
      "title": "المادة التاسعة",
      "content": "…",
      "metadata": {
        "regulation_title": "نظام الأحوال الشخصية",
        "article_num": "9",
        "section_title": "الفصل الأول النفقة"
      },
      "relevance": "high",
      "reasoning": "…",
      "appears_in_sub_queries": [1, 2],
      "rrf_max": 0.22,
      "cross_references": []
    },
    {
      "ref_id": "case:b7e1d4f2-3a8c-4e9b-b2d5-1f0c8a7e6d3b",
      "domain": "cases",
      "source_type": "ruling",
      "title": "…",
      "content": "…",
      "metadata": {
        "court": "المحكمة العامة بالرياض",
        "case_number": "خ/1234/1445",
        "year": 1445
      },
      "relevance": "medium",
      "reasoning": "…",
      "appears_in_sub_queries": [1],
      "cross_references": [
        {"cites_ref_id": "reg:a3f8c2d1-9b4e-4f7a-8c3d-2e1f0a9b8c7d", "kind": "cites_as_precedent"}
      ]
    },
    {
      "ref_id": "compliance:a7f3c9e210e8d4b2",
      "domain": "compliance",
      "source_type": "gov_service",
      "title": "خدمة تقديم دعوى نفقة",
      "content": "…",
      "metadata": {
        "authority": "وزارة العدل",
        "url": "https://najiz.sa/.../nafaqa",
        "url_fetched_at": "2026-04-18T14:30:00Z"
      },
      "relevance": "high",
      "reasoning": "…",
      "appears_in_sub_queries": [],
      "triggered_by_ref_ids": ["reg:a3f8c2d1-9b4e-4f7a-8c3d-2e1f0a9b8c7d"],
      "cross_references": [
        {"cites_ref_id": "reg:a3f8c2d1-9b4e-4f7a-8c3d-2e1f0a9b8c7d", "kind": "implements"}
      ]
    }
  ],
  "dropped": [
    {"ref_id": "reg:…", "reason": "…"}
  ]
}
```

**Design notes:**
- `ref_id` is a **namespaced UUID string**: `{domain}:{db_uuid}`. Domain prefix gives cross-domain uniqueness; UUID is opaque to users and reveals no internal structure.
- Per-domain differences live in `metadata` — keeps the top-level shape identical across domains.
- `cross_references` is the answer to "regulation and case cite the same article" — soft link with a relationship kind, not a merge.
- `appears_in_sub_queries` preserves the granularity the per-sub-query rerankers already have — the router can see "this hit only came from q3" and decide accordingly. Compliance results always have `[]` here since they have no expander sub-queries; they use `triggered_by_ref_ids` instead.
- `triggered_by_ref_ids` (compliance only) — which reg/case `ref_id`s caused this compliance result to be fetched. Preserves the targeting chain for debugging and cross-reference rendering.
- `schema_version` from day 1 — any breaking change bumps to `2.0` and we write a migration.

## 4. Per-domain work

### reg_search (small, low-risk)
1. `search_pipeline.py` — thread the UUID `id` through candidate dicts from DB query to reranker input (it's already fetched, just need to stop dropping it)
2. `reranker.py` — change the reranker prompt to emit decisions keyed by UUID rather than positional index. Output parse becomes `{id: "...", action: keep, relevance: high}`
3. `logger.py` — add `save_unified_artifact(ura: UnifiedRetrievalArtifact)` that writes `reranker/ura.json` alongside the existing per-sub-query markdown files
4. Per-sub-query markdown stays (for debugging) — not a breaking change

**Estimated effort:** 1 day

### case_search (deferred — out of scope)
Cases require more planning and fine-tuning before wiring into the URA pipeline. The schema already accommodates it — `domain: "cases"`, UUID `ref_id`, domain-specific metadata (court, case_number, year, ruling_type) — so when it's ready, the input/output shape won't change significantly.

The `merge_partial_ura()` signature already accepts `cases: RerankerOutput | None` — passing `None` is the no-op path until case_search is brought in.

### compliance_search (dependent step — runs after partial URA)

**Not a parallel domain.** Compliance is never in the query expander. It runs after reg+cases finish, triggered by the partial URA.

**Input:**
```python
compliance_search(
    original_query: str,
    reg_hits: list[dict],  # [{"ref_id": "reg:uuid", "regulation_title": "...", "article_num": "9"}, ...]
)
```
Only titles and article numbers are passed — not full content. This keeps the compliance agent's input small (~5–10 tokens per reg hit) and avoids leaking URA internals into the compliance prompt.

**Decision logic:** the compliance agent decides which regulation hits warrant a service/form lookup. It may search for all of them, some of them, or none (if the query is purely theoretical with no procedural implication).

**Output:** URA-shaped results with:
- `ref_id` = `compliance:{sha1(canonical_url)[:16]}`
- `source_type` = one of `gov_service` | `scraped_document` | `form`
- `metadata.url` = the gov URL
- `triggered_by_ref_ids` = the reg `ref_id`s that caused this search
- `relevance` = `"high"` by default (scraping only returns hits that matched the query)
- No reranker needed

Reg/compliance overlap handled via `cross_references` as before.

**Estimated effort:** 1 day (input reshaping + trigger logic + existing output reshape)

### New module: `deep_search_v3/result_merger.py`
Two-stage merge, ~200 lines:

```python
def merge_partial_ura(
    reg: RerankerOutput | None,
    cases: RerankerOutput | None,
    query_context: QueryContext,
) -> PartialURA:
    """Stage 1: reg + cases only. Used to trigger compliance."""

def merge_to_ura(
    partial: PartialURA,
    compliance: SearchOutput | None,
) -> UnifiedRetrievalArtifact:
    """Stage 2: add compliance results, compute cross_references, final ordering.
    Dedup by ref_id. Order: relevance DESC, domain priority (reg > cases > compliance), rrf DESC."""
```

The two-stage split makes the pipeline explicit: partial URA exists as a named intermediate that triggers compliance, not just an internal implementation detail.

**Estimated effort:** 0.5 day

## 5. Downstream consumer changes

### Aggregator (small)
The aggregator is a **pure synthesis step** — it receives the complete URA and produces Arabic synthesis. No tool calls, no retrieval, no compliance lookups mid-synthesis. Everything the aggregator needs is in the URA before it starts.

- `log_parser.py` gains a URA loader. When `ura.json` exists in a run dir, skip the per-sub-query markdown path entirely
- `preprocessor.py` dedup becomes trivial — already uses tuple identity, now uses `ref_id` directly
- `models.py.Reference` gains `ref_id: str` and `domain: Literal[...]` fields
- `build_aggregator_user_message` renders domain-aware XML: `<reference n="4" domain="regulations" ref_id="reg:...">…` — gives the LLM domain awareness when synthesizing (e.g. CRAC section on "القاعدة النظامية" can distinguish regulations from case-precedent)

**Aggregator personalization:** the aggregator's deps get a `user_framing` field (e.g. `"lawyer_advising_client"` | `"client_asking_about_own_case"` | `"researcher"`). This gets injected into the system prompt via a small framing clause before the CoT section. The URA is constant — framing is the personalization vector.

**Estimated effort:** 1 day

### Router (new cache-hit path)
- Router gains a `cached_ura_lookup(query_embedding, threshold=0.92)` that checks recent URAs for similarity to the new query
- On cache hit: skip search loop entirely, pass cached URA to aggregator with fresh framing
- On partial hit (some sub-queries would match but others are new): invoke search with ONLY the uncovered sub-queries, merge into the existing URA

This is a substantial but separate feature — plan calls it out but defers actual implementation.

**Estimated effort (deferred):** 2–3 days

### Frontend side panel
Minimum viable:
1. Synthesis artifact already contains `references_json`; extend to include `ref_id` per reference
2. New endpoint: `GET /api/sources/{ref_id}` → returns the full source record from DB (or cached URA)
3. Frontend: citation `(4)` becomes clickable → opens side panel showing title, full content, metadata, URL (if compliance)
4. No follow-up chat at this stage — just display the source. Keep it simple, as you said.

**Estimated effort:** 1 day backend + 1 day frontend

## 6. Cross-domain overlap handling (your Q3 concern)

Compliance and reg overlap was the trickiest question. The answer falls out of the schema:

**Rule:** Each domain emits its own `ref_id`. If compliance scrapes a form that's also codified in reg, both are emitted as separate URA entries. The merger does a content-overlap detection pass:

```python
# Simplified
for comp_result in compliance_results:
    for reg_result in reg_results:
        if content_overlap_ratio(comp_result.content, reg_result.content) > 0.7:
            comp_result.cross_references.append(CrossRef(
                cites_ref_id=reg_result.ref_id,
                kind="codified_in_regulation"
            ))
```

The aggregator then renders them as separate citations but with a cross-reference note ("هذا النموذج مُقنَّن في المادة X، المرجع (N)"). Users see them as linked but distinct sources — which is faithful to the law: a form IS a different artifact than the regulation that defines it, even if they share text.

## 7. Migration plan

Strict gradualism, no big-bang:

**Phase 1 (week 1) — reg_search produces URA alongside existing markdown**
- reg_search outputs BOTH `reranker/ura.json` and the current per-sub-query markdowns
- Aggregator reads URA when present, falls back to markdown parsing otherwise
- Nothing in production behavior changes yet — this is additive

**Phase 2 (week 2) — compliance as dependent step, merger handles all 3 domains**
- result_merger gains `merge_partial_ura()` (stage 1: reg+cases → partial URA)
- compliance_search wired to receive original query + reg_hits from partial URA
- compliance emits URA-shaped output with `triggered_by_ref_ids`
- result_merger `merge_to_ura()` (stage 2: partial URA + compliance → full URA)
- Aggregator consumes full URA

**Phase 3 (deferred) — case_search conform**
- Out of scope until case_search planning and fine-tuning is complete
- When ready: UUID as ref_id, same pattern as reg_search; `merge_partial_ura()` already accepts the slot

**Phase 4 (week 4) — side panel UX**
- Frontend `/api/sources/{ref_id}` + side panel component
- Synthesis artifact stores `ref_n → ref_id` map so side panel can resolve numbered citations

**Phase 5 (deferred, when there's real traffic) — router cache-hit**
- URA-based query cache, partial-hit re-search

**Legacy cleanup:** Once URA is the primary path end-to-end (end of phase 3), the per-sub-query reranker markdowns become debug-only. We can flip a flag to stop writing them by default, keeping a `--legacy-markdown` opt-in.

## 8. Schema versioning

- Every URA has `"schema_version": "1.0"` from day one
- Changelog file: `agents/deep_search_v3/_ura_schema_changelog.md`
- Additive changes (new optional field) keep `1.0` and append to changelog
- Breaking changes (rename, type change, required field added) bump to `2.0` + write an on-read migration for old URAs in logs
- Aggregator + router refuse to load URAs with newer major version than they know — fail loud, don't silently misparse

## 9. Added complexity — honest delta

| Dimension | Before | After |
|---|---|---|
| Files changed | — | ~12 files edited, 3 new |
| New contract | implicit per-domain MD | explicit JSON shared across 3 producers + 2 consumers |
| Downstream simplicity | aggregator does dedup + normalize | aggregator reads canonical stream |
| New capability | — | **side-panel source lookup, cache-hit router** |
| Migration effort | — | 4 weeks gradual, no breaking moment |
| Schema governance | — | yes (version + changelog) |
| Token cost reduction | — | **none** — separate project (Option C lives in a future backlog) |

## 10. Open questions (answered)

1. **DB ID stability** — resolved: use UUID `id`. DB is ingested once — UUIDs are stable. `chunk_ref` stays in DB and may appear in metadata for agent reasoning but is never the primary handle (would expose chunking strategy).
2. **Source follow-up UX** — side panel only, no follow-up chat in v1
3. **Compliance position** — not a parallel domain, not a tool call inside the aggregator. Runs as a dependent step after reg+cases partial URA is assembled. Input is original query + reg hit titles/article nums (not full content). Output merges into full URA before aggregator starts.
4. **Cross-domain dedup** — keep separate with optional cross-reference metadata
5. **Migration** — 4-phase gradual, additive, no breaking moments
6. **URA purpose** — hidden artifact for aggregator + router (content layer), constant across users
7. **Schema versioning** — semver from day 1, changelog file

## 11. Build sequencing (when approved)

### Scope
**In scope:** reg_search → partial URA → compliance (dependent step) → full URA → aggregator
**Out of scope:** case_search (passes `None` everywhere), router cache, frontend side panel

---

### Step 1 — UUID plumbing through reg_search (1 day)

**`reg_search/models.py`**
- Add `db_id: str = ""` to `RerankedResult` — carries the DB UUID from search through reranker

**`reg_search/search_pipeline.py`**
- Thread `id` (UUID) from the Supabase row dict into every candidate dict before passing to reranker

**`reg_search/reranker.py`**
- After reranker decisions are applied, copy `db_id` from the candidate into the assembled `RerankedResult`
- No change to reranker prompt — UUID is carried by code, not emitted by LLM

---

### Step 2 — URA schema + partial merger (0.5 day)

**NEW `deep_search_v3/_ura_schema.py`**
```python
@dataclass
class URAResult:
    ref_id: str          # "reg:{uuid}" or "compliance:{url_sha1_first16}"
    domain: str          # "regulations" | "compliance"
    source_type: str     # "article" | "section" | "gov_service" | "form"
    title: str
    content: str
    metadata: dict       # regulation_title, article_num, url, authority, etc.
    relevance: str       # "high" | "medium"
    reasoning: str
    appears_in_sub_queries: list[int]   # reg only; [] for compliance
    rrf_max: float
    triggered_by_ref_ids: list[str]     # compliance only; [] for reg
    cross_references: list[dict]

@dataclass
class PartialURA:
    schema_version: str = "1.0"
    query_id: int = 0
    log_id: str = ""
    original_query: str = ""
    produced_at: str = ""
    sub_queries: list[dict] = field(default_factory=list)   # expander output summary
    results: list[URAResult] = field(default_factory=list)  # reg only at this stage

@dataclass
class UnifiedRetrievalArtifact:
    schema_version: str = "1.0"
    query_id: int = 0
    log_id: str = ""
    original_query: str = ""
    produced_at: str = ""
    produced_by: dict = field(default_factory=dict)
    sub_queries: list[dict] = field(default_factory=list)
    results: list[URAResult] = field(default_factory=list)  # reg + compliance merged
    dropped: list[dict] = field(default_factory=list)
```

**NEW `deep_search_v3/result_merger.py`**
```python
def merge_partial_ura(
    reg_reranker_results: list[RerankerQueryResult],
    original_query: str,
    query_id: int,
    log_id: str,
) -> PartialURA:
    """Convert reg reranker output to PartialURA. cases=None passthrough by design."""

def merge_to_ura(
    partial: PartialURA,
    compliance: ComplianceURASlice | None,
) -> UnifiedRetrievalArtifact:
    """Add compliance results to partial URA. Dedup by ref_id, add cross_references."""
```

---

### Step 3 — Compliance as dependent step (1 day)

**`compliance_search/models.py`**
- Add `triggered_by_ref_ids: list[str] = field(default_factory=list)` to `Citation`
- Add new dataclass `RegHit` for passing reg context: `ref_id`, `regulation_title`, `article_num`

**NEW entry point in `compliance_search/loop.py` (or new `compliance_search/ura_runner.py`)**
```python
async def run_compliance_from_partial_ura(
    original_query: str,
    reg_hits: list[RegHit],   # titles + article_nums only, no full content
    deps: ComplianceSearchDeps,
) -> ComplianceURASlice:
    """Compliance search triggered by partial URA.
    LLM receives: original query + list of regulation titles/articles.
    LLM decides which regulations warrant a service/form lookup.
    Returns URA-shaped compliance results with triggered_by_ref_ids populated."""
```

The existing `run_compliance_search()` (standalone loop) stays untouched — this is a new entry point alongside it.

---

### Step 4 — Aggregator reads URA (1 day)

**`aggregator/models.py`**
- Add `ref_id: str = ""` and `domain: Literal["regulations", "compliance"] = "regulations"` to `Reference`
- Type `compliance_results` properly: `ComplianceURASlice | None = None`

**`aggregator/preprocessor.py`**
- Add `_compliance_identity_key(citation: Citation) -> tuple` — keyed by `(source_type, ref)` since compliance has no regulation_title/article_num
- Extend `preprocess_references()` to also flatten `agg_input.compliance_results` citations into the reference list after reg results
- Compliance references get `domain="compliance"` on the `Reference`

**`aggregator/log_parser.py`**
- Add `load_aggregator_input_from_ura(ura: UnifiedRetrievalArtifact) -> AggregatorInput` — converts URA results back into the `sub_queries` + `compliance_results` slots that `AggregatorInput` expects
- Existing markdown-based `load_aggregator_input_from_run()` stays as fallback

---

### Step 5 — Orchestrator + test runner (0.5 day)

**NEW `deep_search_v3/full_loop_runner.py`**
```python
async def run_full_loop(
    query: str,
    query_id: int,
    deps: FullLoopDeps,
) -> AggregatorOutput:
    # 1. reg_search
    reg_result = await run_reg_search(...)
    # 2. partial URA
    partial = merge_partial_ura(reg_result.reranker_results, ...)
    # 3. compliance (dependent)
    reg_hits = [RegHit(ref_id=r.ref_id, ...) for r in partial.results if r.relevance == "high"]
    compliance = await run_compliance_from_partial_ura(query, reg_hits, ...)
    # 4. full URA
    ura = merge_to_ura(partial, compliance)
    # 5. aggregator
    agg_input = load_aggregator_input_from_ura(ura)
    return await run_aggregator(agg_input, ...)
```

---

### Step 6 — Shadow validation gate

Run `full_loop_runner` against the existing 7 logged queries from the test matrix.
For each query, assert:
- Same regulation `ref_id`s cited (set equality, order may differ)
- No new hallucinated citations
- Compliance results appear where expected (queries with procedural implications)
- Compliance results absent where not needed (purely theoretical queries)

Nothing ships to production until all 7 pass.

---

### File manifest

| File | Status | Change |
|---|---|---|
| `deep_search_v3/_ura_schema.py` | NEW | URAResult, PartialURA, UnifiedRetrievalArtifact |
| `deep_search_v3/result_merger.py` | NEW | merge_partial_ura(), merge_to_ura() |
| `deep_search_v3/full_loop_runner.py` | NEW | end-to-end orchestrator |
| `reg_search/models.py` | EDIT | add db_id to RerankedResult |
| `reg_search/search_pipeline.py` | EDIT | thread UUID through candidate dicts |
| `reg_search/reranker.py` | EDIT | copy db_id into assembled RerankedResult |
| `compliance_search/models.py` | EDIT | add triggered_by_ref_ids, RegHit dataclass |
| `compliance_search/ura_runner.py` | NEW | run_compliance_from_partial_ura() |
| `aggregator/models.py` | EDIT | ref_id + domain on Reference, type compliance_results |
| `aggregator/preprocessor.py` | EDIT | compliance identity key + flatten compliance refs |
| `aggregator/log_parser.py` | EDIT | add load_aggregator_input_from_ura() |

**Cases throughout:** `cases: None` — `merge_partial_ura()` accepts but ignores, `AggregatorInput.case_results` stays `None`. No case files touched.

**Total estimated effort: 4 days**
