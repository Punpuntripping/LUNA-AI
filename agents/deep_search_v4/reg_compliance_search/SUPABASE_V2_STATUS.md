# Supabase `regulation_v2` — Current Status

> Snapshot taken 2026-05-15 against project `dwgghvxogtwyaxmbgjod` via the PostgREST
> service-role API. This documents the *new* regulation corpus that `reg_search`
> will migrate onto. The legacy 3-tier corpus (`articles` / `sections` /
> `regulations`) is **not** covered here.

## 1. Overview

The v2 corpus is **3 tables**, not 2 — titles live in their own table:

| Table | Rows | Role |
|-------|-----:|------|
| `regulations_v2` | 3,375 | One row per legal document (نظام / لائحة / قرار …) |
| `chunks_v2` | 33,906 | Content chunks of a regulation (~10 chunks/regulation) |
| `chunk_titles_v2` | 123,788 | Search "titles" — multiple per chunk (avg ≈ 3.8, max 7) |

Hierarchy: `regulations_v2` → (1‑to‑many) `chunks_v2` → (1‑to‑many) `chunk_titles_v2`.

**Search model:** embeddings exist *only* on `chunk_titles_v2.title_embedding`
(and `regulations_v2.summary_embedding`). `chunks_v2` itself has **no embedding
column**. So semantic retrieval must target chunk *titles*; many titles can
resolve to the same chunk, so results must be **deduplicated by `chunk_id`**.

## 2. Tables

### `regulations_v2` — 3,375 rows

| Column | Type | Notes |
|--------|------|-------|
| `id` | uuid | PK |
| `reg_ref` | text | Stable external ref, e.g. `17330_reg_501` |
| `entity_id` | uuid | FK → `entities.id` |
| `entity_ref` / `entity_name` | text | Issuing authority |
| `title` / `clean_title` | text | Document title |
| `core_subject` | text | Short subject phrase |
| `doc_type_raw` / `doc_type_bucket` | text | Normalized type — see §4 |
| `sectors` | text[] | Arabic sector tags (1–N per doc) |
| `parent_law_id` | uuid | FK → `regulations_v2.id` (self) |
| `doc_relation` | text | `law_of_source` / `sibling_under_same_law` / null |
| `status_class` / `status_raw` | text | Lifecycle — see §4 |
| `legal_authority` | text | **JSON string** (not jsonb) — see §5 caveat |
| `start_date` / `end_date` | date | Effective dates |
| `landing_url` / `fallback_url` / `pdf_url` | text | Sources |
| `intro` / `scope` / `obligations` / `definitions` | text | Structured doc fields |
| `summary` / `llm_summary` | text | 100% populated |
| `summary_embedding` | vector(1024) | 100% populated |
| `ingested_at` | timestamptz | |

### `chunks_v2` — 33,906 rows

| Column | Type | Notes |
|--------|------|-------|
| `id` | uuid | PK |
| `chunk_ref` | text | e.g. `17330_reg_501_chunk_002` |
| `regulation_id` | uuid | FK → `regulations_v2.id` |
| `position` | integer | Order within the regulation |
| `prev_chunk_id` / `next_chunk_id` | uuid | Linked-list neighbours |
| `word_count` | integer | min 11 / median ≈ 354 / max 1,672 |
| `boundary_type` | text | `natural` / `merged` / `oversized_unit` / `child` / `split` / `whole_doc` |
| `chosen_level` | text | `article` / `chapter` / `section` / `whole_doc` / `heading` / `topic` |
| `corpus` | text | `with_articles` (≈99%) / `without_articles` |
| `title` | text | Chunk heading — 100% populated |
| `summary` | text | Lawyer-oriented summary — **32,158 / 33,906** populated |
| `context` | text | Where the chunk sits in the doc — **32,158 / 33,906** populated |
| `content` | text | Markdown body — 100% populated |
| `owns` | jsonb | Structural units, e.g. `{"FASL":[2],"MADDA":[4,5,6]}` |
| `has_tables` / `table_count` / `tables` | bool/int/jsonb | **Currently all 0 / empty** |
| `has_images` / `image_count` / `images` | bool/int/jsonb | **Currently all 0 / empty** |
| `enriched_at` / `ingested_at` | timestamptz | |

> No embedding column on `chunks_v2`.

### `chunk_titles_v2` — 123,788 rows

