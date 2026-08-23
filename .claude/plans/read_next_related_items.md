# «اقرأ تاليًا» + «الأنظمة المذكورة» — related-items strips

**Status:** BUILT + migration 143 APPLIED to prod 2026-08-23. Waves A–C shipped;
Wave D (calibration) and Wave E (topic-BM25) still open.
**Wings touched:** `/regulations`, `/compliance`, `/circulars`, `/judgments`
**Next migration number:** `143` (142 = compliance wing is the current head)

## Backfill, as applied (2026-08-23) — the §10 "record the numbers" row

`refresh_related_axis_weights()` → **310** weight rows (regulation 38 sectors /
134 entities · circular 37 / 5 · compliance 34 / 29 · judgment 29 courts / 4
entities). All four §10 invariants pass: entity split-brain collapsed, no
cross-type edge, no self-edge, nothing below FLOOR, weekly job scheduled.

| corpus | edges | sources with ≥1 | bonus-only | score range |
|---|---|---|---|---|
| judgment | 1,456,576 | **4,461** (plan predicted 4,461 exactly) | all | 0.163–1.804 |
| regulation | 77,612 | 2,996 of 3,951 (76%, plan guessed ~50%) | 62,342 | 0.151–6.235 |
| circular | 7,326 | 375 | all | 0.153–1.002 |
| compliance | 354 | 83 of 337 | all | 0.155–2.000 |

`related_items` is **648 MB** with its two indexes. Wave D starts from here.

⚠ **Two operational facts the plan did not have, both cost an attempt:**

1. **The judgment backfill takes ~13 minutes** (12:56 measured) and is bounded by
   the CALLER's `statement_timeout`, not by the function's own `SET` clause —
   that clause cannot extend a statement already running. Supabase's pooler cuts
   at 2 minutes, so the first two attempts died at exactly 120.0s and rolled
   back silently (`related_items` simply stayed empty for that corpus). Run it as
   a `pg_cron` one-shot with a session-level `set statement_timeout = '900s'`;
   the recipe is in 143's header beside that clause.
2. **The candidate volume is 2.2M, not the "~10^5" 143's comment claimed.** The
   plan's own §7 table (2,218,000) was right and the SQL comment was wrong; it
   has been corrected in the migration.

Two card strips at the bottom of every public library object page, above `SiteFooter`:

```
… document content …
AskRayhanWidget
┌─ «الأنظمة المذكورة» ──── 7 max · 3 in view · side-scroll ─┐   ← only where citations exist
└───────────────────────────────────────────────────────────┘
┌─ «اقرأ تاليًا» ───────── 7 max · 3 in view · side-scroll ─┐   ← every type, same-type only
└───────────────────────────────────────────────────────────┘
SiteFooter
```

Both are **ungated** — the same bytes for anon, free and paid. That is not a preference,
it is forced: all four detail pages are ISR-baked (`DOC_REVALIDATE = 86400`,
`frontend/lib/library/api.ts:201`) and serve one HTML artifact to every visitor. A
per-tier strip is not expressible without going dynamic.

---

## 1. Locked decisions

| # | Decision | Why |
|---|---|---|
| **D1** | Strip contents are **titles + links + the snippet the hub cards already show anon**. It never unlocks body content. | The gate keeps doing its job; only the navigation mesh opens. |
| **D2** | **اقرأ تاليًا is same-type only.** نظام→أنظمة, خدمة→خدمات, تعميم→تعاميم, حكم→أحكام. | Cross-type candidates can only ever score on entity+sectors (max 2.0) and would never outrank a same-type candidate carrying a base. Mixing produced a list that was same-type in practice anyway. |
| **D3** | **الأنظمة المذكورة is the only cross-type surface**, and only in the direction حكم→نظام and نظام→نظام. | It is a factual citation list, not a similarity guess. |
| **D4** | **No reverse section** («الأحكام التي طبّقت هذا النظام»). | Only 50 of 1,686 slugged أنظمة have any slugged judgment citing them, and 4 of those have 1,100–3,455. A section that exists on 3% of pages and floods 4 is not worth building. |
| **D5** | **Graph is computed over the FULL corpus**; the publish filter is applied at render. | B can be A's top neighbour and simply not render until B is slugged. Publishing then lights an item up everywhere with **no recompute**. |
| **D6** | The refresh job stores **every edge above the floor, ranked — never a top-N per source.** | A top-10 whose members are all unpublished renders an empty strip while good candidates sit at rank 11+. Average degree is ~2, so storing everything is cheap. |
| **D7** | **Cap 7, 3 in view, horizontal RTL side-scroll**, identical card design in both strips. | |
| **D8** | **One card per نظام. No مادة-level cards.** | The section is الأنظمة المذكورة, not المواد المذكورة. |
| **D9** | **Unresolved citations are skipped.** | 5,469 of 22,278 judgment citation entries name a قرار وزاري / تعميم / فقه ruling with no page to link to. |
| **D10** | **No relation-type chip on cards.** `document_relations.relation` ranks but never displays. | 585 of 746 relation rows are `one_way` (single-method, unconfirmed) — good enough to rank on, not good enough to assert. |
| **D11** | One fixed heading per strip. No `representative_core` in the heading. | |
| **D12** | **Precomputed tables, refreshed weekly by a SQL function on `pg_cron`.** Publish state stays out of the table. | Every input is already in Postgres; no LLM, no external call. Publish freshness comes from the 24h ISR window, not from the job. |
| **D13** | **Deduplicate across the two strips.** الأنظمة المذكورة renders first and wins; اقرأ تاليًا excludes anything shown above it and backfills. | Only actually bites on نظام pages — everywhere else the two strips hold disjoint types. |
| **D14** | **الأنظمة المذكورة is omitted on تعاميم and خدمات.** | Neither corpus has any citation data. `cross_references_v2` only carries `case` and `reg_chunk` sources. Extracting نظام mentions from prose is a separate project. |

