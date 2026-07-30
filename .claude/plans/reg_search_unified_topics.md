# reg_search → Unified Topic Search (absorbs compliance) — Build Plan

> **Goal:** reg_search becomes the single retrieval executor over the unified
> `public.search_topics` layer (4 source types: regulation, appendix, circular,
> service). The compliance executor is **deleted** — its planner mode, phase,
> package, slots, and telemetry. `search_chunk_titles()` becomes legacy.
>
> **Upstream reference (source of truth for the data layer):**
> `C:\Programming\agentic_for_ministry\ingestion\search_topics\REFERENCE.md`
> Live-verified 2026-07-15 on project `dwgghvxogtwyaxmbgjod`:
> regulation 123,022 topics / 3,373 regs · appendix 9,527 / 980 regs ·
> circular 2,119 / 1,843 docs · service 6,217 / 4,420 docs. All 100% embedded.

---

## 0. Locked decisions (from the reflection Q&A, 2026-07-15)

| # | Decision |
|---|----------|
| D1 | **No per-type enforcement downstream.** RPC fetch `p_per_type=15` (all 4 types), merge into ONE pool, global top-15 by similarity to the reranker. The type mix is whatever similarity says. Reranker has no type quotas/caps. |
| D2 | **Reranker circular block < 1,000 chars total** (title + entity + first 200 chars of content). Entity name included in circular AND service blocks at every stage. |
| D3 | **Sector filter moves into the RPC** (`p_sectors`), compliance-style: await `sectors_future` (bounded grace) BEFORE the RPC; retry once unfiltered on 0 rows; NEVER pass `[]` (pass `None` — `sectors && '{}'` is always false). |
| D4 | **`search_chunk_titles` = legacy.** All retrieval through `search_topics()`. reg_search/search.py is the only caller — this cutover covers it. |
| D5 | **Circular rendering built new, mimicking the service pattern** (new URA domain + resolvers + frontend meta). |
| D6 | **Post-soak DB drop** (coordinated with ministry project): `regulation_v2.chunk_titles` (table+HNSW), `public.chunk_titles_v2` view, `search_chunk_titles()`, `services.embedding` + `idx_svc_embedding`, `hybrid_search_services()`. |
| D7 | **Planner modes:** `compliance_led` deleted → `case_led | reg_compliance_led | full` (mode renamed per D16). `reg_compliance_led` support = **cases** (new). `case_led` support = reg_compliance (unchanged role). `full` = reg_compliance + cases co-primaries. |
| D8 | **Accept the RPC's `DISTINCT ON (doc_id)`** — one item per doc per type per sub-query. Coverage across one law comes from multiple sub-queries + precise-mode prev/next unfolding. Old client dedup code dies (the RPC does it server-side — verified in live function body). |
| D9 | **New `"circulars"` URA domain + `CircularURAResult`.** Services keep the existing `"compliance"` domain — zero downstream churn for services. |
| D10 | **Service three-view unfold** ("mimic its neighbors"): reranker = `service_context`; aggregator = `intro_description` + `steps` + `requirements` + `required_documents`; user = `service_context` only. |
| D11 | **Circular three-view:** reranker = title + entity + first 200 chars (<1k block); aggregator = full content **capped 4,000 chars** (p90=3,958; max outlier 168,782); user = **full content**, uncapped. |
| D12 | **Precise/simple banding over CHUNK rows only** — top-5 chunk rows get precise (prev/next context); circulars/services are flat views regardless of rank, and never consume a precise slot. |
| D13 | `corpus` added to `CHUNK_SELECT` — appendix chunks get a (ملحق) label in reranker/aggregator/user views. |
| D14 | Expansion prompt: **light addition only** (corpus now includes regulations+appendixes, circulars, gov services). Same tone, same output shape, same two-angle rule, no cap. The compliance expander's need-inference philosophy is NOT ported. PLUS two light prohibitions (see §3a): (a) no document-type labels (نظام/لائحة/تعميم/خدمة) in queries — topics are subject-phrased, not container-phrased; (b) no entity/platform names even if the user named them, UNLESS the rule sought applies to the entity itself (✅ «تنظيم وزارة التجارة» ❌ «إجراءات السجل التجاري وفقاً لوزارة التجارة»). |
| D15 | Aggregator: `prompt_mode_reg` absorbs a **~50%-condensed** `prompt_mode_compliance`; the compliance key is deleted; `prompt_mode_full` updated. |
| D16 | **The merged loop is renamed `reg_compliance`** — package, entry points, slots, spans, ledger labels, mode name (see §3i rename map). URA *domains* are data-level identities and do NOT rename (`regulations` / `compliance` / `circulars` / `cases` stay). |
| D17 | **No sectors in ANY reranker block** (all 4 types). Chunk blocks never had them (`نطاق النظام` is prose scope, not the sector taxonomy — it stays); the القطاع line is dropped from the ported service block and omitted from the new circular block; the merged reranker prompt's input-field list does not mention sectors. Sectors remain in row/URA *metadata* (filtering + downstream) — they're just never rendered as LLM context for the reranker. |