| Column | Type | Notes |
|--------|------|-------|
| `id` | uuid | PK |
| `title_ref` | text | e.g. `17393_reg_054_chunk_005_topic_3` |
| `chunk_id` | uuid | FK → `chunks_v2.id` |
| `regulation_id` | uuid | FK → `regulations_v2.id` (denormalized) |
| `topic_index` | integer | Title index within the chunk |
| `title_text` | text | The searchable Arabic title/topic phrase |
| `title_embedding` | vector(1024) | **100% populated** |
| `embedded_at` / `ingested_at` | timestamptz | |

## 3. Search RPC

`search_chunk_titles` is already deployed:

```
search_chunk_titles(query_embedding vector(1024), match_count int = ?, ef_search int = ?)
  → { title_id, chunk_id, regulation_id, title_text, distance }
```

- Pure vector search over `chunk_titles_v2.title_embedding` (cosine `distance`,
  lower = closer). `ef_search` tunes the HNSW candidate list — **default 80**,
  and rows returned = `min(ef_search, match_count)`. Must be passed explicitly
  (≈300–500); see §7.7.
- It returns **titles**, not chunks — and the same `chunk_id` appears multiple
  times (one row per matching title). The caller must dedup by `chunk_id` and
  then fetch `chunks_v2` content.
- **Decision:** v2 search is **semantic-only** via `search_chunk_titles`. No
  hybrid (BM25 + semantic) RPC will be added. Legacy `hybrid_search_*` RPCs
  target the old tables and are being dropped from `reg_search` (full cutover).

`reg_search` v2 flow:
`embed(query)` → `search_chunk_titles` → **chunk-aggregation + multi-hit
selection (§7)** → fetch `chunks_v2`.
The per-chunk retrieval/unfold shape (chunk-only vs. neighbours vs. regulation
meta) is **deferred — to be designed later, out of scope for this pass.**

## 4. Categorical values (sampled n=1,000)

- `doc_type_bucket`: `law_statute`, `regulation_generic`, `organizational_framework`,
  `executive_regulation`, `rules`, `technical_regulation`, `controls`, `guide`,
  `requirements`, `standard_spec`, `procedure`, `policy`.
- `status_class`: `in_force` (~73%), `consultation_ended` (~20%),
  `in_force_amended`, `cancelled`, `in_progress`.
- `chosen_level`: mostly `article` (~78%) and `chapter` (~19%).
- `corpus`: `with_articles` (~99%), `without_articles` (~1%).

## 5. Caveats / open items

1. **`legal_authority` is a TEXT column holding a JSON string** (keys:
   `authority_level`, `citizen_relevance`, `authority_score`, `authority_basis`,
   `scoring_rationale`, …). Only **1,184 / 3,375 (~35%)** populated. Needs
   `json.loads()` on read; consider migrating to `jsonb`.
2. **`chunks_v2.summary` / `context` missing for 1,748 rows** (~5%) — likely the
   un-enriched / `without_articles` chunks. Retrieval/format code must tolerate nulls.
3. **`has_tables` / `has_images` are all false** corpus-wide, and `tables` /
   `images` jsonb empty — table/image extraction not yet run (or not present).
4. Embedding dimension is **1024**, matching the current `reg_search` query
   embedder (Alibaba DashScope `text-embedding-v4` @ 1024 dims). Dimension is
   compatible, but the *model used to embed the v2 titles* is unconfirmed.
5. RLS state on the v2 tables was not checked (service-role key bypasses RLS).

## 6. Confirmed decisions (2026-05-15)

- **Search method:** semantic-only, using the existing `search_chunk_titles` RPC.
- **Migration:** full cutover — `reg_search` targets only `regulations_v2` /
  `chunks_v2` / `chunk_titles_v2`. Legacy `articles` / `sections` / `regulations`
  tables and `hybrid_search_*` RPCs are removed from `reg_search`.
- **Chunk unfold shape:** deferred — out of scope, to be designed later.

### Still open

- The embedding model used to generate `chunk_titles_v2.title_embedding` is
  unconfirmed. The dimension (1024) matches the current query embedder (Alibaba
  DashScope `text-embedding-v4`), but if the titles were embedded with a
  different model, semantic relevance will degrade. **Confirm the v2 titles were
  embedded with Alibaba `text-embedding-v4`.**

---

## 7. Multi-hit chunk selection (Design B — confirmed)

### 7.1 Why

`search_chunk_titles` returns *titles*; the same `chunk_id` appears once per
matching title. Each title is a **distinct facet** of the chunk (not a
duplicate) — verified against the source `chunks_abstract_topics.ndjson`. So a
chunk hit on several of its titles means the query agreed with the chunk along
several independent angles — a **consensus signal** stronger than one title
scoring marginally higher. The selection stage must reward this instead of
collapsing each chunk to its single best title.