### Accepted trade — say it out loud

This puts up to **14 outbound links on every baked public page**. `navigation_enumeration_defence`
meters ITEMS not pages, and anon hub depth is capped at page 1 — this strip hands an anonymous
crawler a walkable graph across the whole published corpus, past that cap. That is the point of
the feature, and it was accepted knowingly. Bodies stay gated exactly as they are.

---

## 2. Ground truth (measured live, 2026-08-22)

Everything below came from the production DB, not from migration files. **Migration files ≠ prod
schema in this project — re-verify before relying on any of it.**

### Corpus sizes and the link pool

A strip can only link **slugged** items (`seo_item_meta.slug is not null`):

| Type | `content_type` | Rows | Slugged (linkable) |
|---|---|---|---|
| خدمات | `compliance` | 337 | **337** |
| تعاميم | `circular` | 1,843 | **1,843** |
| أنظمة | `regulation` | 3,951 | **1,686** |
| أحكام | `judgment` | 30,531 | **10,000** |

### The relation graph — أنظمة only

| Table | Rows | Shape |
|---|---|---|
| `regulation_v2.core_subjects` | 1,821 memberships / **524 clusters** / threshold 0.6 | A **partition** — every covered نظام is in exactly one cluster. Carries `representative_core`, an Arabic label. Sizes: 288 clusters of 2, tail to 57. |
| `regulation_v2.core_subject_relations` | **2,191** unordered pairs, score 0.6–1.0 (median 0.862), 1,739 docs | One row per pair — **queries must union `(a,b)` with `(b,a)`**. |
| `regulation_v2.document_relations` | **746** pairs | `sibling_under_same_law` 414 · `executive_regulation` 330 · `amendment` 2. `agreement`: **both = 161**, **one_way = 585**. |

- `regulations_v2.parent_law_id` (317 rows) is **fully contained** in `document_relations`. Do not read it — one source, not two.
- `core_subject_relations` ∩ `document_relations` = only **205** pairs. Complementary, both feed the score.
- Coverage over the full 3,951: **1,993 (50%)** have ≥1 curated edge. Same ratio inside the slugged
  subset (1,044 / 1,686 have an edge; **841** have one whose other end is also slugged).
- Live degree when both ends are slugged: `core_subject_relations` avg **1.97**, max **14**;
  `document_relations` avg **1.81**.

**Sector is redundant among curated candidates:** 96.2% of core-subject edges already share a
sector, and only 3.5% share neither entity nor sector. Sector is a *fallback*, not a ranker.

### Citations

| Source | Rows | Notes |
|---|---|---|
| `cases.referenced_regulations` (jsonb) | 8,411 slugged judgments carry entries · 22,278 entries · avg **2.65**, max ~6 | 16,809 resolved · **16,734 point at a slugged نظام** · 5,469 unresolved (D9 drops these) |
| `public.cross_references_v2` `source_type='reg_chunk'` | 5,174 edges → **717 distinct نظام→نظام pairs**, max 7 targets | 580 أنظمة have any outbound citation; **305** with both ends slugged |
| `public.cross_references_v2` `source_type='case'` | 29,363 edges | مادة-granularity; only **67 distinct regulations** are ever cited. Inferior to `referenced_regulations` for this purpose. |

**The cap of 7 never binds on الأنظمة المذكورة** — 0 judgments exceed 10 distinct cited أنظمة.
Expect 1–3 cards; the scroller is decorative there.

### Scarcity axes

`sectors` is `text[]` on `regulations_v2`, `circulars`, `services` (خدمات inherit via
`service_guides.service_id → services.sectors`). **أحكام have no `sectors` column** —
`legal_domains` and `court` play that role. Vocabulary: 38 sectors, `shared/library/sectors.py`;
12 courts, `shared/library/courts.py`.

Average sectors per doc: أنظمة **2.62** · تعاميم **1.89** · خدمات **1.56**.

Entities: `entities` table (400 rows). `regulations_v2.entity_ref/entity_id`,
`circulars.entity_ref`, `service_guides.entity_ref`, `cases.entity_id`.

Distinct entities: **أنظمة 135 `entity_ref`s** (134 resolve to a name among slugged items) ·
**خدمات 29** · **تعاميم 5** · **أحكام 4**.

> ⚠ **Key the entity axis on `entity_ref`, never `entity_name`.**
> `regulations_v2.entity_name` is **NULL on 1,739 of 3,951 rows (44%)**. Within `regulations_v2` no
> `entity_name` maps to more than one `entity_ref`, so `entity_ref` alone is a safe key.