---

## 1. THE VIEW MATRIX — what each stage sees, per source type

> The centerpiece contract. "Reranker" = the markdown block graded per
> candidate. "Aggregator" = the reference content block in `<references>`.
> "User" = source viewer / ReferencePanel via `references_service`.

### 1a. Regulation chunk (`source_type='regulation'`) — UNCHANGED except score source

| Stage | Sees |
|---|---|
| **Reranker (precise, top-5 chunk rows)** | `### [Cn] {chunk title}` · **النظام:** {reg name} · **نطاق النظام:** {scope ≤1500} · **درجة الصلة:** {score} · **سياق المقطع السابق** (≤800) · **سياق المقطع الحالي** (≤800) · **ملخص المقطع** (≤2000) · **سياق المقطع التالي** (≤800) |
| **Reranker (simple, remaining chunk rows)** | `### [Cn] {chunk title}` · النظام · نطاق النظام · درجة الصلة · **ملخص المقطع** only |
| **Aggregator** | Full `chunk_content` markdown (enricher-built aggregator view) + resolved cross_refs — unchanged |
| **User** | `ChunkSourceView` — unchanged |

Only change: `_rrf` now = RPC `score` directly (same 1-cosine scale; the old
`sim = 1 - distance` computation dies).

### 1b. Appendix chunk (`source_type='appendix'`) — same machinery + label + NULL-context guard

| Stage | Sees |
|---|---|
| **Reranker** | Identical block shape to regulation chunks (same `chunks_v2` unfold, prev/next chains are appendix-local and safe) **plus a (ملحق) tag in the header** derived from `corpus='appendix'`. ⚠ `context` is **NULL by design** for appendix chunks → precise mode must degrade gracefully: render summary; skip/blank the سياق lines (never crash, never "None"). |
| **Aggregator** | Same as regulation chunk + (ملحق) tag next to the reg name |
| **User** | `ChunkSourceView` + (ملحق) label |

`doc_id` = parent regulation → citations attribute to the regulation, correctly.

### 1c. Circular (`source_type='circular'`) — NEW

| Stage | Sees |
|---|---|
| **Reranker** | `### [Cn] تعميم: {title}` · **الجهة:** {entity name} · **درجة الصلة:** {score} · first **200 chars** of `content` + `...` — whole block **< 1,000 chars**. No sectors (D17), no link (reranker ignores links). |
| **Aggregator** | `تعميم: {title}` · الجهة · full `content` **capped at 4,000 chars** with an Arabic truncation marker (`… [اقتُطع النص]`) |
| **User** | **Full content, uncapped** (new `CircularSourceView`, mimicking the service view pattern; include `source` link if present). Long-doc note: max circular is 168k chars — panel must scroll, not choke. |

Entity name: one batched `public.entities` lookup by `entity_ref` (same
pattern the service branch of the RPC uses).

### 1d. Service (`source_type='service'`) — reranker as today, aggregator RICHER, user as today

