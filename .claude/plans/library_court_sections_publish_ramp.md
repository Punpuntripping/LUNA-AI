# Library — court sections + publish ramp

**Status:** **CODE BUILT 2026-08-09** · migrations NOT applied · nothing published · not deployed
**Scope:** `/judgments` court routes · judgments 100 → ~10,000 published · regulations 502 → 1,188 published

## Build state (2026-08-09)

| Piece | State |
|---|---|
| `shared/library/courts.py` — 12 buckets, 29 raw variants | BUILT · exact bijection with the live corpus verified |
| Migration 123 `library_judgments_ranked` | WRITTEN · **not applied** · 40 cols verified against `information_schema` |
| Migration 124 `library_sector_counts_published()` | WRITTEN · **not applied** · signature verified against live `pg_proc` |
| Backend: lister on ranked view, court section, counts endpoint | BUILT · 1,357 tests pass (2 failures pre-existing, unrelated) |
| Frontend: 12 routes, switcher, TS mirror | BUILT · `tsc` + lint clean · Arabic slug encoding proven on the wire |
| `build_judgment_slugs.py` — per-feed allocation | BUILT · dry run lands 7,776/1,000/1,000/224 = 10,000 exactly |
| `build_entity_quota_ids.py` + `--unpublish --ids-file` | BUILT · dry run lands 686 new / 1,188 total, 135/135 entities |

**Nothing is applied, published, or deployed.** Every publish tool ran dry-run only.

⚠ **`next build` has NOT been run.** Both new court routes carry `generateStaticParams`
*and* read `searchParams`; Next 15 bails such a segment to request-time rendering (which is
what `/judgments` already does) and dev compiled both cleanly, but only a production build
proves it. Run it before deploying. If it objects, drop `generateStaticParams` — the 12
routes resolve on demand regardless — rather than dropping `searchParams`.

⚠ **`test_library_judgments.py` and `test_build_judgment_slugs.py` were git-ignored**
(`.gitignore:19` ignores `backend/tests/*` with per-file `!` exceptions). That is why the
ranked-view rewire broke 8 judgment tests while CI stayed green. Both now have exceptions.
Any new test file needs one or it silently never runs in CI.

Related: [[project_seo_public_library]] · [[project_judgments_wing]] · [[project_library_usage_rank]] ·
[[project_library_sectors]] · [[project_access_tiers_gating]] · `.claude/plans/ranking_criteria.md`

---

## 0. The blocker — read this before anything else

**Publishing more than 1,000 items into any wing breaks that wing today.**

`SAMPLE_MODE_MAX_IDS = 1000` (`backend/app/services/library_service.py:154`). Above it
`_published_ids()` returns `None` and the wing falls to its legacy corpus-pagination path.
For judgments that path is `list_judgments_hub` `:4268-4283` — it pages all **30,531**
`cases` rows, then silently discards unslugged rows at `:4291-4293`. The docstring at
`:4180-4183` names the assumption it rests on: *"every judgment is slugged then."*

At 10,000 of 30,531 published that assumption is false. Each 9-card page would render ~3
cards and `total_pages` would come from the corpus count — ~3,393 pages, mostly holes.

This already happened once: 2026-08-06, publishing 503 regulations crossed the then-300
ceiling and prod `/regulations` returned `items: 0`. It was fixed for regulations only, with
`library_regulations_ranked` (migration 116) — a published-only view. **Judgments, circulars
and services have no equivalent.**

Going above 1,000 is a deliberate decision taken by the user 2026-08-08. It makes §1 work
mandatory, not optional.

---

## 1. Foundation — published-only views + published-aware counts

### 1.1 `library_judgments_ranked` view (new migration)

Mirror `shared/db/migrations/116_library_usage_rank.sql:88-134`:

```sql
create or replace view public.library_judgments_ranked as
select c.*, m.slug, m.rank, m.usage_score
from public.cases c
join public.seo_item_meta m
  on m.content_type = 'judgment' and m.content_id = c.id::text
where m.slug is not null;
revoke all on public.library_judgments_ranked from anon, authenticated;
grant select on public.library_judgments_ranked to service_role;
```

`cases` is pipeline-owned — read only, never ALTER. Same rule as `regulations_v2`.

### 1.2 Rewire the judgments lister off `_published_ids`

`library_service.py` — `list_judgments_hub` `:4207`, `_judgment_count` `:4159`,
`judgments_hub_total_pages` `:4170`:

- Introduce `_JUDGMENT_HUB_TABLE = "library_judgments_ranked"` beside
  `_REG_HUB_TABLE` (`:2001`).