Title-count distribution across the 32,145 source chunks (avg **3.85**
titles/chunk):

| Titles per chunk | Share |
|---|---|
| 1 | 6.2% |
| 2 | 9.9% |
| 3 | 22.4% |
| 4 | 28.8% |
| 5 | 22.3% |
| 6+ | 10.3% |

A chunk can be hit on at most as many titles as it owns — so **16% of chunks
(≤2 titles) cannot reach a hard "≥3 hits" bar**. The consensus rule below is
title-count aware to keep them eligible.

### 7.2 The stage

Runs **after** `search_chunk_titles`, **before** chunk fetch / unfold / rerank.
It governs *selection* only — it does not replace a downstream reranker.

1. Convert each title row's cosine `distance` → similarity `sim = 1 − distance`
   (0–1, higher = better).
2. Group title rows by `chunk_id`. Per chunk record:
   - `best_sim` — highest title sim,
   - `hits` — count of the chunk's titles with `sim ≥ hit_floor`,
   - `owned_titles` — total titles the chunk owns (from `chunk_titles_v2`, or
     the `owns`-equivalent topic count).
3. Apply the dual-track rule (§7.3). Selected chunks proceed to fetch/unfold.

### 7.3 Selection rule (Design B, Option 2 — absolute + full-consensus)

A chunk is **selected** if **either** track passes:

- **Rule A — quality:** `best_sim ≥ T_main`.
- **Rule B — consensus rescue:** `best_sim ≥ T_floor` **AND** either
  - `hits ≥ K`  *(absolute consensus)*, **or**
  - `hits ≥ 2` **AND** `hits == owned_titles`  *(full consensus — every title
    the chunk owns was hit; rescues 2-title chunks fairly).*

`hits` only ever counts titles with `sim ≥ hit_floor`, so the full-consensus
branch never fires on two weak guesses.

### 7.4 Knobs (starting values — TUNABLE, need calibration)

| Knob | Start | Meaning |
|------|------:|---------|
| `T_main` | 0.60 | Quality bar — best title passes the chunk on its own |
| `T_floor` | 0.50 | Rescue floor — best title must still reach this in Rule B |
| `hit_floor` | 0.45 | Min sim for a title to count as a "hit" |
| `K` | 3 | Absolute hit count for consensus rescue |

> Values are placeholders. They must be **calibrated** on real query→title
> similarity distributions from `search_chunk_titles` (the cosine scale of
> Alibaba `text-embedding-v4` titles is not yet measured). Calibration is a
> follow-up task. Invariant to preserve: `hit_floor ≤ T_floor < T_main`.

### 7.5 Ranking

Selected chunks are ordered by `best_sim` descending. Multi-hit is **not** a
score boost here — it only governs *inclusion* (Design B, not Design A). Among
chunks with equal/near-equal `best_sim`, higher `hits` breaks the tie upward.
Final ordering is still owned by any downstream reranker.

### 7.6 Worked example (`T_main`=0.60, `T_floor`=0.50, `hit_floor`=0.45, `K`=3)

| Chunk | Owned titles | Hits (sim ≥ 0.45) | Title sims | Outcome |
|-------|-------------:|------------------:|------------|---------|
| A | 4 | 1 | 0.71 | Rule A ✅ |
| B | 5 | 1 | 0.60 | Rule A ✅ |
| C | 6 | 3 | 0.58, 0.55, 0.52 | Rule B (`hits≥3`) ✅ rescued |
| D | 7 | 5 | 0.44…0.39 | best 0.44 < `T_floor` ✗ dropped |
| E | 2 | 2 | 0.57, 0.53 | Rule B (full consensus, 2==2) ✅ rescued |
| F | 5 | 2 | 0.57, 0.53 | `hits<K` and `hits≠owned` ✗ dropped |

### 7.7 Implication: `ef_search` (the real candidate-pool knob)

The multi-hit signal only exists if enough titles are fetched — and the knob
that controls this is **`ef_search`** (HNSW candidate-list size), *not*
`match_count`.

Empirically measured against the live RPC:

| `ef_search` | rows returned (`match_count=300`) |
|------------:|-----------------------------------|
| default (unset) | 80 |
| 40 | 40 |
| 80 | 80 |
| 200 | 200 |
| 500 | 300 (now `match_count`-capped) |

Rows returned = `min(ef_search, match_count)`. **The RPC default `ef_search`
is 80** — pass it explicitly. `reg_search` must call `search_chunk_titles`
with `ef_search ≈ 300–500` (and `match_count` ≥ that).