| Stage | Sees |
|---|---|
| **Reranker** | Ported compliance block (`_format_service_block`) **minus the القطاع line (D17)**: `### [Cn] خدمة: {service_name_ar} [ref]` · **الجهة:** {provider_name} · **RRF/درجة الصلة:** {score} · `service_context` (≤600 chars) · **الرابط** |
| **Aggregator** | **NEW rich view:** `خدمة: {intro_title ∥ service_name_ar}` · **الجهة:** {provider_name} · {intro_description} · **الخطوات:** {steps[] numbered} · **المتطلبات:** {requirements[]} · **المستندات المطلوبة:** {required_documents[]} · **الرابط:** {service_url ∥ url} — replaces today's `service_context` pass-through |
| **User** | `service_context` only + link (existing `ServiceSourceView`, unchanged) |

All service columns fetched in ONE batched `services` select at search time
(`search_topics()` returns no content columns).

---

## 2. Search loop rewrite — `agents/deep_search_v4/reg_search/search.py`

Architecture survives (semaphore, batch embeddings, `asyncio.to_thread`, SSE
events, fail-soft `([], 0)`, no absolute score gate). Steps 2–6 rewritten:

1. **Embed** — unchanged (precomputed batch path stays).
2. **Sectors BEFORE the RPC** *(moved up from step 6)* — `resolve_sector_filter(sectors_future)` with existing grace; result `None` or non-empty list.
3. **RPC** — `_search_topics_rpc(supabase, embedding, sectors)`:
   `search_topics(p_query_embedding, p_types=NULL /*all 4*/, p_per_type=15, p_sectors=sectors or None)`.
   Constants: `PER_TYPE = 15` replaces `MATCH_COUNT=150` / `EF_SEARCH=150`
   (RPC self-tunes `hnsw.ef_search` from `p_overfetch`; the old truncation
   trap is gone). **0 rows + sectors set → retry once with `p_sectors=None`**
   (+ SSE "بدون تصفية" status, as today).
4. **Merge** — sort ALL returned rows by `score` DESC, cut **top-15 overall**
   (D1). Optional one-line `seen source_id` guard (RPC already guarantees
   uniqueness per call). Old title-dedup loop DELETED (D8).
5. **Per-type content fetch — concurrent (`asyncio.gather`), ONE shared
   mechanism**: generalize `_fetch_chunks` into `_fetch_by_ids(table, columns,
   ids)` and call it 3× with per-type column constants. The column lists are
   payload trims (never `select("*")` — it would drag `services.embedding`
   [1024-d vector], `original_markdown`, `fts`), not per-type logic:
   - regulation+appendix → `chunks_v2`, `CHUNK_SELECT + ", corpus"` (D13)
   - circular → `circulars`: `id, circ_ref, title, content, entity_ref, source`
     (+ entity name: embed `entities(name)` in the same select if the FK
     allows, else one batched `entities` lookup — D2)
   - service → `services`: `id, service_ref, service_name_ar, provider_name,
     service_context, intro_title, intro_description, steps, requirements,
     required_documents, service_url, url` (one fetch feeds all three views, D10)
   Circular/service rows are one-hop flat (the row IS all three views);
   only the chunk path keeps its extra unfold hops (regulations_v2 name/scope,
   prev/next contexts in precise mode) — unchanged from today.
6. **Band + tag** — `_mode`: precise for top-5 **chunk** rows only (D12);
   `simple` for other chunks; `flat` for circular/service. `_rrf` = `score`.
   Every row carries `source_type` forward.
7. **Return** — same contract. SSE topic text broadened:
   `جاري البحث في الأنظمة والتعاميم والخدمات: …`.

DELETE: `_search_chunk_titles`, `_filter_by_sectors` (+ its `regulations_v2`
hop), the best_sim dedup block, `MATCH_COUNT`/`EF_SEARCH`. Update module
docstring + REG_SEARCH_V2_REFRAME references.

---

## 3. Per-agent changes

