# Ranking (Sorting) Criteria — what appears on page 1, page 2, page 3…

**Status:** PLAN (nothing built)
**Decides:** the order of `/regulations`, therefore which regulations a visitor and
a crawler see first, and which are buried on page 40
**Surfaces:** `backend/app/services/library_service.py`, `scripts/build_usage_rank.py` (new), migration `115`

---

## 0. What this document is choosing

The hub serves **9 cards per page**. The published set is 100 items → 12 pages;
the full corpus is 3,952 → 440 pages. Page 1 is the only page most visitors and
most crawl budget will ever reach. So "sorting" here is not a preference toggle —
it is the decision of **what the library is, at a glance**.

Everything below is measured against the live database (2026-08-05), not proposed
in the abstract.

---

## 1. The pages we serve today

Order today is **in-force first, then `clean_title`** — three implementations of
one contract in `list_regulations_hub` (`library_service.py:1780`): BM25 when `q`
is present, a Python sort in sample mode (`:1845`), a two-partition DB range
slice in full corpus (`:1857-1884`).

**Live page 1:**

| # | Title |
|---|---|
| 1 | القانون (النظام) الموحد للتعدين لدول مجلس التعاون |
| 2–9 | **eight consecutive «النظام الأساس لشركة … للتأمين التعاوني»** |

Ten of the 100 published regulations are insurance-company incorporation
charters. Alphabetical order puts eight of them on page 1.

In full corpus the same key opens with `(المعايير السعودية…)`, then **five rows
literally titled `<no title>`**, then `2025البيع و التأجير`,
`GSO 05 DS CAC 260: 2019`, and English titles — before the first نظام appears.

Alphabetical cannot work here because titles are type words, not names:
اللائحة 549 · لائحة 426 · دليل 370 · نظام 323 · الدليل 225. Sorting by title
sorts by *document type spelled out*. And `clean_title` is NULL on 1,694/3,952
(43%) — the DB path orders on `clean_title` alone (`:1865`, `:1874`, `:1883`)
while the sample path coalesces to `title` (`:1739`), so the two paths claiming
one contract do not agree.

---

## 2. The pages this plan produces

Computed by running §4 + §5 against the live published set:

**Page 1**

| # | Score | Entity | Title |
|---|---|---|---|
| 1 | 50.55 | 17642 | نظام المعاملات المدنية |
| 2 | 37.00 | 17642 | نظام المرافعات الشرعية |
| 3 | 32.75 | 17642 | نظام الإثبات |
| 4 | 28.60 | 17486 | نظام التنفيذ |
| 5 | 26.95 | 17609 | نظام العمل |
| 6 | 21.40 | 17573 | نظام الإجراءات الجزائية |
| 7 | 20.80 | 17642 | نظام الأحوال الشخصية |
| 8 | 18.50 | 17642 | نظام المحاكم التجارية |
| 9 | 11.60 | 17606 | نظام الشركات |

**Page 2** — نظام المرور · نظام المرافعات أمام ديوان المظالم · نظام السجل التجاري ·
نظام حماية البيانات الشخصية · نظام التنفيذ أمام ديوان المظالم · نظام التعاملات
الإلكترونية · نظام التجارة الإلكترونية · نظام التأمينات الاجتماعية · نظام المحاماة

**Page 3** — نظام مكافحة جرائم المعلوماتية · نظام الإفلاس · نظام الحماية من الإيذاء ·
نظام ضريبة القيمة المضافة · نظام الأحوال المدنية · نظام التسجيل العيني للعقار ·
نظام مكافحة المخدرات · النظام الموحد لرعاية أموال القاصرين · نظام التكاليف القضائية

The insurance charters fall to the zero-usage block around rank 60 — page 7+.

---

## 3. The signal — `workspace_item_references`

Every source the deep-search pipeline put before the writer, and whether the
writer used it.

| Column | Meaning |
|---|---|
| `wi_id` | the workspace item (answer) the reference belongs to |
| `item_id` | **`chunks_v2.id` — a مادة chunk, NOT a `regulations_v2.id`** |
| `used` | the writer actually cited it in the final answer |
| `relevance` | reranker verdict — `high` / `medium` only |

### 3.1 ⚠ The join is chunk-level

`item_id` joins `chunks_v2.id`. Joining it to `regulations_v2` returns **zero
rows** — silently, no error. Roll up via `chunks_v2.regulation_id`.

### 3.2 Measured state