Why it is a *precondition* for Design B: at `ef_search = 80`, recall testing
showed buried consensus chunks returning **0 hits** — their titles sat past
rank 80, so the multi-hit rule was structurally blind to them. At
`ef_search = 400` those same chunks surface with 2–4 hits and Rule B can act.
A low `ef_search` makes Design B dead on arrival.

### 7.8 Calibration findings (recall test, 40 paraphrased queries)

- **Recall is bimodal:** a chunk is either top-1–3 or not top-10 at all —
  median rank when found is **2**; almost nothing lands in ranks 11–20.
- `ef_search` 80 → 400 raised "found anywhere" 31/40 → 39/40, but **recall@10
  barely moved (72% → 75%)** — `ef_search` fixes *visibility*, not *proximity*.
- **Every recall miss is a boilerplate chunk** — "المخالفات والعقوبات",
  "مسؤوليات الجهات الرقابية", "الأحكام الختامية", "التعريفات". Saudi technical
  regulations form large parallel families with near-identical such articles;
  a query with no regulation context cannot distinguish one family member's
  penalties article from 100 clones. **This is a query-scoping problem**
  (orchestrator must inject regulation/sector context), not a chunk-selection,
  threshold, or multi-hit problem — and not fixable by `ef_search`.
- For substantive, distinctive queries, recall is excellent (~94% top-10).
- Probe scripts: `reg_search/calibrate_chunk_titles.py`,
  `reg_search/recall_test.py`.

---

## 8. Retrieval strategy study — A/B/C/D (2026-05-15)

Four retrieval strategies were evaluated against one **frozen 60-query
benchmark** (`reg_search/recall_benchmark.json` — random chunks, each given an
LLM-paraphrased Arabic question; gold = the source chunk). Each strategy has a
runnable script (`reg_search/option_{a,b,c,d}_search.py`) and a report
(`reg_search/reports/option_{a,b,c,d}_recall.md`).

| Strategy | found-at-all | recall@5 | recall@10 | recall@20 | median rank |
|----------|:-----------:|:--------:|:---------:|:---------:|:-----------:|
| A — semantic only, `ef_search=400` | 95% | 67% | 75% | 80% | 3 |
| B — hybrid (semantic `ef=80` + BM25) | 97% | 65% | 75% | 83% | 3 |
| **C — hybrid (semantic `ef=400` + BM25)** | **100%** | **75%** | **80%** | **87%** | **2** |
| D — sector-scoped semantic, `ef=400` | 95% | 67% | 75% | 80% | 3 |

### Findings

- **`ef_search=400` is necessary but not sufficient.** A alone reaches 75% — a
  hard ceiling that B and D also hit.
- **Hybrid only pays off at full depth.** At `ef=80` (B) the BM25 lane just
  re-finds what the truncated semantic lane lost — lanes look redundant. At
  `ef=400` (C) the semantic lane is at full strength and BM25 adds *orthogonal*
  hits (≈8 queries top-10 via semantic only, ≈8 via lexical only) → recall@10
  75 → 80%, recall@5 67 → 75%, found-at-all 60/60.
- **Sector scoping is a dead end.** D with *oracle* (perfect) sector knowledge
  still scores 75% — the boilerplate clones are **intra-sector**. The 38-tag
  vocabulary is too coarse; the lever that would work is **regulation-level**
  scoping, not sector.
- **The remaining ~20% gap is the boilerplate / parallel-family problem** —
  not fixable by any retrieval-side lever tested.

### Decision

- **Adopt Option C** — hybrid retrieval: semantic lane (`search_chunk_titles`,
  `ef_search=400`) + client-side BM25 lexical lane (PostgREST `fts(simple)` →
  `rank_bm25`), fused with RRF (k=60).
- **Do not hard-cut at top-10 before the reranker.** C achieves
  **found-at-all = 60/60** — every gold chunk is somewhere in the result set.
  The selection stage must pass a *deep* fused pool (≈100–150 chunks) to the
  downstream reranker; recall@10 only matters if the pool is cut early, which
  it must not be. With a deep pool, C's usable recall is effectively 100%.
- Design B multi-hit (§7) still applies as an inclusion safeguard within that
  pool.
- The boilerplate gap is reassigned to (a) the **reranker** (precision over a
  deep pool — the chunks are present) and (b) an **ingestion fix**: boilerplate
  `chunk_titles_v2` entries like "المخالفات والعقوبات" carry no regulation
  identity; if abstract-topic titles embedded regulation context they would
  self-disambiguate.