> ⚠ **Entity split-brain — three refs, one body.** In `entities`:
> `5000` «أنظمة عامة (هيئة الخبراء بمجلس الوزراء)» (**920** أنظمة full corpus / 345 slugged),
> `17573` «هيئة الخبراء بمجلس الوزراء» (**366** / 138), and `40002` «هيئة الخبراء بمجلس الوزراء»
> (**0** — exists in `entities`, owns nothing; fold it in anyway so it can never surface as a
> singleton with weight 1.0). Collapsed, that body owns **1,286 of 3,951 أنظمة (33%)** and must
> weigh ~0. Uncollapsed it reads as two mid-size entities and both earn a real weight. Fix in
> Wave A; see §9.

Top full-corpus `entity_ref` counts in `regulations_v2`:
`5000=920, 17405=411, 17573=366, 17636=234, 18308=198, 18269=155, 17900=149, 17609=109`.

### Topic supply (for Wave E)

| Corpus | Docs covered | Topics per doc | Table |
|---|---|---|---|
| أنظمة | 1,686 / 1,686 | **43.8** | `search_topics` `source_type='regulation'` (155,765) |
| تعاميم | 1,843 / 1,843 | **1.15** | `search_topics` `source_type='circular'` (2,119) |
| خدمات | 4,746 services | ~1.4 | `search_topics` `source_type='service'` (6,712) |
| أحكام | 10,000 / 10,000 | 9.69 | `case_topics` (279,948) — `basis` / `fact` / `principle`, each vector-indexed |

`topics` (38 rows) is just the sector list mirrored and **`topic_map` is empty (0 rows)** — there
is no curated cross-corpus topic taxonomy. Do not build against it.

`search_index` (BM25) currently holds judgment 10,000 · circular 1,843 · regulation 1,686 · blog
103 · service 100 · template 6 — **no `compliance` rows**; those arrive with the unbuilt
`compliance_entity_sections` plan.

---

## 3. The scoring model

`score = base + bonus`, evaluated per **(source, candidate)** pair within one corpus.

### 3.1 Scarcity weight — the shared primitive

For an axis value `v` in corpus `c` (sector, entity, or court):

```
N            = documents in the corpus (FULL corpus, not the slugged subset)
n_v          = documents carrying v
share_v      = n_v / N
TARGET_SHARE = 0.007            -- what "scarce" means, as a fraction of the corpus
w(v)         = least(1.0, (TARGET_SHARE / share_v)^2)
```

Full-corpus counts, so **publishing never shifts the weights**.

Inverse-square, not inverse-linear: the target spread was ~100× across an 8.9× size difference.
`1/n` gives only 8.9×. `TARGET_SHARE = 0.007` is calibrated to the design anchors —
المعاملات التجارية ≈ 0.001 and الشؤون الخارجية ≈ 0.1:

| أنظمة sector | n | share | `1/n` | **w** |
|---|---|---|---|---|
| المعاملات التجارية | 783 | 19.8% | 0.0013 | **0.0013** |
| المالية والضرائب | 681 | 17.2% | 0.0015 | 0.0017 |
| القضاء والمحاكم | 304 | 7.7% | 0.0033 | 0.0083 |
| العقار | 179 | 4.5% | 0.0056 | 0.0239 |
| الشؤون الخارجية | 88 | 2.2% | 0.0114 | **0.0989** |
| الحج والعمرة | 45 | 1.1% | 0.0222 | 0.3782 |
| التعاملات والأحوال المدنية | 25 | 0.6% | 0.0400 | 1.0000 |

> ⚠ **Do not normalize on `n_min` (the smallest observed value).** That was the first draft and it is
> wrong: it measures rank among values, not share of the corpus. تعاميم have only 5 entities spanning
> 296–619, so the smallest would score `w = 1.0` while being **16% of the corpus** — a near-maximum
> bonus for "same issuer" across 296 circulars. أحكام entities (4 values, 225–20,671) fail the same
> way. Corpus share is the invariant; rank is not.

`TARGET_SHARE` and `FLOOR` are the only two tuning knobs. Keep them adjacent and obvious.

Same treatment on entity. Measured full-corpus weights:

| corpus | entity | n | w |
|---|---|---|---|
| أنظمة (N=3,951) | `5000`+`17573`+`40002` collapsed | 1,286 | **0.00046** |
| | `17405` | 411 | 0.0045 |
| | `17636` | 234 | 0.0140 |
| | `18269` | 155 | 0.0318 |
| خدمات (N=337) | وزارة العدل | 115 | **0.0004** |
| | وزارة التجارة | 43 | 0.0030 |
| | وزارة الصحة | 16 | 0.0217 |
| تعاميم (N=1,843) | البنك المركزي السعودي | 619 | **0.00043** |
| | الهيئة السعودية للمراجعين والمحاسبين | 317 | 0.0017 |
| | وزارة العدل | 296 | 0.0019 |

**All five تعاميم entities land between 0.0004 and 0.0019** — correctly, since each is 16–34% of the
corpus. تعاميم therefore run on sectors alone until Wave E lands the topic base. That is expected,
not a defect.

And on court. Note this is measured on the **raw `cases.court` string over the full 30,531**, which
is far more granular than the 12-value canonical vocabulary in `shared/library/courts.py`:

| court (raw) | n | w |
|---|---|---|
| التجارية | 20,334 | **0.0001** |
| ديوان المظالم — الدائرة التجارية | 2,625 | 0.0066 |
| ديوان المظالم — الدائرة الإدارية | 1,879 | 0.0129 |
| هيئة الزكاة والضريبة — اللجنة الاستئنافية الضريبية/الزكوية | 644 | 0.110 |
| هيئة الزكاة والضريبة — الدائرة الأولى … جدة | 585 | 0.134 |
| هيئة الزكاة والضريبة — الدائرة الأولى … الدمام | 455 | 0.221 |

> **Wave D decision — raw court or canonical bucket?** The raw string yields a rich tail of mid-size
> chambers (w = 0.11–0.22) that the 12-value canonical bucketing would collapse into
> «ديوان المظالم» / «لجان» and flatten to ~0. Raw is more discriminative; its risk is that
> `COURT_VARIANTS` exists because ~30 raw spellings map to those 12, so variant spellings will split
> one chamber into several. Sample the raw values during calibration and decide then. Either way,
> التجارية is 67% of the corpus and gets nothing — which is why most judgment pages show no strip.

### 3.2 Bonus — entity + sectors, capped at 2.0

```
k            = number of shared sectors (or legal_domains)
mult(k)      = k*(k+1)/2                       -- 1, 3, 6, 10 …
sector_term  = least(1.0, mult(k) * Σ w(s) over the k shared sectors)
entity_term  = w(entity)  when the entity matches, else 0
bonus        = least(2.0, entity_term + sector_term)
```

The `mult(k)` super-linearity is deliberate: two documents sharing two sectors is far rarer than
sharing either one, so the pair deserves more than the sum. The `least(1.0, …)` on `sector_term` is
required — without it, three rare shared sectors gives `6 × ~2.5 = 15` and the *weakest* axis
outranks the strongest.

**أحكام substitute court for sector:** `bonus = least(2.0, entity_term + w(court))`.

### 3.3 Base — per corpus

**أنظمة** — take the **maximum** of whichever apply:

| condition | base |
|---|---|
| `document_relations` edge, `agreement = 'both'` | **5.0** |
| `document_relations` edge, `agreement = 'one_way'` | **3.5** |
| `core_subject_relations` edge | **1.5 + 1.5 × (score − 0.6) / 0.4** → [1.5, 3.0] |
| same `core_subject_id`, no relation row | **1.2** |
| none | 0.0 |

Splitting `both` / `one_way` is how D10's skepticism enters the arithmetic: 161 cross-confirmed
edges stay untouchable at 5.0, while the 585 single-method guesses drop to 3.5 and have to compete.

**تعاميم / خدمات** — topic-BM25, normalized **relative to the best match for that source**:

```
base = 3.0 * (bm25_score / max_bm25_score_for_this_source)     -- (0, 3.0]
```

Relative normalization because BM25 is unbounded and its absolute scale varies with corpus
statistics. Ships in **Wave E**; until then تعاميم and خدمات run bonus-only.

**أحكام** — no base axis. `base = 0.0` always; court carries the signal through the bonus.

### 3.4 Floor, guard, cap

```
FLOOR = 0.15          -- calibrate in Wave D
```

Rationale for the starting value: a pair sharing one common sector
(المعاملات التجارية, `w = 0.001 × mult(1) = 0.001`) falls far below it, while a pair sharing two
mid-rare sectors (العقار 0.0195 + الإسكان 0.0323, `× mult(2) = 0.156`) clears. Tune against real
samples, do not ship the guess.

- **Cap 7** after the publish filter and after dedup against الأنظمة المذكورة.
- **Bonus-only guard:** at most **2** of the 7 may have `base = 0`. This applies **only to corpora
  that have a base axis** (أنظمة always; تعاميم/خدمات after Wave E). It is **off for أحكام**, where
  every score is bonus-only by construction — otherwise a supreme-court judgment would be capped
  at 2 cards for no reason.
- **Below the floor, the strip is hidden entirely** — no padding with weak sector matches.

### 3.5 Known and accepted: the bands overlap

The ceilings are additive and they cross:

- `1.2` (bare cluster member) `+ 2.0` (full bonus) `= 3.2` > `3.0` (best core-subject relation)
- `3.0 + 2.0 = 5.0` = `5.0` (a confirmed `document_relations` edge)

So a same-subject + same-entity + rare-sector candidate can tie a confirmed لائحة edge, and will
beat an unconfirmed one (3.5). **This is intended, not a bug** — see D10. If it ever needs
reversing, the one-line knob is to lower the bonus cap to 1.0 and widen the base gaps to ≥2.0,
which restores strict non-crossing bands.

### 3.6 What each corpus actually gets

| | خدمات (337) | أنظمة (1,686) | تعاميم (1,843) | أحكام (10,000) |
|---|---|---|---|---|
| base | topic-BM25 (Wave E) | **relations + core subject** | topic-BM25 (Wave E) | **none** |
| entity | 29 values, وزارة العدل = 115 → ~0 | **134 values — works** | 5 values → ~0 | 4 values → ~0 |
| sector / court | 1.56/doc | 2.62/doc | 1.89/doc | court: 7,483/10,000 are التجارية → ~0 |
| **expected outcome** | thin until Wave E | **good** | **bonus-only until Wave E** | **no strip on ~75% of pages** |