| Metric | Value |
|---|---|
| Regulation refs | 3,205 (2,809 `used`) |
| Refs joining `chunks_v2` | 2,941 — **264 orphans (8.2%)** from corpus re-ingest |
| Distinct chunks referenced | 1,645 (1,385 used) |
| **Distinct regulations after roll-up** | **462 of 3,952 (11.7%)** — 445 used |
| Distribution | 181 cited once · 148 cited 2–4× · 133 cited 5+ · max 213 |
| Published rows with usage | 59 of 100 |

**Resolution warning:** only the top ~130 regulations have enough citations to
separate them. Below that the score is a near-tie, which is exactly the region
§5 governs.

### 3.3 ⚠ Provenance — pipeline traffic, not market demand

All 3,205 references come from **9 distinct users**, 275 conversations,
2026-05-25 → 2026-08-03 — the dev and demo accounts. The honest name for this
metric is *"how often the retrieval pipeline surfaces this regulation for legal
questions"*. It is a strong bootstrap prior (the page 1 in §2 is the correct
page 1 for a Saudi legal library) but it must be recomputed as real traffic
arrives, and scored so one account cannot define the library (§4.2).

Zero usage means **untested, not unimportant** — 3,490 regulations sit there.

---

## 4. The score

### 4.1 Per-reference quality points

| `used` | `relevance` | points |
|---|---|---|
| true | high | 1.00 |
| true | medium | 0.60 |
| false | high | 0.30 |
| false | medium | 0.15 |

### 4.2 Dampening — conversation, then user

```
conv_score(reg, conv) = min(1.0, Σ points for reg in that conversation)
user_score(reg, user) = Σ over that user's conversations
usage_score(reg)      = Σ over users of min(user_score, USER_CAP)
```

A conversation votes **at most once** — breadth of questions beats depth of one.
`USER_CAP` (default 10) stops one account owning the ranking; it binds at 9
users and goes inert as traffic grows. Both are CLI flags, not literals.

### 4.3 Intra-tie prominence (the fallback key, never primary)

| Term | Source | Populated |
|---|---|---|
| authority weight | `legal_authority` JSON → `authority_level` (binding_law 9.4 · implementing_regulation 6.1 · support_guidance 4.1) | 1,212 (31%) |
| doc-type weight | `doc_type_bucket` (law_statute / executive_regulation ≫ guide / unspecified) | 100% |
| status | `status_class` (in_force → amended → consultation → cancelled) | 100% |
| junk-title penalty | `clean_title` NULL (1,694) · `<no title>` · Latin/digit/punct start (13) · charter family | 100% |
| depth | `chunks_v2` count (545 ≥20 · 1,809 mid · 1,598 <5) | 100% |

**`start_date` must not be used.** It is populated on 1,009 rows and *all 1,009
are consultation rows* — zero in-force regulations carry a date. "Newest first"
would rank drafts above every enacted نظام.

---

## 5. Two segments, one interleave

Ties are not spread across the score range — they are almost entirely the
bottom of it:

| Band | Regulations | Share |
|---|---|---|
| score = 0 | 3,490 | 88.3% |
| 0 < score ≤ 1 | 258 | 6.5% |
| score > 1 | 204 | 5.2% — 67 distinct scores, 158 in 21 ties |

So there is no need for per-tie-group machinery. **The list is two segments:**

1. **Head — `score > 1` (204 rows, ~23 pages).** Order by `score desc`, then
   §4.3 prominence, then `id`. No interleave. Usage earned these positions.
2. **Tail — `score ≤ 1` (3,748 rows).** One entity round-robin over the whole
   segment, with score kept as the leading key so the 258 low-scored rows stay
   ahead of the 3,490 zeros.

`TAIL_THRESHOLD = 1.0` is a CLI flag.

### 5.1 The interleave is a greedy max-heap — NOT a round-robin

`entity_ref` is 100% populated, 135 distinct. **Key on `entity_ref`, never
`entity_name`** (NULL on 44% of rows — a name-keyed interleave would collapse
every unnamed issuer into one bucket).

Without it the tail is 836 consecutive cards from entity `5000`, then 396 from
`17405`, then 304 from `17573`.

⚠ **A middle draft of this plan specified a window-function round-robin — slot 1
of every entity, then slot 2, rounds ordered by `bucket desc`. That is wrong on
a skewed distribution, and wrong in the way that is hardest to catch: it fails
silently, at the end of the list, where a dry-run sample never looks.** Entity
5000 has 836 rows and the next largest has 396, so from slot 397 onward 5000 is
the only bucket left and its final ~440 rows come out consecutive — exactly the
clustering the rule exists to prevent. The 14-row live sample the draft was
verified against sat in the first few rounds, where every bucket is still alive,
so it looked correct.