### 3a. Expander — `reg_search/expander.py`, `prompts.py::EXPANDER_PROMPTS`
- **Prompt (light touch, D14):** in the scope-of-search section, replace the
  chunk-titles wording with: queries are matched by meaning against **abstract
  topics** covering **regulations and their appendixes, circulars (تعاميم),
  and government services (خدمات حكومية)** — you are responsible for both the
  regulatory and the compliance/services ground. Same tone, same two-angle
  rule, same 2–10 output, no cap.
- **Two light prohibitions**, appended to the existing «Two mandatory
  conditions» section (`prompts.py:155-158`), matching its tone, one ❌/✅
  pair each (style of the article-number section):
  1. **No document-type labels.** Topics label content by its SUBJECT, never
     by its container — no topic is shaped like «نظام العمل», «تعميم بخصوص
     كذا», or «خدمة إصدار كذا». Queries therefore never lean on the words
     نظام / لائحة / تعميم / خدمة as labels.
     ❌ «تعميم بخصوص إبلاغ العاملين عن المخالفات»
     ✅ «إلزام المنشآت بنشر آليات إبلاغ العاملين عن المخالفات»
  2. **No entity or platform names** (وزارة، هيئة، أبشر، ناجز…) in any query
     — **even when the user named them** — UNLESS the rule sought applies to
     the entity itself (its own organization/competencies). This STRENGTHENS
     existing condition 2 (which only barred authorities the user hadn't
     mentioned).
     ✅ «تنظيم وزارة التجارة واختصاصاتها»
     ❌ «إجراءات السجل التجاري وفقاً لوزارة التجارة»
- The deleted compliance expander's «خدمة تتيح…» phrasing template must NOT
  leak into the merged prompt — it is exactly the container-phrasing that
  prohibition 1 bans (topics are subject-phrased).
- Slot `reg_search_expander` (_FLASH_MEDIUM) unchanged (renamed per §3i).
- `compliance_search_expander` slot + prompt DELETED (not merged).

### 3b. Reranker — `reg_search/reranker.py`, `unfold_reranker.py`, `prompts.py::RERANKER_PROMPTS`
**Mechanism untouched:** per-sub-query parallel LLM calls, keep-only contract
(emit keeps only, drop by difference), relevance high/medium, `query_axes` /
`satisfies_axes` / `weak_axes`, flat `max_keep` cap (high first, then `_rrf`),
TextOutput JSON salvager, zero-keep reconsider retry, slot
`reg_search_reranker` (tier_2).

**unfold_reranker.py:**
- `format_chunk` unchanged; add (ملحق) header tag when `corpus='appendix'`;
  NULL-context guard for appendix precise blocks (§1b).
- ADD `_format_circular_block` (per §1c, <1k chars, no sectors — D17).
- PORT `_format_service_block` from `compliance_search/unfold_reranker.py`
  minus its القطاع line (per §1d, D17).
- `build_reranker_user_message`: dispatch block builder on `source_type`.

**Prompt (scope extended, mechanism identical):**
- Input description: candidates are now three block kinds — **مقطع نظام**
  (chunk, incl. ملحق-tagged), **تعميم** (title + entity + 200-char snippet),
  **خدمة حكومية** (service_context block). The field list mentions NO sectors
  for any kind (D17).