**أحكام will show no «اقرأ تاليًا» on most pages.** 7,483 of 10,000 slugged judgments sit in
التجارية where court is worth 0.0002 and entity is worth ~0, so nothing clears the floor. That is
the intended outcome — better a missing strip than six arbitrary commercial rulings. `case_topics`
(100% coverage, 9.69 topics/judgment, three vector-indexed kinds, `principle` being the
transferable one) is the shelved upgrade; it is **not** in this plan.

---

## 4. Wave A — data layer (`143_related_items.sql`)

Agent: **@sql-migration**

### 4.1 `public.related_axis_weights` — the scarcity table

Materialized so the weights are auditable and so the refresh function is not recomputing
`n_v` per pair.

```sql
create table if not exists public.related_axis_weights (
  corpus   text    not null check (corpus in ('regulation','compliance','circular','judgment')),
  axis     text    not null check (axis in ('sector','entity','court')),
  value    text    not null,
  n        integer not null,
  weight   real    not null,
  built_at timestamptz not null default now(),
  primary key (corpus, axis, value)
);
```

### 4.2 `public.related_items` — the edge store

```sql
create table if not exists public.related_items (
  source_type text not null check (source_type in ('regulation','compliance','circular','judgment')),
  source_id   text not null,          -- matches seo_item_meta.content_id (uuid::text)
  target_type text not null check (target_type in ('regulation','compliance','circular','judgment')),
  target_id   text not null,
  score       real not null,
  base        real not null default 0,
  bonus       real not null default 0,
  reason      text not null,          -- audit only, never rendered (D10)
  built_at    timestamptz not null default now(),
  primary key (source_type, source_id, target_type, target_id),
  constraint related_items_no_self check (source_id <> target_id or source_type <> target_type)
);

create index if not exists idx_related_items_lookup
  on public.related_items (source_type, source_id, score desc);
```

**No stored `rank` column.** Rank is meaningless before the publish filter runs — it changes with
every publish. Order by `score desc` at read time.

`reason` vocabulary: `document_relation_both` · `document_relation_one_way` ·
`core_subject_relation` · `core_subject_member` · `topic_bm25` · `bonus_only`.

### 4.3 RLS and grants

```sql
alter table public.related_items       enable row level security;
alter table public.related_axis_weights enable row level security;
revoke all on public.related_items,       public.related_axis_weights from public, anon, authenticated;
```

No policies. The backend reads with the service role. This matters: there is an **open finding**
that the anon key can read corpus tables directly through PostgREST
(`project_anon_postgrest_corpus_exposure`). Do not add a permissive policy "so the frontend can
read it" — the frontend never talks to Supabase for library data.

### 4.4 `refresh_related_axis_weights()`

Rebuilds all three axes for all four corpora from full-corpus counts. Straightforward
`insert … on conflict do update`; `n_min` per (corpus, axis) is
`min(n) filter (where n >= 2)`.

### 4.5 `refresh_related_items(p_corpus text)`

`SECURITY DEFINER`, `returns integer` (rows written), one branch per corpus, mirroring the shape of
`refresh_search_index` in `111_bm25_search_index.sql:370`. Deletes and re-inserts that corpus's
rows. Raises on an unknown corpus.

**Candidate generation — do NOT cross-join the corpus.** A naive
`regulations × regulations` is 15.6M pairs for أنظمة and 932M for أحكام. Generate candidates from
the union of:

1. `document_relations`, both directions
2. `core_subject_relations`, both directions
3. same `core_subject_id` (cluster co-members) — bounded, max cluster is 57
4. same `entity_ref` — **bounded by a guard**: skip entities where `w(entity) < 0.0005`, which kills
   the 345/155/138-document buckets before they generate 60k pairs each
5. shared sectors — **bounded by the same kind of guard**: only generate a sector-only pair when
   `sector_term ≥ FLOOR`, which by construction excludes the huge sectors

Then score, apply the floor, and insert. Steps 4 and 5 are what keep this from being a cross join:
the scarcity weights make common values generate nothing, which is exactly why the weights table is
materialized first.

For **أحكام**, steps 1–3 do not exist and step 4 yields ~0, so the only generator is court —
and only for courts above the floor. Expect a small table (order of 10⁵ rows, dominated by the
five tail courts), not 932M.

### 4.6 Weekly `pg_cron` schedule

Follow the guarded pattern at `111_bm25_search_index.sql:695` — check `pg_extension`, unschedule any
existing job of the same name, then schedule. Weekly, offset from the nightly BM25 job:

```sql
perform cron.schedule(
  'related_items_refresh_weekly', '40 3 * * 0',
  $cron$
    select public.refresh_related_axis_weights();
    select public.refresh_related_items('regulation');
    select public.refresh_related_items('circular');
    select public.refresh_related_items('compliance');
    select public.refresh_related_items('judgment');
  $cron$);
```

Plus a backfill run at migration time, same as 111 does.

Weekly is safe because the inputs (`core_subjects`, `document_relations`, `cross_references_v2`,
sectors, entities) are pipeline-ingested and change rarely. Publish state is **not** in the table,
so a newly slugged item appears in everyone's strip within the 24h ISR window without waiting for
Sunday.

---

## 5. Wave B — backend

Agent: **@fastapi-backend**
Files: `backend/app/services/library_service.py`, `backend/app/api/public_library.py`

### 5.1 Reuse the hub item shape

The related entries should be returned as the **existing hub item models**, so the frontend feeds
them straight into the existing cards with zero new card work:

| Doc response | new field | item type |
|---|---|---|
| `RegulationDocResponse` (`public_library.py:2421`) | `related_next`, `cited_regulations` | `RegHubItem` (`:2242`) |
| `ComplianceGuideDoc` (`:2562`) | `related_next` | `ComplianceHubItem` (`:2434`) |
| `CircularDocResponse` (`:2689`) | `related_next` | `CircularHubItem` (`:2527`) |
| `JudgmentDocResponse` (`:2887`) | `related_next` | `JudgmentHubItem` (`:2587`) |
| `JudgmentDocResponse` | `cited_regulations` **type change** | `JudgmentCitedRegulation` → `RegHubItem` |

**Prerequisite refactor:** `list_regulations_hub` builds its item dict inline
(`library_service.py:~2440`). Extract `_reg_hub_item(row) -> dict` (and the three siblings) so the
lister and the related-items reader produce byte-identical shapes. Do this first — duplicating the
dict literal is how the two drift.

### 5.2 `get_related_next(supabase, content_type, content_id, exclude_ids) -> list[dict]`

One query, then hydrate:

```sql
select target_id, score
from public.related_items ri
join public.seo_item_meta m
  on m.content_type = ri.target_type
 and m.content_id   = ri.target_id
where ri.source_type = :ct
  and ri.source_id   = :cid
  and m.slug is not null              -- D5: the publish filter lives HERE
  and ri.target_id <> all(:exclude)   -- D13: dedup against الأنظمة المذكورة
order by ri.score desc
limit 40                              -- over-fetch, then apply the bonus-only guard in Python
```

Then in Python: apply the bonus-only guard (≤2 rows with `base = 0`, skipped for `judgment`), take
7, hydrate into hub items in one batched lookup. **Batch and chunk every `in.()` at
`_ID_IN_CHUNK` (150)** — a long `in.()` blows PostgREST's URL length into a 400, which has bitten
the hub listers before. Fail soft: a lookup error costs the strip, not the page.

### 5.3 الأنظمة المذكورة — resolved live, not precomputed

**Judgments** — rework `_judgment_cited_regulations` (`library_service.py:5079`):

- it currently dedupes by `(regulation_id, article_no)` → **re-dedupe by `reg_ref` alone** (D8)
- it currently keeps unresolved refs with `reg_slug = None` → **drop them** (D9)
- it currently returns `{title, article_no, reg_slug, article_slug}` → **return `RegHubItem`s**
- `JUDGMENT_CITED_FREE_LIMIT` → **7**
- keep the existing join, which is verified and non-obvious: `ref['regulation_id']` is a **`reg_ref`
  TEXT key** («17642_reg_037»), not a uuid → `regulations_v2.reg_ref` → that row's `id::text` is the
  `seo_item_meta` key for the slug

Dropping `article_slug` loses links to مادة pages. There are only 5 slugged articles today, so this
costs 5 potential links. Note it and move on.

**أنظمة** — new `_regulation_cited_regulations(supabase, reg_id)`:

```sql
select distinct target_regulation_id
from public.cross_references_v2
where source_type = 'reg_chunk'
  and source_regulation_id = :id
  and target_regulation_id is not null
  and target_regulation_id <> :id
```

→ filter to slugged → hydrate to `RegHubItem` → cap 7. Expect 0–7 (580 أنظمة have any outbound
citation, avg 1.24, max 7).

**تعاميم / خدمات** — the field is absent (D14).

### 5.4 Call order in the doc endpoints

الأنظمة المذكورة resolves **first**; its target ids become `exclude_ids` for `get_related_next`
(D13). On نظام pages both lists are أنظمة and this is load-bearing; elsewhere it is a no-op.

### 5.5 Caching

Unchanged. These endpoints already send `public, max-age=3600` for anon and `private, no-store` for
authed callers. The strips carry no per-user bytes, so nothing about the gate changes.

---

## 6. Wave C — frontend

Agent: **@nextjs-frontend**

### 6.1 New: `frontend/components/library/blocks/RelatedStrip.tsx`

Server component. Props:

```ts
{ title: string; children: React.ReactNode; className?: string }
```

- `dir="rtl"`, heading styled like `ReadAfter.tsx`'s `<h2>` (`text-sm font-bold`, lucide icon,
  `mb-3`)
- horizontal track: `flex gap-3 overflow-x-auto snap-x snap-mandatory scroll-smooth` with
  `-mx-*`/`px-*` bleed so cards can touch the container edge
- each child wrapped `shrink-0 snap-start basis-[calc((100%-2*0.75rem)/3)]` for 3-in-view on
  desktop; **1.15 in view on mobile** so the cut-off card signals scrollability
- returns `null` on an empty `children` list — the strip disappears rather than showing an empty
  heading
- hide the scrollbar visually but keep keyboard and wheel scrolling; add
  `aria-label` on the track

> ⚠ **Do not use Radix `ScrollArea`.** It is already documented in this repo that `ScrollArea` kills
> `h-full` and `truncate` on children (`references_window_fixes`, `sidebar_redesign_compact_nav`).
> The hub cards rely on both. Native `overflow-x-auto` with scroll-snap.

### 6.2 Cards — reuse, don't rebuild