The implementation is therefore the greedy construction: repeatedly take from
the largest REMAINING bucket whose entity differs from the previously placed
one. It rebalances as buckets drain, and it is optimal — a no-two-adjacent
arrangement exists iff the largest bucket is at most `ceil(n/2)`, and this finds
one whenever it exists. See `interleave_by_entity()` in
`scripts/build_usage_rank.py`.

Determinism holds: buckets are pre-sorted on `(score, prominence, id)` and heap
ties break on `entity_ref`, so a re-run over unchanged data reproduces the order
exactly — measured, `rank churn: 0 of 503`.

**Measured on the live 503-row published set: 0 same-entity adjacent pairs in
the tail.** The 35 adjacent pairs in the final order are all in the head, by
design (§5.2).

### 5.2 ⚠ The head is deliberately not interleaved

On the measured page 1, positions 1, 2, 3 and 7, 8 are all entity `17642`.
المعاملات المدنية, المرافعات الشرعية and الإثبات are the three most-cited codes in
Saudi practice and they share an issuer. Forcing diversity there would demote
them for a *worse* signal. The rule buys its value where evidence runs out.

Ties inside the head (biggest is 39 rows) break on prominence then `id` — if
that leaves two same-entity rows adjacent, as ranks 22–23 do at score 4.60, that
is accepted, not corrected.

If entity spread on page 1 is wanted anyway, that is a separate soft constraint
(max-N-per-page with bounded displacement), not this rule — §9.

### 5.3 ⚠ Intra-bucket order matters as much as the interleave

The first card of the zero block in the measured run is «النظام الأساس للشركة
الخليجية العامة للتأمين التعاوني» — because round-robin starts with the largest
bucket and the charter sorted first *within* entity 5000. The §4.3 charter-family
penalty must push charters to the back of their own bucket, or the interleave
faithfully spreads them into every round.

### 5.4 ⚠ Adjacency survives only in the unfiltered list

The interleave bakes into one global linear order; any filter takes a
*subsequence*, and subsequences of a non-adjacent arrangement are not guaranteed
non-adjacent.

* `entity=5000` → every item is that entity. Meaningless by definition, not a bug.
* Sector sections (`/library/{sector}/{type}`) — where deep pagination lives —
  can re-cluster. **Open decision, §9.**

---

## 6. Storage

`seo_item_meta` already holds one row per regulation (3,319 of 3,952) on PK
`(content_type, content_id)` with `slug` / `seo_tier` / `gate_override`.

**Migration `115_library_usage_rank.sql`:**

```sql
alter table seo_item_meta add column if not exists rank integer;
alter table seo_item_meta add column if not exists usage_score numeric;
create index if not exists seo_item_meta_rank_idx
  on seo_item_meta (content_type, rank) where slug is not null;
```

`rank` is dense 1..N per `content_type`. `usage_score` is audit-only — never
ordered on directly, or float ties reintroduce the arbitrary order §5 removes.

---

## 7. Wiring — three code paths collapse to one

The two-partition straddle exists **only** because no single column expresses the
contract. With `rank`:

```python
q = supabase.table("regulations_v2").select(_REG_HUB_SELECT)
q = _apply_reg_filters(q, entity, doc_type, sector)
q = q.order("rank").order("id").range(offset, offset + ps - 1)
```

* Sample mode and full corpus become the **same query** — `_reg_hub_sort_key`
  (`:1734`) and the `count_a` in-force sub-count both delete.
* BM25 mode untouched — relevance still replaces rank for a search.
* `regulations_hub_total_pages` loses its in-force partition count.

---

## 8. `scripts/build_usage_rank.py`

Follows `build_seo_slugs.py` conventions: repo-root import shim, UTF-8 stdout,
**`--dry-run` is the DEFAULT**, MERGE-upsert on the composite PK so `slug` /
`seo_tier` / `gate_override` survive untouched.

```
python scripts/build_usage_rank.py                     # dry-run: pages 1-5, tie stats, churn diff
python scripts/build_usage_rank.py --user-cap 10 --apply
python scripts/build_usage_rank.py --articles          # مادة-level output (§8.1)
```

Dry-run must print **the first five pages as they would render**, tie-group
sizes, per-entity concentration of the zero group, adjacency-violation count, and
a **diff against the current rank** so churn is reviewable before it is written.

### 8.1 Free second output — which مادة to publish

References are chunk-level, so the same aggregation ranks individual مواد by
citation count. Only **5 articles are published** today and `publish_articles.py`
takes a hand-made list. This gives it a ranked queue: the most-cited مادة across
275 conversations, parent regulation already published.

---

## 9. Open decisions