- Mandatory first step extended per kind: chunks → النظام/نطاق النظام scope
  check as today (verbatim); circulars/services → **الجهة check** (does the
  issuing entity / provider govern the query's matter?).
- Condensed compliance gates appended for service candidates: ON-ACT +
  OPERATIVE, "ancillary/استعلام/حجز موعد services are never `high`".
- Granularity wording: scope only — same two-gate test, same keep-only rules.

### 3c. URA layer — `agents/deep_search_v4/ura/`
- `schema.py`: `Domain` += `"circulars"`; new `CircularURAResult`
  (`ref_id="circular:{uuid}"`, circ_ref, title, entity_name, content
  [aggregator-capped 4k], source_url, sectors, relevance, reasoning,
  `for_aggregator()` / `for_reference()` projections). Discriminated union +=.
- `reg_adapter.py::reg_to_rqr` becomes **type-aware**: routes each kept row by
  `source_type` → `RegURAResult` (regulation/appendix, + appendix flag) /
  `CircularURAResult` / `ComplianceURAResult` (services keep the existing
  domain, D9).
- NEW `services_unfold.py` (or fold into reg unfold): aggregator-view builder
  from `intro_description+steps+requirements+required_documents` (D10);
  `build_ura_metadata` ported from `compliance_unfold.py`.
- `compliance_adapter.py`, `compliance_unfold.py` DELETED after the port.
- `merger.py` / `enrich.py`: handle circulars domain (content carried at
  adapter time like services — no enrichment fetch needed); appendix flag
  passes through reg enrichment untouched.

### 3d. Aggregator — `aggregator/prompts.py`, `preprocessor.py`, `models.py`
- `prompt_mode_reg`: absorb ~50%-condensed compliance instructions (D15) —
  how to weave service/circular references into the synthesis (procedural
  steps cited as `[n]`, services as executive pathways, circulars as
  entity-level directives), keep IRAC structure + all shared constraints.
- `prompt_mode_full`: reg+cases co-primaries; reg references may include
  services/circulars — same condensed guidance.
- `prompt_mode_compliance` key DELETED (+ its tests).
- `preprocessor._reference_from_ura`: add `CircularURAResult` branch
  (Reference: domain "circulars", source_type "circular", title, entity_name,
  snippet from capped content). Service branch unchanged.
- `AggregatorItem`: add `circular_title` / `circular_content` / `entity_name`
  fields (mirror service fields); `render_aggregator_content` dispatches on
  them; service content now rendered from the rich builder (§1d).

### 3e. Planner — `planner/models.py`, `prompts.py`, `apply.py`
- `Mode = Literal["case_led", "reg_compliance_led", "full"]` (D7 + D16).
- Prompt: delete the `compliance_led` block; **extend the (renamed)
  `reg_compliance_led` description** to absorb its territory — the executor
  now also retrieves e-services, official procedures, forms, and circulars
  (ناجز/أبشر/قوى… examples move here); it stays the default ("when in doubt,
  reg_compliance_led").
- Support rules: `case_led`+support→reg_compliance (unchanged role);
  `reg_compliance_led`+support→**cases** (new); `full` = both, ignores support.
- `MODE_PROFILES`: remove `compliance_led` entry; executor keys per §3i
  (`"reg_compliance"`, `"cases"`); aggregator prompt keys =
  {reg_compliance, case, full}.
- `build_retrieval_config`: drop `include_compliance`; `include_reg` →
  `include_reg_compliance` (§3i).
- Docstrings (models.py:1-42) updated.

### 3f. Orchestrator — `deep_search_v4/orchestrator.py`
- DELETE `_run_compliance_phase` (515–683), compliance imports (45–58, 76),
  `include_compliance`, `compliance_max_keep`, `result_budget["compliance"]`,
  the gather branch, and the compliance arm of `build_ura_from_phases`.
- `_PHASE_TEXT_AR`: remove "compliance"; reg text →
  `اكتمل البحث في الأنظمة والتعاميم والخدمات`.
- Spans: `deep_search.phase.compliance[.skipped]` gone. Ledger labels
  `deep_search.expansion.compliance` / `deep_search.reranker.compliance` gone
  (historical `llm_calls` rows untouched — no data migration, no repricing).

### 3g. Compliance package deletion — `agents/deep_search_v4/compliance_search/`
Whole package (loop, models, search, reranker, expander, prompts,
unfold_reranker, logger, cli, tests) DELETED **after** porting:
`_format_service_block`, the condensed reranker gates (→ reg prompt),
`build_ura_metadata`, and the service-block reranker test fixtures
(→ reg_search tests). The dead Jina code dies with it — nothing ported.

### 3h. Model slots — `agents/utils/agent_models.py`
- DELETE `compliance_search_expander` (213), `compliance_search_reranker` (214).
- reg slots RENAMED per §3i (`reg_search_expander` → `reg_compliance_expander`,
  `reg_search_reranker` → `reg_compliance_reranker`, `reg_search_aggregator` →
  `reg_compliance_aggregator`); tiers/policies unchanged; prune compliance
  references in comments.

### 3i. Naming — the merged loop is `reg_compliance` (D16)

> All plan sections above cite CURRENT paths/names as source locations; the
> rename lands in Wave 2 (same commit window as the planner/orchestrator
> edits, which touch every import site anyway).

**Rename map (old → new):**

| Kind | Old | New |
|---|---|---|
| Package | `agents/deep_search_v4/reg_search/` | `agents/deep_search_v4/reg_compliance_search/` |
| Graph/entry | `reg_search_graph`, `run` via `_run_reg_phase` | `reg_compliance_graph`, `_run_reg_compliance_phase` |
| State/deps | `RegLoopState`, `RegSearchDeps` | `RegComplianceLoopState`, `RegComplianceSearchDeps` |
| Adapter | `reg_to_rqr` (type-aware per §3c) | `reg_compliance_to_rqr` |
| Model slots | `reg_search_expander` / `_reranker` / `_aggregator` | `reg_compliance_expander` / `_reranker` / `_aggregator` |
| Planner mode | `reg_led` | `reg_compliance_led` (prompt text updated to match; still the default mode) |
| Mode profile executor key | `"reg"` in `MODE_PROFILES` executor sets | `"reg_compliance"` |
| Retrieval flag | `include_reg` | `include_reg_compliance` |
| Aggregator prompt key | `prompt_mode_reg` | `prompt_mode_reg_compliance` |
| Logfire span | `deep_search.phase.reg[.skipped]` | `deep_search.phase.reg_compliance[.skipped]` |
| Ledger labels | `deep_search.expansion.reg`, `deep_search.reranker.reg` | `deep_search.expansion.reg_compliance`, `deep_search.reranker.reg_compliance` |
| SSE phase literal (frontend `types/index.ts:968` + progress steps) | `"reg"` | `"reg_compliance"` |
| Log dirs / run ids (reg_search logger) | `reg_*` prefixes | `reg_compliance_*` |

**Deliberately NOT renamed (data-level identities & source types):**

- URA `Domain` values: `"regulations"`, `"compliance"`, `"circulars"`,
  `"cases"` — stored in `workspace_item_references.domain` on historical rows;
  downstream resolvers, `DOMAIN_META`, and old conversations depend on them.
  The loop name and the source domains are different namespaces.
- `RegURAResult` / `ComplianceURAResult` / `CircularURAResult` — named after
  source types, not the loop.
- `chunks_v2` / content tables / RPC names — upstream-owned.
- `sector_picker`, `case_search`, aggregator/planner slot names — untouched.

**Ledger continuity note:** slot + stage labels change at the rename date.
Historical `llm_calls` rows keep `reg_search_*` / `deep_search.*.reg` labels.
`scripts/cost_for_day.py`, `/model-consumption`, `/validate-calls`, and any
Logfire dashboards that group by slot/span must treat old and new labels as
one series (add an alias map, or accept the split at the cutover date —
plan says: add the alias map to the reprice/consumption scripts in Wave 4).

---

## 4. Downstream (citations / API / frontend)

### 4a. `agents/tool_repository/unfold_workspace_item.py`
- ADD `_resolve_circulars(...)`: batch-fetch `circulars` by item_id, render
  `[n] تعميم: {title} — {entity}`; register `"circulars"` in the `by_domain`
  dispatch (silent-skip trap at :343-354). `_resolve_compliance` unchanged.

### 4b. `backend/app/services/references_service.py`
- ADD `_build_circular_shells(...)` mimicking `_build_compliance_shells`
  (:302-354): batch-fetch `circulars` (full content — user view, D11),
  reconstruct `CircularURAResult` shells; register the domain in
  `fetch_item_references`. `workspace_item_references.domain` is free TEXT —
  **no migration needed**.

### 4c. Source viewer — `source_viewer.py`
- ADD `CircularSourceView` (title, entity_name, full content, source link) to
  the SourceView union. Service view unchanged.

### 4d. Frontend
- `types/index.ts`: `ReferenceDomain` += `'circulars'`; `ReferenceSourceType`
  += `'circular'`; SSE `phase` type (:968) → `"reg" | "case"`.
- `ReferencePanel.tsx`: `DOMAIN_META` += circulars entry (label **تعميم**,
  icon, tint — styled like compliance); `SourceViewContent` circular case =
  full-content scrollable body + link (mimic gov_service).
- deep_search progress bar step config: remove the compliance phase step
  (session chip + step tracker); reg step label broadened to match §3f text.
- `chat-store.ts` comments (:145, :815) updated.

### 4e. Monitor / telemetry consumers
- `deep_search_v4/monitor/run_monitor.py`: strip compliance mirroring/stats.
- `/convo-monitor`, `/validate-calls`, reprice scripts: read-only over
  historical labels — no changes; old rows keep their compliance labels.

---

## 5. Tests

| Action | Files |
|---|---|
| UPDATE | `planner/tests/test_apply_modes.py` (drop compliance cases; add reg_led+cases support, full=reg+cases), `test_aggregator_prompt_keys.py`, `aggregator/tests/test_postvalidator_mode_prompts.py`, `tests_ura/test_full_loop.py`, `test_run_retrieval.py`, `test_planner_models.py` |
| DELETE | `compliance_search/tests/*`, `tests_ura/test_compliance_ura.py` |
| ADD | reg_search search-layer tests (merge/top-15/sector-param/retry-unfiltered/`[]`-guard); reranker block tests for circular + service + appendix-NULL-context fixtures (port compliance fixtures); `CircularURAResult` round-trip (adapter → preprocessor → reference → shells); aggregator multi-domain reference assembly (chunk+appendix+circular+service in one synthesis) |
| PRE-STEP | The uncommitted keep-only calibration work (341 tests) is the base — **commit it first** (Wave 0) so this plan lands on a clean tree. |

---

## 6. Sequencing

- **Wave 0 — base:** commit the pending keep-only reranker calibration work.
- **Wave 1 — retrieval core:** search.py rewrite (§2) + per-type fetches +
  block builders (§3b unfold) + reranker prompt. reg_search returns 4 types
  end-to-end. (Compliance executor still wired — do NOT deploy this wave
  alone or services surface twice.)
- **Wave 2 — pipeline & prompts:** URA circulars domain + type-aware adapter
  + aggregator prompt merge + planner mode deletion + orchestrator phase
  deletion + **the §3i rename sweep** (package, slots, mode, spans, ledger
  labels, frontend phase literal). Waves 1+2 deploy **together**.
- **Wave 3 — downstream:** unfold_workspace_item + references_service +
  source viewer + frontend types/panel/progress steps.
- **Wave 4 — deletion sweep & tests:** compliance package, slots, monitor,
  test updates per §5.
- **Wave 5 — verify & soak:** smoke queries hitting each type (a pure-reg
  question, a services question e.g. «كيف أسجّل…», a circular-flavored
  entity directive, an appendix-heavy reg); Logfire trace check
  (`deep_search.phase.reg` only), `/validate-calls` on one turn; confirm
  citations render for all 4 types. After soak → **D6 legacy drop**
  (ministry-side coordination; frees ~1 GB).

## 7. Risks / notes

- **Latency:** the RPC now waits on the sector grace window instead of
  overlapping it (compliance precedent, bounded); 4-branch RPC ≈ same order
  as one HNSW call; content fetches gathered concurrently.
- **Appendix NULL `context`** — the one real crash-shaped edge; guarded in §1b.
- **Circular outlier (168k chars)** — capped at aggregator (4k); user view
  full-content must lazy-render/scroll.
- **Service title duality** — display uses `intro_title ∥ service_name_ar`
  (same COALESCE as the RPC).
- **Old conversations** — historical ledger/Logfire/workspace rows keep
  compliance labels & domain; all readers are label-agnostic; no migration.
- **Router untouched** — it dispatches `agent_family='deep_search'` only;
  provenance tags unaffected.