`RegulationCard` · `ComplianceCard` · `CircularCard` · `JudgmentCard`
(`frontend/components/library/hub/`) render unchanged inside the strip. They already sit on
`CardShell`, which keeps the footer chips outside the anchor — no nested-`<a>` problem in a
scroller.

Cards need a **fixed basis** inside a flex track; verify each still lays out at ~1/3 width.

### 6.3 Retire `CitedRegulations.tsx`

`frontend/components/library/blocks/CitedRegulations.tsx` (used at
`frontend/app/judgments/[slug]/page.tsx:382`) is replaced by `RelatedStrip` + `RegulationCard`.
Heading changes «الأنظمة المستند إليها» → **«الأنظمة المذكورة»**.

`frontend/types/library.ts:467` also references `JudgmentCitedRegulation[]` — check that consumer
before deleting the type, or leave the type in place and stop populating it.

### 6.4 Page wiring — insert before `AskRayhanWidget`

All four pages end the same way, with `SiteFooter` coming from
`LibraryPageShell.tsx:83`:

| Page | `AskRayhanWidget` at |
|---|---|
| `frontend/app/compliance/[slug]/page.tsx` | 256–261 |
| `frontend/app/regulations/[slug]/page.tsx` | 341–346 |
| `frontend/app/circulars/[slug]/page.tsx` | 158–162 |
| `frontend/app/judgments/[slug]/page.tsx` | 413–417 |

Insert `الأنظمة المذكورة` then `اقرأ تاليًا` immediately **above** the widget, so the CTA stays the
last thing before the footer.

### 6.5 Types

`frontend/types/library.ts` and `frontend/lib/library/api.ts`: add `related_next` to the four doc
interfaces, retype `cited_regulations` on `JudgmentDoc`, add `cited_regulations` to
`RegulationDoc`. Run `npx tsc --noEmit`.

### 6.6 Numerals

Any count rendered in this UI uses **Latin digits** — the app-wide policy is ESLint-enforced
(`latin_numerals_policy`). Agent output and corpus body text are the only carve-outs, and neither
applies here.

---

## 7. Wave D — calibration

Not optional, and not something to eyeball in production.

1. Run `refresh_related_axis_weights()` + `refresh_related_items()` for all four corpora.
2. Distribution check: score histogram per corpus; count of sources with 0 / 1–2 / 3–6 / 7+
   surviving candidates **after** the publish filter.
3. **Human sample: 20 random sources per corpus**, dump source title + its 7 candidates + score +
   `reason`. Read them. The failure to look for is a high-scoring pair that is topically unrelated —
   most likely from a `one_way` `document_relations` edge or a cluster that over-merged.
4. Tune `FLOOR` (start 0.15) and, if needed, the bonus-only guard (start 2).
5. Re-run and re-sample. Record the chosen constants in this file.

### Predicted outcomes — computed read-only before the migration was applied

These are arithmetic, not guesses. If the backfill disagrees with them, the implementation is wrong,
not the prediction.

**Candidate-generation cost** (pairs the generators emit, before scoring drops anything), with
`TARGET_SHARE = 0.007`, `FLOOR = 0.15`, generation threshold `FLOOR/2 = 0.075`:

| generator | axis values passing | pairs emitted |
|---|---|---|
| judgment · court | 26 of 29 | 2,218,000 |
| judgment · entity | 1 of 4 | 50,625 |
| regulation · sector | 8 of 38 | 32,013 |
| circular · sector | 22 of 37 | 7,497 |

Single-digit millions total. The guard is doing its job: التجارية alone would have been 20,334² =
**413M pairs**, and it is excluded.

**Judgment coverage after scoring:** **4,461** of 30,531 judgments full-corpus, **1,493 of 10,000
slugged (15%)**, have ≥1 candidate — before the target-must-be-slugged filter, so the rendered
number is lower. Every one of them comes from a هيئة الزكاة والضريبة chamber, لجان التأمينية,
ديوان المظالم — الجزائية, العليا, الاستئناف, العامة or العمالية. **التجارية contributes zero**, which
is 67% of the corpus. This is the designed outcome, quantified.

> **The judgment entity generator is redundant.** `court + entity` clears the floor in **0** cases
> that `court` alone does not — the only entity with any weight (لجان التأمينية, 225) maps
> one-to-one onto a court of the same 225 documents. It emits 50,625 pairs and adds no coverage.
> Harmless and cheap, kept for when the judgment corpus grows a real entity spread; delete it if
> Wave D wants the generation cost back.

> **Court variant-splitting is real, confirmed.** The raw values include «العليا» (124) **and**
> «العليا -  الهيئة الدائمة» (1), plus a stray «التجارية الثالثة» (1). Values with n = 1 produce no
> pairs, so they cost nothing — but they are evidence that `COURT_VARIANTS` normalization would
> merge chambers currently being counted apart. Decide during calibration.

Expected shape for the other three: أنظمة ~50% with ≥1 candidate, تعاميم and خدمات bonus-only and
thin until Wave E.

---

## 8. Wave E — topic-BM25 for تعاميم and خدمات

Separate, ships after A–D. This is the base axis those two corpora otherwise lack.

**Why only those two.** أنظمة already have `core_subjects` — precomputed, clustered, labelled, free
— and at 43.8 topics per نظام, topic matching there is a 44× comparison blowup for an axis that is
already covered. تعاميم have 5 entity values, no citations, no clusters: one topic sentence per
تعميم is thin, but it is the only content signal they will ever have. خدمات are the same case.