- Delete the sample-mode branch (`:4256-4267`) and the `_published_ids` call at `:4247`.
  One `.order(...).range(...)` over the view — every row is published by construction, so
  no page can come back short.
- Keep the ordering that exists today: `date_gregorian` DESC nulls last, then `id`.
  The `_slug_map` round-trip at `:4287` becomes redundant (the view carries `slug`) — drop it.
- Search mode (`q`) is unchanged; BM25 already returns published-scoped ids.

⚠ The ديوان / زكاة / تأمين feeds have `date_gregorian = NULL` on **every** row. Under
date-desc-nulls-last they all sort behind the 19,112 dated MOJ rows. Inside a court section
that is fine (a section is homogeneous). On the unfiltered `/judgments` hub it means
~11,400 published judgments are unreachable before page ~800. Accepted: the court sections
are the intended entry point for those feeds.

### 1.3 Published-aware counts RPC

`sector_counts` (`library_service.py:1873-1928`) and `library_corpus_counts` (`:1859`) call
`_published_sample_counts` (`:1807-1842`) while under the ceiling, and fall through to
`library_sector_counts()` (migration 109) above it. **That RPC counts corpus rows, not
published rows.** Crossing 1,000 therefore makes `/library` report 3,951 regulations and
30,531 judgments — the counts stop measuring what is servable, which the block comment at
`:1740-1796` says is the whole contract.

Add `library_sector_counts_published()`: same shape, but each corpus table inner-joined to
`seo_item_meta` on `(content_type, content_id)` with `slug is not null`. Point
`_published_sample_counts`'s fallthrough at it and delete the id-list scan for wings that
have a ranked view.

`SAMPLE_MODE_MAX_IDS` stays for circulars/services (both at 100 published, untouched here).

### 1.4 Drift fixes bundled in

- `scripts/build_judgment_slugs.py` hardcodes the retired **300** ceiling at `:42`,
  `:833-837`, `:851-852` — its post-run warning fires ~700 rows early. Update to read the
  live constant or drop the warning entirely once §1.2 lands.
- `library_service.py:1532` and `:1589` docstrings still say "(300)" above a constant of 1000.

### 1.5 Rollback tooling (new)

`build_seo_slugs.py` can only add — there is **no un-publish path** for regulations,
circulars or services. The documented 503→166 rollback used ad-hoc SQL that was never
committed. Add `--unpublish --ids-file <path>` (sets `slug = NULL` scoped to
`content_type` + the given ids, batched 500, `--apply` gated, never a DELETE so
`seo_tier`/`gate_override` survive). Mirrors `publish_articles.py:276-279`.

Non-negotiable before §3 publishes 686 new regulations.

---

## 2. Court sections on `/judgments`

### 2.1 URL shape

`/judgments/courts/{court-slug}` and `/judgments/courts/{court-slug}/page/{n}`.

`courts` is a **static** segment, so it resolves ahead of the existing `[slug]` document
route — the same mechanism that already lets `/judgments/page/2` coexist with
`/judgments/{judgment-slug}`. Next cannot host `app/judgments/[court]/` alongside
`app/judgments/[slug]/` (build-time error: two dynamic names at one level), so the extra
segment is what makes the user's requested shape legal. **Existing document URLs do not move.**

Court slugs are **Arabic** (`المحكمة-التجارية`), per the user's example. This departs from
the "Latin for structural path segments" rule recorded in
`.claude/plans/library_sectors.md` — accepted because that rule's justification was SEO
neutrality and this wing is `noindex`. To reverse, change only the slug column in §2.2.

### 2.2 `shared/library/courts.py` (new) — the closed vocabulary

Mirrors `shared/library/sectors.py:38-90`: ordered by corpus volume (that order is the
browse order), reconciled against the live distinct set, **log-and-omit on drift, never raise**.

`cases.court` is raw free text — 30 distinct values, no normalizer exists anywhere in the
repo (grepped backend/, shared/, frontend/lib/). This module *is* the normalizer. It maps
each bucket to its list of raw variants; the query predicate is `in.(variants)`, not a
regex. City never appears in a bucket label.

