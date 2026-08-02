# المكتبة القانونية — unified hub + sector wing

**Status:** PLANNED 2026-08-01 — nothing built.
**Goal:** Turn `/library` from a `ComingSoonHub` placeholder into the real unified hub — the
public mirror of «مكتبتي» — and open a second axis into the corpus: **sector** («القطاع»), the
one label that means the same thing in all four wings.

Companion plans: `seo_public_library.md` (this is its never-built "Topics" phase, relocated from
`/topics/{slug}` to `/library/{sector}`) · `access_tiers_gating.md` PART 5B (مكتبتي — the design
donor) · `cloudflare_navigation_hardening.md` §2.1–§2.3 (the cap policy this changes).

---

## 1. Locked decisions (reflect session 2026-08-01)

| # | Decision | Value |
|---|---|---|
| D1 | `/library` visibility | **Public + indexed**, same as every other nav hub. Drops today's `robots: noindex`. |
| D2 | Design | **Full layout parity with `/library/mine`** — same tab chips + counts, same 3×3 card grid, same pagination. |
| D3 | Tabs | The four مكتبتي defaults only: **الأنظمة** (absorbs nested مواد) · **الأحكام** · **الخدمات** · **التعاميم**. النماذج + الحاسبات stay parked (they have no sector data and their wings aren't launched). |
| D4 | Sector slug script | **Latin.** Rule: *Latin for structural path segments, Arabic for document identifiers.* A sector is structural — `/library/labor-employment/regulations` keeps the one Arabic segment from sitting between two Latin ones. |
| D5 | Slug style | **English translation, not transliteration.** `commercial-transactions`, never `almuamalat-altijariya` — a transliteration is unreadable to both audiences. |
| D6 | Display name | Always Arabic, from `topics.name_ar`. H1 / `<title>` / chips / nav are unaffected by D4 — that's where the Arabic SEO weight actually lives. |
| D7 | Route shape | `/library/{sector}/{type}/page/{n}` — real paginated paths, **not** client-side tabs. Tab content behind JS is indexed unreliably. |
| D8 | Cap policy | A sector is a **SECTION, not a filter**. Real counts, same depth caps as every other hub. See §5. |
| D9 | Thin pages | Sector×type combinations under **3 items** get `noindex, follow`; the tab isn't rendered when empty. |
| D10 | Dark judgments | **No «غير مصنّف» bucket.** The 9,860 sector-less judgments stay reachable only via `/judgments`. Backfill deferred (§10). |
| D11 | Sector pills | The dead `<span>` pills on `RegulationCard` / `ComplianceCard` **become links** to their sector page. |
| D12 | Nav slot | `/library` gets its dropdown hub row back in `lib/nav/site-nav.ts` (reverses the 2026-07-23 removal). |
| D13 | «حفظ» on public cards | **No.** Saving spends an unlock (`resolve_access`); it stays on doc pages + مكتبتي. |
| D14 | Sort control | **DEFERRED — user owns the design.** v1 uses each wing's existing default ordering. Data constraints recorded in §9 so the later pass doesn't re-derive them. |
| D15 | `topic_map` | **NOT materialized.** See trap T1. `topics` is a 38-row slug↔name lookup and nothing more. |

---

## 2. Verified data (live Supabase, 2026-08-01)

Sector coverage — three wings are complete, one has a real hole:

| corpus | column | rows | with sector |
|---|---|---|---|
| `regulations_v2` (VIEW) | `sectors[]` | 3,373 | **3,373 — 100%** |
| `services` | `sectors[]` | 4,717 | **4,717 — 100%** |
| `circulars` | `sectors[]` | 1,843 | **1,843 — 100%** |
| `cases` | `legal_domains[]` | 30,531 | **20,671 — 67.7%** (9,860 dark → D10) |

Vocabulary: the canonical 38 entries in `agents/deep_search_v4/shared/sector_vocab/unified.py:42`
(`VALID_SECTORS`). **Import it, never retype it** — the same rule `public_library.py:563` sets for
every other closed vocabulary. All 38 appear in regs + services; `circulars` lack السياحة والترفيه;
`cases` lack البحث والابتكار and الشؤون الخارجية.

GIN indexes exist on `cases.legal_domains`, `circulars.sectors`, `services.sectors`.
**`regulations_v2` has none** — it's a VIEW over the pipeline-owned `regulation_v2` schema. At
3,373 rows a seq scan is negligible; do not add an index to the view (it would fail), and do not
touch the pipeline schema.

**No sector is thin** — the smallest, حقوق الإنسان, still has 68 items (~8 pages). Every one of
the 38 is a legitimate indexable page.

Of the 152 sector×type combinations: **3 are empty** (تعاميم/tourism-entertainment,
أحكام/research-innovation, أحكام/foreign-affairs) and **7 more hold 1–2 items** (تعاميم for
intellectual-property, sports, water-environment, culture-media; أحكام for human-rights,
social-development, governance). D9 covers these → ~142 solid indexable pages.

---

## 3. The 38 sectors

Ordered by corpus volume — **this is also the browse-grid order** (§7.2). Alphabetical would bury
المعاملات التجارية (20k items) under الأمن الغذائي.

| # | القطاع | slug | أنظمة | خدمات | تعاميم | أحكام | total |
|---|---|---|---|---|---|---|---|
| 1 | المعاملات التجارية | `commercial-transactions` | 693 | 448 | 162 | 18,879 | 20,182 |
| 2 | حوكمة الشركات والاستثمار | `corporate-governance-investment` | 369 | 145 | 136 | 3,922 | 4,572 |
| 3 | القضاء والمحاكم | `judiciary-courts` | 252 | 250 | 328 | 2,941 | 3,771 |
| 4 | المالية والضرائب | `finance-tax` | 505 | 387 | 827 | 554 | 2,273 |
| 5 | العقار | `real-estate` | 154 | 363 | 137 | 1,187 | 1,841 |
| 6 | الإسكان | `housing` | 120 | 53 | 20 | 1,632 | 1,825 |
| 7 | النقل | `transport` | 390 | 535 | 40 | 586 | 1,551 |
| 8 | المهن المرخصة | `licensed-professions` | 212 | 431 | 275 | 319 | 1,237 |
| 9 | العمل والتوظيف | `labor-employment` | 402 | 341 | 91 | 250 | 1,084 |
| 10 | الصحة | `health` | 526 | 166 | 207 | 173 | 1,072 |
| 11 | تقنية المعلومات والأمن السيبراني | `it-cybersecurity` | 357 | 249 | 191 | 131 | 928 |
| 12 | البلديات والتخطيط العمراني | `municipalities-urban-planning` | 353 | 516 | 6 | 12 | 887 |
| 13 | المواصفات والمقاييس | `standards-metrology` ⚠ | 695 | 30 | 80 | 13 | 818 |
| 14 | الأمن الغذائي | `food-security` | 406 | 92 | 210 | 45 | 753 |
| 15 | التعليم | `education` | 240 | 492 | 4 | 17 | 753 |
| 16 | الزراعة | `agriculture` | 219 | 440 | 13 | 63 | 735 |
| 17 | المياه والبيئة | `water-environment` | 312 | 271 | 2 | 85 | 670 |
| 18 | التأمين | `insurance` | 123 | 181 | 290 | 28 | 622 |
| 19 | الصناعة والتعدين | `industry-mining` | 237 | 202 | 20 | 148 | 607 |
| 20 | الحوكمة | `governance` ⚠ | 383 | 181 | 27 | 2 | 593 |
| 21 | الجمارك والتجارة الدولية | `customs-international-trade` | 164 | 203 | 57 | 47 | 471 |
| 22 | الجنايات والجرائم | `criminal-offenses` ⚠ | 198 | 30 | 116 | 117 | 461 |
| 23 | الملكية الفكرية | `intellectual-property` | 67 | 14 | 1 | 373 | 455 |
| 24 | السياحة والترفيه | `tourism-entertainment` | 157 | 220 | 0 | 67 | 444 |
| 25 | الثقافة والإعلام | `culture-media` | 143 | 172 | 2 | 39 | 356 |
| 26 | التنمية الاجتماعية | `social-development` | 181 | 153 | 15 | 2 | 351 |
| 27 | التعاملات والأحوال المدنية | `civil-transactions-status` ⚠ | 23 | 169 | 66 | 67 | 325 |
| 28 | الطاقة | `energy` | 140 | 113 | 3 | 58 | 314 |
| 29 | الأمن والدفاع | `security-defense` | 239 | 26 | 37 | 11 | 313 |
| 30 | الاتصالات والفضاء | `telecom-space` | 91 | 119 | 10 | 84 | 304 |
| 31 | الرقابة | `oversight` ⚠ | 156 | 49 | 26 | 4 | 235 |
| 32 | الحج والعمرة | `hajj-umrah` | 41 | 121 | 8 | 28 | 198 |
| 33 | البحث والابتكار | `research-innovation` | 116 | 77 | 3 | 0 | 196 |
| 34 | المنظمات غير الربحية | `nonprofits` | 92 | 36 | 18 | 6 | 152 |
| 35 | الشؤون الإسلامية والأوقاف | `islamic-affairs-endowments` | 50 | 45 | 33 | 9 | 137 |
| 36 | الشؤون الخارجية | `foreign-affairs` | 77 | 10 | 16 | 0 | 103 |
| 37 | الرياضة | `sports` | 41 | 18 | 1 | 24 | 84 |
| 38 | حقوق الإنسان | `human-rights` | 47 | 16 | 4 | 1 | 68 |

**⚠ Five awaiting user sign-off** — the Arabic doesn't translate cleanly. These do NOT block the
build: seed with the value above, and a slug change is a one-row `topics` update plus a 301 while
the corpus is young.

- **#27 `civil-transactions-status`** — clumsy. Holds نظام المعاملات المدنية *and* نظام الأحوال الشخصية. Alt: `civil-affairs`.
- **#22 `criminal-offenses`** — literal ("felonies and crimes"). Alt: `criminal-law` (reads better, overstates scope).
- **#20 `governance` vs #2 `corporate-governance-investment`** — distinct but confusable; #2 is long. Alt for #2: `corporate-investment`.
- **#31 `oversight`** — alts: `regulatory-oversight`, `audit`.
- **#13 `standards-metrology`** — `metrology` is obscure. Alt: `standards-measurement`.

---

## 4. Route map

```
/library                                  unified hub — 4 tabs + sector browse grid
/library/page/{n}                         deep pages of the "all sectors" view
/library/mine                             UNCHANGED (authed shelf — see trap T2)
/library/{sector}                          sector overview — a slice of each of the 4 types
/library/{sector}/{type}                   paginated, type ∈ regulations|judgments|compliance|circulars
/library/{sector}/{type}/page/{n}          deep pages
```

`{type}` uses the **existing public wing names** (`regulations` / `judgments` / `compliance` /
`circulars`), not the مكتبتي content-type tokens (`regulation` / `judgment` / `service` /
`circular`). The URL should match the wing a reader already knows. One mapping table, one place.

**Canonicals.** `/library/{sector}/{type}` is the canonical for a sector-filtered view.
`/regulations?sector=X` stays a working API-level filter but nothing links it and it carries a
canonical pointing at the path form — no duplicate content.

---

## 5. The cap-policy change (§2.1 amendment) — read before touching the backend

`public_library.py:387-482` draws its line at **FILTERED vs UNFILTERED**: any filtered anon
request gets `_ANON_WALL_TOTAL_PAGES` (= `ANON_HUB_MAX_PAGE + 1` = 2) and the count query is never
issued. A sector page is filtered under that rule, so shipping it as-is gives an anon reader a
paginator reading «1 2» over 20,182 items — exactly the failure the 2026-07-30 revision was
written to fix.

**Amendment: a sector is a section.** The oracle §2.1 closes is *free-text `q`*, where the answer
moves with attacker-chosen input, one probe per slice. A **closed 38-value vocabulary** validated
server-side yields 152 fixed numbers that move only when the corpus does — the same argument that
already lets anon see real section totals (`public_library.py:420-424`).

So:

- **Counts: REAL.** Memoised exactly like `_unfiltered_total_pages` (5-min TTL, keyed by
  `{section}:{sector}`). Compute all 152 in **one** grouped query per refresh, not 152 queries.
- **Depth: UNCHANGED.** `ANON_HUB_MAX_PAGE = 1`, `FREE_HUB_MAX_PAGE = 3`, paid unbounded —
  identical to every other hub. Real numbers, same walls.
- **Validation: MANDATORY.** The sector slug is checked against the 38-entry vocabulary before any
  DB work, alongside `_COURT_LEVEL_VOCAB` / doc-type / category. This is what makes the count
  memoisable and closes the "every filter value is a fresh page 1" hole for this axis. Note that
  `public_library.py:550` currently *exempts* sector/domain from validation on the grounds that
  nothing links them — **that comment must be updated**, because this plan links all 38.
- `library_budget` item metering (§2.2) applies unchanged to authed callers.

Reach math for the record: 152 sector×type page-1s × 9 items = ~1,368 anon-reachable items, up
from 45. Bounded, closed-vocabulary, and far below the ~50k URLs `/sitemaps/articles` already
ships (see `project_scraping_assessment.md`).

---

## 6. Phase 0 — taxonomy seed

`topics` + `topic_map` already exist (migration `096_topics_taxonomy`, applied to prod) and are
**both empty (0 rows)**. Schema: `topics(id, slug, name_ar, parent_id, description)`.

- **`scripts/seed_topics.py`** — idempotent upsert of the 38 rows from `VALID_SECTORS` × the §3
  slug map. `parent_id` stays NULL (flat taxonomy for now; the column is there for a later
  grouping pass — §10).
- The slug↔`name_ar` map lives in **one** module — `shared/library/sectors.py` — importing
  `VALID_SECTORS` and pairing it with the Latin slugs. Backend reads it; the seed script reads it;
  the frontend gets it from the API, never a second hardcoded copy.
- **`topic_map` is left empty on purpose** (D15 / trap T1).

**Agent:** @sql-migration (seed script only — no DDL needed) → @shared-foundation for
`shared/library/sectors.py`.

---

## 7. Phase 1 — backend

### 7.1 Close the circulars gap
`CircularsFilters` is `entity` + `q` only (`public_library.py:1580`, `lib/library/api.ts:418`)
despite 100% sector coverage. Add `sector` to the endpoint, to `_apply_circular_filters`, and to
the wire type. Without it the التعاميم tab on a sector page cannot filter.

### 7.2 New endpoints
```
GET /api/v1/public/library/sectors
    → the 38 rows: {slug, name_ar, counts:{regulations,judgments,compliance,circulars,total}}
      Memoised (§5). Powers the browse grid + the switcher.

GET /api/v1/public/library/sectors/{slug}
    → one sector: name_ar + per-type counts + a first slice of each of the 4 types
      (for the /library/{sector} overview page).
```
Per-type paginated lists **reuse the existing wing endpoints** with the sector filter applied —
no new list code, no second gating path. `resolve_gate` / `truncate_for_gate` / `library_budget`
behaviour is inherited unchanged.

### 7.3 Unified hub counts
`GET /api/v1/public/library` → the four tab counts for the unfiltered hub (3,373 / 20,671 /
4,717 / 1,843). Unfiltered totals, already public in the nav copy — memoise per §5.

**Agent:** @fastapi-backend → @security-reviewer (cap-policy amendment is a security-relevant
change; §5 must be reviewed, not assumed).

---

## 8. Phase 2 — frontend

### 8.1 `/library` — the unified hub
Replace `ComingSoonHub`. Mirrors `MyLibraryPage.tsx` structure: header → tab chips with counts
(`MyLibraryTabs` is the reference; the public version is a server component with `<Link>` tabs,
not `useState`) → 3×3 card grid reusing each wing's existing card → `HubPagination`.

Cards are the **existing** `RegulationCard` / `JudgmentCard` / `ComplianceCard` / `CircularCard`.
§5B.1's rule holds in both directions: a filtered hub is not a new design system.

### 8.2 The sector browse grid — «تصفّح حسب القطاع»
All 38 as a responsive grid of plain `<Link>`s + counts, volume-ordered, below the tabs.

**Server-rendered, no client state.** 38 links in the SSR HTML *is* the crawl skeleton — a
`<select>` or JS popover hides it from crawlers, which defeats the point. Same reasoning
`JudgmentsFilterBar.tsx:16-27` already states for the judgments filter row, and the same trap
`global_header.md` hit with Radix portals.

### 8.3 `/library/{sector}` and `/library/{sector}/{type}`
Sector page = the same four tabs, scoped. Sector context renders as a header line plus a
«تغيير القطاع ▾» disclosure — build it as native `<details>`/`<summary>` wrapping the same 38
`<Link>`s: crawlable, keyboard-accessible, works with JS off, zero client state.

Deep pages follow the `/regulations/page/[n]` template exactly, **including** the §3.7
`readVerifiedBotSignal()` scoping — see `app/regulations/page/[n]/page.tsx:11-35`. Page 1 must
stay statically prerendered; only deep segments may read headers.

### 8.4 Pills become links (D11)
`RegulationCard.tsx:47` and `ComplianceCard.tsx:42` — pills become `<Link href="/library/{slug}">`.
Add sector pills to `CircularCard` and `JudgmentCard` for symmetry (both corpora carry the data).

### 8.5 Nav + guard
- `lib/nav/site-nav.ts` — restore the `href: "/library"` hub row on the المكتبة القانونية slot
  (D12), with `hubLabel` / `hubDescription`.
- `AuthGuard.PUBLIC_PREFIXES` already contains `/library`, and `PRIVATE_EXCEPTIONS` already
  contains `/library/mine` — **no change needed**, but verify after adding the dynamic segment
  (trap T2).

**Agent:** @nextjs-frontend → @frontend-dev-loop.

---

## 9. Sort — deferred (D14), constraints recorded

The four corpora do **not** share a sortable column. Findings, so the later design pass starts
from facts:

| wing | sortable without user data | current default (already built) |
|---|---|---|
| الأنظمة | `clean_title`; `start_date` **only 976/3,373 = 29%** | سارية first, then alpha — `library_service.py:1503` |
| الأحكام | `date_gregorian` 19,112/30,531 = 63%; titles are **derived** so alpha is meaningless | newest first, nulls last — `:3623` |
| الخدمات | `service_name_ar`; `is_most_used` (185 rows — a **corpus flag**, not usage) | `is_most_used` desc, then alpha — `:2075` |
| التعاميم | `title` only — **no date column exists at all**, just `built_at`/`ingested_at` pipeline stamps | alpha — `:2734` |

Consequence: a uniform «الأحدث» across all four tabs is impossible. v1 ships each wing's existing
default ordering with no sort control.

---

## 10. Deferred

- **Sector backfill for the 9,860 dark judgments.** Input signal is good — every one of them has
  `case_topics` rows (`fact` / `basis` / `principle`) with embeddings — but `case_topics.attrs` is
  keyed on الطرف / موقف المحكمة / النوع and carries no sector, so this needs its own classifier
  pass. Own plan. See `project_case_topics_loop`.
- **Sector grouping into ~6 families** (`topics.parent_id` is already there for it). Editorial —
  needs user input. Ship flat-38-by-volume first.
- **Sort vocabulary** (§9).
- **النماذج + الحاسبات tabs** — `forms` has no sector column; calculators have no table.
- **«الأكثر اطلاعاً»** from aggregate `library_items.use_count` — user owns this design.

---

## 11. Traps

**T1 — `topic_map` must stay empty.** Materializing 30k+ join rows looks tidy and is a trap:
`regulations_v2` is a VIEW over the pipeline-owned `regulation_v2` schema, so a re-ingest silently
desynchronizes the join table with no error. Query `sectors[]` / `legal_domains[]` directly through
the existing `.contains()` filters — already built, GIN-indexed on 3 of 4 corpora, and always
current. `topics` is a 38-row lookup, nothing more. (Same reasoning that pushed migrations 095/096
onto a sidecar instead of per-table columns.)

**T2 — `/library/mine` must not be swallowed by `[sector]`.** Next resolves static segments before
dynamic ones so `app/library/mine/page.tsx` wins, and `AuthGuard.PRIVATE_EXCEPTIONS` is checked
before the prefix list (`AuthGuard.tsx:46,73`) — but **verify both** after the dynamic segment
lands. A regression here renders a per-user shelf for anonymous visitors. Also: reject `mine` as a
sector slug server-side so the two namespaces can never collide.

**T3 — ISR bake ordering.** Deploy **backend before frontend**. A frontend prerender against a
backend that doesn't yet serve `/public/library/sectors` bakes empty hubs, and a same-commit
rebuild is a Docker-layer-cache no-op. Purge via `/api/revalidate`. See
`project_isr_bake_docker_cache_trap`.

**T4 — the §2.1 comment block is documentation with teeth.** `public_library.py:550` states sector
filters are deliberately unvalidated *because nothing links them*. This plan links all 38. Update
the comment in the same commit that adds validation, or the next reader inherits a false premise.

**T5 — `revalidate` + `noindex` interaction on thin pages (D9).** The thin-page decision depends on
a count, so `generateMetadata` needs that count. Follow the `/regulations/page/[n]` split exactly
(`page/[n]/page.tsx:62-71`): metadata fetch stays **unexempted** from the crawler signal, body
fetch is exempted. Getting this backwards hands crawlers indexable thin pages.

**T6 — do not add a GIN index to `regulations_v2`.** It's a VIEW; the DDL fails. The underlying
pipeline schema is not ours to alter. 3,373 rows seq-scan fine.

---

## 12. Success criteria

1. `/library` returns 200, is indexable, renders four tab chips with real counts, and the sector
   grid's 38 links are present in `view-source` HTML (crawl-safe — the `global_header.md` trap #6
   check).