Build: tsvector + GIN on `search_topics.topic_text` using the existing `luna_tsvector()` Arabic
pipeline (`111_bm25_search_index.sql`), or new `search_index` rows at topic granularity. Match
topic-to-topic, take the best-scoring match per candidate pair, normalize per §3.3, feed the
core-subject band.

---

## 9. Traps

| Trap | Guard |
|---|---|
| **Migration before deploy.** Code that reads `related_items` 500s if 143 has not been applied. | Apply 143 to prod **before** pushing the backend. |
| **ISR purge is mandatory.** New sections will not appear on baked pages without one — a 200 from the revalidate endpoint lies if the path is not percent-encoded. | `isr_bake_docker_cache_trap`, `isr_revalidate_percent_encoding`. Purge and spot-check a real Arabic slug. |
| **Railway tarballs the dirty tree**, and frontend root is `/frontend` so backend-only commits skip the frontend build. | `railway_master_pull_trap`. Diff every file before `git add`; verify new imports are tracked; boot from a clean clone. |
| **Both relation tables are one-row-per-unordered-pair.** Reading only `(a,b)` silently halves the graph. | Union both directions everywhere. |
| **`parent_law_id` is a duplicate** of `document_relations` (317 ⊂ 746). | Never read it; double-counting inflates the base. |
| **Entity split-brain:** `entity_ref` `5000` (920) + `17573` (366) + `40002` (0) are one body — 33% of أنظمة. | Collapse all three to one key in `refresh_related_axis_weights` before counting. Without it, two mid-size entities each earn a real weight instead of ~0. |
| **`entity_name` is NULL on 44% of أنظمة.** | Key the entity axis on `entity_ref`. Keying on the name silently drops 1,739 rows out of the counts and inflates every remaining weight. |
| **`clean_title` is NULL** on some أنظمة. | `coalesce(clean_title, title)` everywhere, matching the hub lister. |
| **`core_subject_size` ≠ actual members** on 51 of 524 clusters. | Count rows; the column is a build-time figure. |
| **Candidate explosion.** An unguarded self-join is 15.6M pairs for أنظمة, 932M for أحكام. | The scarcity guards in §4.5 steps 4–5 are load-bearing, not an optimization. |
| **PostgREST `in.()` URL length.** | Chunk at `_ID_IN_CHUNK` (150), as the existing resolver does. |
| **Radix `ScrollArea` kills `truncate`/`h-full`** on hub cards. | Native `overflow-x-auto` + scroll-snap. |
| **Anon PostgREST exposure is an open finding.** | RLS on, no policies, grants revoked. The frontend never reads these tables directly. |
| **`topic_map` is empty and `topics` is the sector list.** | Do not build a topic axis against them. |

---

## 10. Success criteria

**Data**

- [ ] `related_items` populated for all four corpora; row counts and per-corpus coverage recorded here
- [ ] `select count(distinct source_id) from related_items where source_type='regulation'` ≈ 1,993 (full-corpus, pre-publish-filter)
- [ ] No self-edges; no row below `FLOOR`; no `source_type <> target_type` (same-type invariant, D2)
- [ ] `related_axis_weights` has 38 sector rows per sectored corpus, 12 court rows, one row per entity
- [ ] `cron.job` contains `related_items_refresh_weekly`
- [ ] `\timing` on a full `refresh_related_items('judgment')` — record it; if it exceeds a few minutes the candidate guards are not biting

**Backend**

- [ ] Every doc endpoint returns `related_next` ≤ 7, all targets slugged
- [ ] نظام pages: no id appears in both `cited_regulations` and `related_next`
- [ ] Judgment `cited_regulations` is one entry per نظام, no unresolved entries, ≤ 7
- [ ] تعاميم and خدمات responses have no `cited_regulations` field
- [ ] Bonus-only guard holds on أنظمة (≤2 with `base = 0`) and is off on أحكام
- [ ] An empty strip returns `[]`, never an error

**Frontend**

- [ ] `npx tsc --noEmit` and `npm run lint` clean
- [ ] Both strips render above `AskRayhanWidget`, below the last content block
- [ ] 3 cards in view on desktop, ~1.15 on mobile, RTL scroll direction correct
- [ ] Empty list → no heading, no empty box
- [ ] Anon (logged-out, hard-refresh) sees the identical strip a paid user sees
- [ ] Latin digits throughout

**End to end**

- [ ] Spot-check one نظام with a confirmed `document_relations` edge, one with only a core-subject
      edge, one with neither → the third shows no strip
- [ ] Spot-check a supreme-court judgment (strip present) and a commercial-court one (absent)
- [ ] Publish one previously-unslugged نظام, purge ISR, confirm it appears in a related page's strip
      **without** re-running the weekly job — this is the D5/D6 acceptance test

---

## 11. Out of scope

- «الأحكام التي طبّقت هذا النظام» (D4)
- `case_topics` vector similarity for أحكام — shelved upgrade, §3.6
- Extracting نظام citations from تعاميم / خدمات prose (D14)
- Behavioural signals ("readers also viewed")
- Any change to the gate, the character budgets, or the enumeration meter
- The `compliance` BM25 corpus — belongs to `compliance_entity_sections.md`