| # | Slug | Label | Raw variants | Rows | Sectors | Mesh |
|---|---|---|---|---|---|---|
| 1 | `المحكمة-التجارية` | المحكمة التجارية | 2 | 20,335 | 20,335 | 16,863 |
| 2 | `ديوان-المظالم-تجارية` | ديوان المظالم — الدائرة التجارية | 1 | 2,625 | 0 | **0** |
| 3 | `اللجان-الضريبية-عام` | اللجان الضريبية والزكوية — عام | 6 | 2,281 | 0 | 1,687 |
| 4 | `ديوان-المظالم-إدارية` | ديوان المظالم — الدائرة الإدارية | 1 | 1,879 | 0 | **0** |
| 5 | `لجان-ضريبة-القيمة-المضافة` | لجان ضريبة القيمة المضافة | 6 | 1,622 | 0 | 499 |
| 6 | `لجان-ضريبة-الدخل-والزكاة` | لجان ضريبة الدخل والزكاة | 6 | 1,063 | 0 | 146 |
| 7 | `لجان-التأمين` | لجان الفصل في المنازعات التأمينية | 1 | 225 | 0 | **0** |
| 8 | `ديوان-المظالم-جزائية` | ديوان المظالم — الدائرة الجزائية | 1 | 165 | 0 | **0** |
| 9 | `المحكمة-العليا` | المحكمة العليا | 2 | 125 | 125 | 117 |
| 10 | `محكمة-الاستئناف` | محكمة الاستئناف | 1 | 106 | 106 | 77 |
| 11 | `المحكمة-العامة` | المحكمة العامة | 1 | 69 | 69 | 58 |
| 12 | `المحكمة-العمالية` | المحكمة العمالية | 1 | 35 | 35 | 33 |

One `court = ''` row is excluded from the vocabulary and reachable only via `/judgments`.

**Facts the table encodes, each one load-bearing:**

- **City is stripped** — six VAT variants differing only by جدة/الرياض/الدمام collapse into
  row 5, six income-tax variants into row 6. This is the user's «لجان الفصل في الرياض =
  لجان الفصل» requirement.
- **Rows 3, 5, 6 are the tax-type split the user asked for, and it only reaches 54% of the
  tax corpus.** 2,281 ZATCA rows carry court strings that name no tax type at all
  («اللجنة الابتدائية الأولى», «اللجنة الاستئنافية», «لجنة الفصل الضريبي/الزكوي») — there is
  nothing in the field to split on. Row 3 is that unavoidable residual, and it holds the
  *best* ZATCA content (1,687 with citation mesh, more than VAT and income combined).
  Splitting it further requires classifying from the judgment body — an LLM pass, out of scope.
- **Rows 2, 4, 7, 8 have zero citation mesh and zero sectors** (4,894 rows). ديوان المظالم
  carries unstructured `cited_laws_text` on 3,239 rows instead of the structured
  `referenced_regulations` the mesh reads; لجان التأمين has neither. These sections render
  without judgment→regulation links. Known and accepted.
- **Row 12 has 35 judgments corpus-wide.** Shipped anyway (user default): the route is
  honest, costs nothing, and stands as a visible marker that labour judgments need sourcing.
  That sourcing is a scraping job and is **not** in this plan.
- **Rows 9, 10 overlap the existing `court_level` chips** — `العليا`/`الاستئناف` are court
  *levels* leaking into the `court` column on the MOJ feed only. Hence the facet is labelled
  **«الجهة القضائية»**, never «نوع المحكمة», and the two controls compose rather than contradict.

### 2.3 Backend

Follow the sector precedent exactly (`public_library.py:902-962`) — **no new endpoint.**
`/library/{sector}/{type}` reuses each wing's hub with a validated `sector_slug`; that is
what makes it inherit gating, metering and depth caps unchanged. Forking a hub per court
would fork all of it 12 ways.

1. `_COURT_VOCAB` + `_court_section(court_slug)` beside `_sector_section` — validates,
   400s on unknown/reserved, returns `(variants, slug)`.
2. `list_judgments` (`:2297`) gains a `court` query param.
   **It must stay OUT of the `filtered` flag at `:2340`**, exactly as `sector_key` does.
   `filtered` triggers the enumeration-oracle clamp (`public_library.py:414-456`,
   `_visible_total_pages:621-637`) which pins anon `total_pages` to 2. A closed,
   server-owned vocabulary is a **section**, not a filter — its counts move only when the
   corpus does. This is the whole reason for §2.2's design.
3. `_apply_judgment_filters` (`library_service.py:4118-4138`) gains
   `qb.in_("court", variants)`. Thread through `_judgment_search_rows` `:4141`,
   `_judgment_count` `:4159`, `judgments_hub_total_pages` `:4170`, `list_judgments_hub` `:4207`.
4. Per-court counts + `total_pages` memoised beside `_sector_counts_memo`
   (`public_library.py:468-534`, 5-min TTL). Expose `GET /public/library/judgments/courts`.