2. `/library/labor-employment/regulations` returns 402 أنظمة across real pagination for an authed
   paid caller; an anon caller sees 9 items, a CTA wall, and **the true total page count**.
3. `/library/mine` still resolves to the authed shelf and still 401s cleanly for anon.
4. Sector pills on `/regulations` and `/compliance` navigate to the matching sector page.
5. A sector×type page with <3 items carries `noindex, follow`; an empty one renders no tab.
6. `/sitemaps/sectors` lists ~142 URLs; the sitemap index references it.
7. An invalid sector slug (`/library/zzz`) 404s without a DB round-trip.
8. `tsc --noEmit` clean · `next lint` clean · `next build` prerenders `/library` and
   `/library/{sector}` page 1s as static.
9. @security-reviewer signs off on the §5 cap amendment specifically.

---

## 13. Agent sequence

```
@shared-foundation   shared/library/sectors.py (slug map)
@sql-migration       scripts/seed_topics.py  → 38 rows
@fastapi-backend     circulars sector filter · /sectors endpoints · §5 cap amendment
@security-reviewer   §5 review — BLOCKING
@nextjs-frontend     /library hub · sector grid · sector routes · pills · nav slot
@frontend-dev-loop   evaluate + iterate
@validate            success criteria §12
@deploy-checker      backend FIRST, then frontend (T3)
```