1. **Rank read path** — join `seo_item_meta` in the hub query (second round-trip
   per page) or denormalise `rank` onto a helper the corpus query orders on (sync
   step)?
2. **Per-section rank** — one global order (v1) vs. an extra interleave per
   `(sector, type)`, which is where deep pagination actually lives (§5.4).
3. **Head diversity** — accept §5.2 (recommended) or add a soft max-N-per-page
   entity cap with bounded displacement?
4. **Zero-usage floor** — should no-usage + junk-title rows be rank-suppressed
   below everything, or excluded from publishing via `seo_tier`?
5. **`USER_CAP`** — 10 proposed; revisit at ~100 real users.

---

## 10. BUILD STATE (2026-08-05) — built, DB live, backend NOT deployed

| Piece | State |
|---|---|
| Migration `116_library_usage_rank.sql` | **APPLIED to prod DB** — `rank`/`usage_score` on the sidecar, `library_regulations_ranked` view, `library_reg_usage_refs()` + `library_reg_chunk_counts()` RPCs |
| `scripts/build_usage_rank.py` | Built. Dry-run default, idempotent (re-run churn = 0) |
| `scripts/build_seo_slugs.py --ids-file` | Built — publishes a chosen id set instead of the whole corpus |
| `list_regulations_hub` rewire | **Written, tested locally, NOT DEPLOYED** |
| Published set | **166** (see §10.2) — snapshot of all 503 slugs saved off-repo |

### 10.1 ⚠ PUBLISH AND DEPLOY ARE COUPLED — SEQUENCE THEM

`SAMPLE_MODE_MAX_IDS` was 300. Publishing 503 rows crossed it on the DEPLOYED
code, which flipped `_published_ids()` to `None` → corpus pagination → every
unslugged row dropped → **prod `/regulations?page=1` returned `items: 0,
total_pages: 440`**. Confirmed live, then rolled back.

The rewired lister does not have this failure mode (it paginates the
published-only view at any size), and the constant is now 1000. But the order is
still load-bearing:

> **deploy the backend FIRST, publish SECOND, rank THIRD.**

Publishing before deploying breaks the wing for as long as the gap lasts.

### 10.2 Current published set and how to finish

Rolled back to **166** = all `doc_type_bucket='law_statute'` published rows. That
is a strict SUPERSET of the original 100 (which were all `law_statute`), so no
previously-live URL 404s, and it is under the deployed 300 ceiling, so prod
serves correctly right now — verified: 9 items, 19 pages.

To finish after the backend deploys:

```
python scripts/build_usage_rank.py --emit-used-ids ids.txt
python scripts/build_seo_slugs.py --type regulation --ids-file ids.txt --apply
python scripts/build_usage_rank.py --apply
# then purge ISR: /api/revalidate
```

That republishes to 503. Slugs regenerate deterministically (corpus loads in
`id` order, collisions resolve identically), and an exact snapshot of all 503
`(content_id, slug, rank, usage_score)` rows was exported before the rollback if
byte-identical restoration is wanted instead.

### 10.3 `SAMPLE_MODE_MAX_IDS` 300 → 1000

The regulations LISTER no longer reads it. But `_published_sample_counts` still
does, and it feeds the SECTOR COUNTS — crossing the ceiling flips those onto the
corpus path, where a sector reporting 695 rows of which 0 are servable gets
prerendered as a static, indexable, EMPTY page (the soft-404-at-scale failure
documented in `library_service.py`). Past ~1000 published rows those counts need
their own RPC over the ranked view; the id-list scan is not meant to grow
indefinitely.

---

## 11. Traps

* **`item_id` is a chunk id** — joining to `regulations_v2` returns zero rows
  silently. Roll up via `chunks_v2.regulation_id`.
* **264 orphan refs (8.2%)** point at chunk ids no longer in `chunks_v2`. Count
  and report; never let them fail the run.
* **Key on `entity_ref`, not `entity_name`** (§5).
* **No unseeded `random.shuffle()`.** "Shuffle" here means deterministic
  interleave. A per-run random order on an ISR-cached paginated surface means
  page-2 items reappearing on page 3 and duplicate-content signals across ~440
  pages. Intra-bucket order is `(prominence, id)`; any random component must be
  seeded from the regulation id, never the clock.
* **`start_date` is a trap** (§4.3) — 100% of dated rows are consultations.
* **ISR purge after every `--apply`.** A rank change reorders every hub page;
  without `/api/revalidate` the frontend serves the old order from the Data Cache
  indefinitely (`project_isr_bake_docker_cache_trap`).
* **Rank is a snapshot** of a continuously growing table. Weekly manual run to
  start; the §8 churn diff is what makes recomputes reviewable.