5. `cases.court` is already an indexed BM25 facet (`search_service.py:104-114`; migration
   112 weights it as the issuing entity at weight B) — the **search** path can already
   filter by court. Only browse needs this work. Reuse the facet for in-section search.

### 2.4 Frontend

- `app/judgments/courts/[court]/page.tsx` + `.../page/[n]/page.tsx`.
  `generateStaticParams` over the 12 slugs (mirror `app/library/[sector]/[type]/page.tsx:39-48`).
- A court switcher rendered on `/judgments` and on every court page — copy
  `SectorBrowseGrid` / `SectorSwitcher`, not the chip row (12 entries is past chip density).
- `JudgmentsFilters` (`lib/library/api.ts:508-524`) gains `court?`. `buildQuery` `:579-595`
  needs no change (it forwards truthy keys verbatim); `hub-query.ts` needs none either.
- ⚠ `JudgmentsHubView.tsx` — add `court` to **both** `browseFilters` (`:49-57`) **and** the
  `query` string at `:64-67`. Miss the second and pagination silently drops the court.
- `robots: NOINDEX_PDPL` on the new routes, same as the three existing judgment routes.
  No sitemap section — the `TODO(pdpl)` blocks at `public_library.py:116-126` and
  `frontend/lib/seo/sitemap.ts:32-39` stay closed.

---

## 3. Publish ramp

### 3.1 Judgments — 100 → ~10,000

Selector is `scripts/build_judgment_slugs.py`. Keep its engine: sqrt-weighted court
allotment (`_court_allowances:463-500`), `_COURT_CAP_FRAC = 0.35`, 60/30/10 level mix, the
`short_summary + facts + ruling + reasoning` eligibility gate. Eligibility is not a
constraint — **29,425 of 30,531 rows pass**.

Feed allocation (user-specified):

| Feed | Corpus | Eligible | Publish |
|---|---|---|---|
| لجان التأمين | 225 | 224 | **all 224** |
| ديوان المظالم | 4,669 | 4,501 | ~1,000 |
| لجان الزكاة والضريبة | 4,966 | 4,934 | ~1,000 |
| وزارة العدل | 20,671 | 19,766 | remainder (~7,775) |

Two selection inputs the user asked for, with their real limits:

- **"use the already used"** — `workspace_item_references` has referenced **229 distinct
  judgments, ever**, and they came from 9 dev/demo accounts. It ranks a top-229; it cannot
  select 10,000. Applied as a **bonus on `_quality_score`**, never as the sort key.
- **"diversify by the sectors"** — `legal_domains` is populated **per source feed, not per
  row**: all 20,671 MOJ rows have it, all 4,669 ديوان + 4,966 زكاة + 225 تأمين rows have
  **zero**. Sector diversification can therefore only steer the MOJ slice. That is where
  the bulk sits, so the constraint is tolerable — but the existing `_DOMAIN_CAP_FRAC = 0.30`
  soft ceiling must be computed **within the MOJ allocation**, not globally, or it will
  read the domain-less feeds as a diversity win.

Order: §1 deployed → `--limit 10000` dry run → review the distribution report → `--apply`
→ purge ISR.

### 3.2 Regulations — 502 → 1,188

**Rule (user decision 2026-08-08): `quota_e = min(entity_regs, max(3, ceil(0.25 × entity_regs)))`,
applied to all 135 entities, additive.** An entity already above quota keeps what it has —
nothing is un-published. `new_e = max(0, quota_e − published_e)`. Total: **686 new, 1,188 published.**

⚠ **The `min(entity_regs, …)` bound is load-bearing and was missing from the first draft of
this plan.** 49 entities own fewer than 3 regulations, so an unbounded floor of 3 demands 85
documents that do not exist. Written as `max(3, ceil(0.25 × n))` the rule computes **771 new /
1,273 total**; bounded it computes 686 / 1,188. The bounded figure is the one that was priced
and approved. Implemented in `quota_for()` and documented there — without the note someone
will "simplify" the clamp back out.

No per-entity ceiling. Considered and rejected: `build_usage_rank.py` already interleaves
the tail by entity with a greedy max-heap (`interleave_by_entity:331-376`), so a large
published set for one entity does not dominate browse order; and the entity a ceiling would
bind, `5000 — أنظمة عامة (هيئة الخبراء بمجلس الوزراء)` (920 regs → 230 quota, 124 new), is
the general-statutes bucket. It *should* be the largest allocation.

Largest allocations:

| Entity | Regs | Published | Quota | New |
|---|---|---|---|---|
| أنظمة عامة (هيئة الخبراء بمجلس الوزراء) | 920 | 106 | 230 | 124 |
| الهيئة العامة للغذاء والدواء | 411 | 15 | 103 | 88 |
| هيئة الخبراء بمجلس الوزراء | 366 | 73 | 92 | 19 |
| البنك المركزي السعودي | 198 | 23 | 50 | 27 |
| هيئة التأمين | 155 | 12 | 39 | 27 |

Every one of the 135 entities ends up represented; **85 currently have zero**.

**Within an entity's quota**, pick: `status_class = 'in_force'` → usage rank →
`doc_type_bucket` (`law_statute` / `executive_regulation` above `guide` / `standard_spec`)
→ article count. Entities with nothing in force fall back to whatever they hold, so
representation never fails.

⚠ **Do not select by `reg_ref` suffix.** `_reg_001` is scrape order, not importance:
نظام العمل is `17609_reg_122`; `17900_reg_001` is «الدليل الإجرائي لقرار توطين المهن
الهندسية»; entity `5000` uses a different scheme entirely (`5000_regulation_0002`) ordered
by Hijri year, so its "first two" are نظام توحيد المملكة (1351هـ) and نظام الإقامة (1371هـ).

`entity_name` is NULL on 1,739 of 3,951 rows in the `regulations_v2` view (37 entity_refs),
but `public.entities` (400 rows) covers **every** `entity_ref`. Join it; never group by the
view's own `entity_name`.

New script `scripts/build_entity_quota_ids.py` → emits an id file →
`build_seo_slugs.py --type regulation --ids-file … --apply`.

### 3.3 Sequencing — load-bearing, from `.claude/plans/ranking_criteria.md:353`

**Deploy backend FIRST → publish SECOND → rank THIRD → purge ISR FOURTH.**

`build_usage_rank.py` reads `library_regulations_ranked`, i.e. published rows only — a rank
run before the publish cannot see the new rows. Unranked rows are not broken meanwhile:
PostgREST `.order()` asc is NULLS LAST, so they queue at the back (`library_service.py:2065-2069`).

ISR purge is **manual and mandatory**. `build_seo_slugs.py` and `build_judgment_slugs.py`
do not POST `/api/revalidate` and do not mention it; only `publish_articles.py` and
`set_gate.py` do. Skipping it means the hub serves the pre-publish bake
([[project_isr_bake_docker_cache_trap]]). Purge `/judgments`, every
`/judgments/courts/{slug}`, `/regulations`, `/library`, and their `page/{n}` variants.

---

## 4. Out of scope

- **PDPL audit and the `noindex` lift.** All judgment routes stay `noindex, nofollow` with
  no sitemap section. Live scan of the corpus: **609** bodies contain a 10-digit
  national-ID-shaped number, **85** an `05…` mobile, **4,018** mention هوية/السجل المدني,
  2 an IBAN. Scaled to a 10k publish ≈ 200 ID-shaped numbers and ~1,300 identity mentions.
  Under `noindex` this publish buys **in-app library depth, not search traffic** — that is
  the user's stated intent (2026-08-08). Masking assets exist:
  `.claude/plans/identifier_masking.md`, [[project_pdpl_number_masking]].
- Sourcing labour judgments (35 in corpus).
- Classifying the 2,281 untyped ZATCA rows by tax type.
- Backfilling `referenced_regulations` for ديوان المظالم from `cited_laws_text`.
- Circulars and services (100 published each) — untouched.

---

## 5. Success criteria

1. `/judgments/courts/المحكمة-التجارية` renders 9 cards, real `total_pages`, and paginates
   past page 2 **anonymously** (proves court is a section, not a filter).
2. All 12 court routes resolve; every existing `/judgments/{slug}` document URL still resolves.
3. `/judgments` page 1 unchanged for an anonymous reader.
4. Published counts: judgments ~10,000, regulations 1,188. No hub page renders fewer than 9
   cards except the last page of a section (proves §1.2 — this is what breaks first).
5. `/library` sector grid reports **published** counts, not corpus counts (proves §1.3 —
   this is what breaks second, and silently).
   ⚠ Do **not** check this by comparing the RPC's `judgments` column to the published
   judgment total. `cases.legal_domains` is populated per source feed, so the ~2,224 rows
   published from ديوان / زكاة / تأمين belong to no sector and are correctly absent from
   every sector row: at ~10,000 published the RPC reports ~7,776. Check it against the
   published **وزارة العدل** count instead, or against the regulations column.
6. All 135 entities have ≥1 published regulation.
7. `build_seo_slugs.py --unpublish --ids-file` reverses a batch on staging.
8. `tsc`, lint, `next build`, and the backend suite clean.