---

## 9. Final search decision (2026-05-16) — resolves §6 ↔ §8

§6 said "semantic-only"; §8's strategy study then said "adopt Option C
hybrid". That contradiction is **resolved in favour of semantic-only.**

**Decision: semantic-only retrieval. No BM25 hybrid lane.**

- The Option-C BM25 lane is operationally fragile — per-token `fts(simple)`
  calls, statement-timeout dodging, client-side BM25, content-only caps. That
  is heavy, failure-prone machinery for the +5pp recall@10 §8 measured.
- `reg_search` rank-bands and passes only the **top ~15** chunks to the
  reranker (PRECISE ranks 1–5, SIMPLE ranks 6–15). Option C's gain came from
  *orthogonal deep* lexical hits at ranks beyond 15 — which a top-15 cut never
  reaches. Most of the hybrid gain is structurally unreachable in this pipeline.
- Boilerplate / parallel-family disambiguation is reassigned to the
  **reranker's regulation-scope test** (the v2 reranker prompt leads with
  "does the regulation's scope apply to the sub-query?"), not to BM25.

**`ef_search`: settle on ≈150** (`match_count` ≈150).

- With a top-15 reranker cut, pool depth past ~15 is irrelevant.
- Measured (2026-05-16, live RPC, random 1024-d vector): ef=80 ≈ 0.55 s,
  ef=400 ≈ 1.6–2.7 s — a 3–5× latency cost. Per §7.8 recall@10 only moves
  72% → 75% across that range.
- ef≈150 is a mild margin over the default 80 for HNSW ranking stability of
  the top-15, at ~0.7–0.9 s. ef=400 is not worth the ~2 s tail.

**Sector: not a recall lever.** §8 Option D showed *oracle* sector knowledge
still scores 75% — no better than plain semantic. Sector stays only as the
**planner-driven scope filter** (`LoopState.sectors_override`) — a
precision/narrowing knob applied at the caller's discretion, not a retrieval
mechanism.

**Net v2 search shape:**
`embed(query)` → `search_chunk_titles(ef_search≈150, match_count≈150)` →
dedup by `chunk_id` (best_sim) → **select top-15 by best_sim** → rank-band each
chunk into `_mode` (`precise` ranks 1–5 / `simple` ranks 6–15) → hand chunk
rows to the reranker. No BM25 lane, no §7 absolute gate (see §10).

---

## 10. §7 threshold calibration result (2026-05-16) — §7 selection dropped

The §7 multi-hit selection (§7.3) used absolute cosine thresholds
(`T_main/T_floor/hit_floor/K`) explicitly flagged as uncalibrated guesses.
They were calibrated against the frozen 60-query gold benchmark
(`_archive/reg_search_v2_experiment/calibrate_60.py`, `ef_search=150`,
`match_count=150`).

**Returned-list cosine scale (avg / 60 queries):** top 0.774, median 0.547,
min 0.509 — the *entire* returned set sits above ~0.51.

**Gold chunk (the correct answer):** found in pool 55/60; `best_sim` avg 0.724,
median 0.710, p25 0.626, p10 0.576, min 0.532; rank avg 9.0, median 3; in
top-5 = 40/60, in top-15 = 47/60; hits avg 3.20.

**Threshold survival** (gold kept / avg pool size per query):

| T | gold kept | pool size |
|--:|:---------:|:---------:|
| 0.45 | 55/60 | 102 |
| 0.50 | 55/60 | 84 |
| 0.55 | 53/60 | 54 |
| 0.60 | 45/60 | 24 |
| 0.65 | 36/60 | 10 |

**Findings:**
- `hit_floor=0.45` / `T_floor=0.50` are **no-ops** — the whole returned set
  clears them, pool stays 84–102 chunks/query, no selection happens.
- `T_main=0.60` **drops 10 of 55 findable gold chunks** (~18% recall loss) —
  gold `best_sim` p10/p25 are 0.576/0.626, real correct answers sit below 0.60.
- The cosine scale is **query-dependent** — one query's best chunk (0.655)
  is below another's median (0.693). No single absolute T can mean "good".

**Decision:** the §7 absolute-threshold gate is **dropped**. `search.py`
selects **top-15 by `best_sim`** (relative, scale-immune). Retrieval ceiling of
the design: **78% (47/60) of gold in top-15**, 67% in top-5. The §7.3 dual-track
rule and §7.4 knobs are obsolete unless reworked into a *relative* form.
