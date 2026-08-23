# /compliance — entity sections + the guides search corpus

Status: **MIGRATION 144 APPLIED to prod 2026-08-23** — `refresh_search_index
('compliance')` returned **337**, every image-hole token line stripped (0 rows
match `^\s*\d+_\d+\s*$` in `lead`), 7,202 BM25 terms, avg `doc_len` 710, and
`bm25_refresh_nightly` now carries the compliance line. The live function was
verified branch-by-branch against this file BEFORE and AFTER (each of the six
pre-existing branches hashes identically; only the compliance branch is new —
it was spliced into the live definition rather than re-transcribed, so a
dropped branch was impossible by construction).
§11 is the build log: what the plan got wrong, and what only appeared once it
met the compiler.
Reads before this one: `.claude/plans/compliance_service_guides.md` (the wing
itself), `.claude/plans/library_court_sections_publish_ramp.md` §2 (the section
pattern this copies), `.claude/plans/bm25_navigation_search.md` §4.5 (the corpus
registry this joins), `.claude/plans/navigation_enumeration_defence.md` (the rule
§2 below deliberately exempts one wing from).

---

## 0. What this is, and the two exclusions it reverses

Two browse axes for `/compliance`, both of which the wing's own plan listed under
**§9 "Explicitly OUT of scope (v1)"**:

1. **Entity sections** — `/compliance/{entity}` and `/compliance/{entity}/page/{n}`,
   the same affordance `/judgments/courts/{court}` gives the judgments wing:
   «تصفّح حسب الجهة» as a server-rendered grid of tiles with counts.
2. **The search index** — the 337 guides join `search_index` as a new
   `compliance` corpus, which turns the cross-wing SearchBar on for them and
   gives the hub back the `HubSearchPanel` its own comment says to "add back with
   the corpus".

The judgments wing is the model for the SHAPE. It is not the model for the
ACCESS RULE, and §2 is where the two part company — read it before writing code,
because it is the one decision here that a reviewer will challenge.

---

## 1. Verified live state (queried 2026-08-22 — do not re-derive)

| Fact | Value |
|---|---|
| Guides in `library_compliance_v` | **337**, every one published (`seo_item_meta.content_type='compliance'`, slug non-null) |
| Distinct `services.provider_name` | **28**, zero NULL, zero near-duplicate spellings |
| Guide slug ∩ proposed entity slugs ∩ reserved words | **∅** — verified against all 337 live slugs |
| `guide_md` | avg 5,459 chars · max 35,677 · 1,839,756 total |
| Guides carrying image-hole tokens | **324 of 337** (46,525 chars stripped by the §6.2 regex) |
| `summary` | present on 337/337, avg 274 chars |
| `search_index` corpora | judgment 10,000 · circular 1,843 · regulation 1,686 · blog 103 · **service 100** · template 6 |
| `search_index` corpus CHECK | `regulation, judgment, circular, service, blog, template` |
| Index write path | `refresh_search_index(p_corpus)` (SECURITY DEFINER, delete+insert per corpus) → `search_index_fill_trg` BEFORE trigger builds `search_doc`/`doc_len` → `refresh_bm25_stats(p_corpus)` |
| Nightly | `cron.job` `bm25_refresh_nightly`, `20 2 * * *` |

### 1.1 The entity distribution — the fact that shapes every other decision

| # | `provider_name` | Guides | Pages @9 |
|---|---|---|---|
| 1 | وزارة العدل | 115 | 13 |
| 2 | وزارة الموارد البشرية والتنمية الاجتماعية | 53 | 6 |
| 3 | وزارة التجارة | 43 | 5 |
| 4 | المؤسسة العامة للتأمينات الاجتماعية | 19 | 3 |
| 5 | وزارة الصحة | 17 | 2 |
| 6 | هيئة الزكاة والضريبة والجمارك | 14 | 2 |
| 7 | وزارة التعليم | 10 | 2 |
| 8 | صندوق تنمية الموارد البشرية | 8 | 1 |
| 9 | وزارة البيئة والمياه والزراعة | 8 | 1 |
| 10 | الهيئة العامة لتنظيم الإعلام | 7 | 1 |
| 11 | وزارة الخارجية | 7 | 1 |
| 12 | المركز الوطني لنظم الموارد الحكومية | 6 | 1 |
| 13 | بنك التنمية الاجتماعية | 5 | 1 |
| 14 | هيئة حقوق الإنسان | 4 | 1 |
| 15 | وزارة البلديات والإسكان | 4 | 1 |
| 16–19 | وزارة الصناعة والثروة المعدنية · وزارة السياحة · المركز الوطني للرقابة على الإلتزام البيئي · الهيئة العامة للنقل | 2 each | 1 |
| 20–28 | الهيئة العامة للعقار · الهيئة العامة للأوقاف · الهيئة السعودية للمقَيّمين المعتمدين (تقييم) · المركز السعودي للأعمال الاقتصادية · وزارة المالية · الهيئة الملكية لمحافظة العلا · الهيئة العامة للمنشآت الصغيرة والمتوسطة · المؤسسة العامة للتدريب التقني والمهني · الهيئة العامة للغذاء والدواء | 1 each | 1 |

**Only 7 of 28 entities have a page 2 at all.** The `/page/{n}` half of the URL
shape is built once and exercised by a quarter of the vocabulary — build it
anyway (one route, and وزارة العدل alone needs 13 pages), but do not let a smoke
test that only opens page 1 of a one-guide entity count as coverage.

### 1.2 Why this vocabulary is easier than `courts.py`

`cases.court` is free text: 30 raw strings collapsing to 12 buckets, where the
same body appears under several spellings differing only by city. `courts.py`
had to BE the normalizer, and its query predicate is `in.(variants)`.

`services.provider_name` is already canonical: 28 strings, one per body, no city
suffixes, no numbered دوائر, no residual bucket. So `entities.py` is a
slug ⇄ single-name map and the predicate is `eq`, not `in`. There is **no
variant list**, and adding one later would be a corpus problem, not a code one.

---

## 2. The three decisions (taken 2026-08-22)

### D1 — the entity axis is ANON-VISIBLE and INDEXABLE. It is not a paid SECTION.

`public_library.py:453 section_scope_allowed()` refuses any section-scoped hub
slice below `paid`, at page 1, with no crawler exemption; every sector and court
page is `noindex, follow` and off the sitemap as the matching half of that
decision (`app/judgments/courts/[court]/page.tsx` header · `lib/seo/sitemap.ts`).

**That rule does not apply here, and the reason is a property of this wing, not a
change of mind about the rule.** The gate exists because a section axis
multiplies the depth cap over a corpus that is otherwise metered: 27 free items
per slice × 152 slices ≈ 4,100 items reachable by walking links. Every term of
that argument is absent on `/compliance`:

- The wing is **100% published** — 337 of 337 — and **ungated end to end**.
  `library_service.py:839` records that `'compliance'` is deliberately absent
  from the gate map and `get_compliance_guide` resolves no gate and charges
  nothing. There is no partial content for a section slice to accumulate.
- **All 337 guide URLs are already in the sitemap** and have been since
  2026-08-19. A crawler does not need the hub, at any depth, to reach every
  guide. An anon reader who walks 28 entity page-1s sees 252 CARDS whose
  bodies were already fully public.
- The guides are **ours** — our own authored rewrite of each entity's PDF. The
  PDPL argument that closed the courts axis has no counterpart.

So: **`entity_slug` is NOT passed into `_hub_page_visible(section_scoped=…)`.**
That single omission IS the exemption — `section_scope_allowed()` is not edited,
`_SECTION_SCOPE_TIERS` is not edited, and no other wing changes behaviour.

⚠ **What D1 does NOT change**, and a reviewer must be able to confirm each in one
grep:

- The **anon depth cap still applies per URL**: anon gets page 1 of an entity and
  the CTA wall at page 2, exactly as on `/compliance/page/2` today. `hub_page_allowed`
  is untouched. وزارة العدل's 13 pages are not an anon-readable 115-card list.
- The verified-crawler depth waiver (§3.7) still applies, so Googlebot reaches
  the deep entity pages — which is what makes indexing page 1 honest rather than
  cloaked. This is precisely the test the courts axis FAILED and this one passes.
- The **sector axis on `/compliance` stays paid-only.** `sector_slug` keeps
  feeding `section_scoped`. Sector is the cross-wing axis governed by the shared
  rule; entity is one wing's own. Do not "harmonise" them without redoing §2.
- `entity_slug` stays **out of the `filtered` flag** — same as `sector_key` and
  `court`. A closed, server-owned 28-value vocabulary yields 28 fixed numbers
  that move only when the corpus does; it is a section, not an oracle. Putting it
  in `filtered` would pin anon `total_pages` to 2 and print «1 2» over 115 guides.
- `_charge_hub_yield` still meters a signed-in caller's yielded items, keyed
  `section="compliance"` — one budget for browse and search alike.

### D2 — the URL is flat: `/compliance/{entity}` · `/compliance/{entity}/page/{n}`

The requested shape, not the `/judgments/courts/{court}` shape. It is legal in
Next because it needs ONE dynamic segment serving two kinds of thing, not two
dynamic names at one level (which is the build error that forced `courts` into
the judgments URL). `app/compliance/page/[n]` keeps working because a static
segment resolves ahead of `[slug]`.

The cost is a **shared slug namespace**, and it is paid three times:

1. `app/compliance/[slug]/page.tsx` dispatches **entity-vocabulary FIRST** (an
   in-process dict lookup, no fetch), then falls through to the guide fetch.
2. Because entity-first means an entity slug would SHADOW a same-named guide —
   404ing a URL that is in the sitemap — the 28 slugs plus `page`, `entities`,
   `mine` are **reserved** in `scripts/build_compliance_slugs.py` and asserted by
   a test against live `seo_item_meta`. Verified clean today: ∅ collisions.
3. `get_compliance_guide` refuses the reserved set server-side too, so the two
   layers cannot drift into disagreeing about what a slug means.

### D3 — all 28 entities ship, all indexable

Including the nine one-guide entities. The judgments precedent for the 35-row
المحكمة العمالية applies: the route is honest, costs nothing, and a browse grid
that silently omits an entity whose name is printed on the cards is worse than a
thin page. Consequence accepted: 13 entity pages carry 1–2 cards and are
indexable.

---

## 3. `shared/library/entities.py` (new) — the closed vocabulary

Mirrors `shared/library/sectors.py` / `courts.py`: ordered by corpus volume
(that order IS the browse order), log-and-omit on drift, **never raise at import**
(a pipeline re-ingest of `services` must not be able to crash backend boot). A
`provider_name` no bucket claims is logged; its guides stay reachable via the
hub, the sitemap and search.

**SLUGS ARE LATIN kebab-case**, unlike `courts.py` and per `library_sectors.md`
D4. The justification that let courts go Arabic — «the wing is noindex, there is
no SEO to be neutral about» — is exactly inverted here: this is the indexed wing.
Latin also removes the percent-encoding trap that runs through every entry point
in `lib/library/courts.ts` and through ISR revalidation
(memory `isr-revalidate-encoding`).

⚠ **REVIEW THIS TABLE BEFORE APPLYING.** These slugs become permanent indexable
URLs the moment the sitemap ships them; the same "never rewrite an existing slug"
rule that governs `build_seo_slugs.py` governs these.

| # | Slug | `provider_name` | Guides |
|---|---|---|---|
| 1 | `ministry-of-justice` | وزارة العدل | 115 |
| 2 | `ministry-of-human-resources` | وزارة الموارد البشرية والتنمية الاجتماعية | 53 |
| 3 | `ministry-of-commerce` | وزارة التجارة | 43 |
| 4 | `gosi` | المؤسسة العامة للتأمينات الاجتماعية | 19 |
| 5 | `ministry-of-health` | وزارة الصحة | 17 |
| 6 | `zatca` | هيئة الزكاة والضريبة والجمارك | 14 |
| 7 | `ministry-of-education` | وزارة التعليم | 10 |
| 8 | `hrdf` | صندوق تنمية الموارد البشرية | 8 |
| 9 | `ministry-of-environment` | وزارة البيئة والمياه والزراعة | 8 |
| 10 | `media-regulation-authority` | الهيئة العامة لتنظيم الإعلام | 7 |
| 11 | `ministry-of-foreign-affairs` | وزارة الخارجية | 7 |
| 12 | `etimad` | المركز الوطني لنظم الموارد الحكومية | 6 |
| 13 | `social-development-bank` | بنك التنمية الاجتماعية | 5 |
| 14 | `human-rights-commission` | هيئة حقوق الإنسان | 4 |
| 15 | `ministry-of-municipalities-housing` | وزارة البلديات والإسكان | 4 |
| 16 | `ministry-of-industry` | وزارة الصناعة والثروة المعدنية | 2 |
| 17 | `ministry-of-tourism` | وزارة السياحة | 2 |
| 18 | `environmental-compliance-center` | المركز الوطني للرقابة على الإلتزام البيئي | 2 |
| 19 | `transport-general-authority` | الهيئة العامة للنقل | 2 |
| 20 | `real-estate-general-authority` | الهيئة العامة للعقار | 1 |
| 21 | `awqaf-general-authority` | الهيئة العامة للأوقاف | 1 |
| 22 | `taqeem` | الهيئة السعودية للمقَيّمين المعتمدين (تقييم) | 1 |
| 23 | `saudi-business-center` | المركز السعودي للأعمال الاقتصادية | 1 |
| 24 | `ministry-of-finance` | وزارة المالية | 1 |
| 25 | `royal-commission-alula` | الهيئة الملكية لمحافظة العلا | 1 |
| 26 | `monshaat` | الهيئة العامة للمنشآت الصغيرة والمتوسطة | 1 |
| 27 | `tvtc` | المؤسسة العامة للتدريب التقني والمهني | 1 |
| 28 | `sfda` | الهيئة العامة للغذاء والدواء | 1 |

Module surface: `ENTITY_ORDER`, `ENTITY_LABELS`, `name_for_slug(slug)`,
`RESERVED_SLUGS = {"page", "entities", "mine"}`. `name_for_slug` returns `None`
for a reserved segment even if a future edit adds it to the map — so
`/compliance/page/2` can never resolve as an entity in either namespace
(the `courts.py:_court_section` rule, copied).

**No DB migration for the entity axis.** The vocabulary is code; the predicate is
`eq` on a column that already exists in `library_compliance_v`. The only
migration in this plan is §6's, for the search corpus.

---

## 4. Backend

### 4.1 `backend/app/api/public_library.py`

1. `_ENTITY_VOCAB` + **`_entity_section(entity_slug) -> Optional[str]`** beside
   `_sector_section` (`:1095`) and `_court_section` (`:1155`). In-memory, called
   BEFORE tier resolution and before any query, so probing the namespace costs no
   round-trip. 400 «الجهة غير معروفة» for unknown or reserved. ONE spelling only —
   there is no raw `?provider_name=`, and the existing free-text `provider` facet
   is a different axis that stays exactly as it is.
2. `list_compliance` (`:2468`) gains `entity_slug: Optional[str]`.
   - `entity = _entity_section(entity_slug)` — resolves to the raw
     `provider_name`.
   - `filtered = bool(provider or q) or _sector_is_unslugged(sector, sector_key)`
     — **unchanged**. `entity` is not added.
   - `_hub_page_visible(..., section_scoped=bool(sector))` — **unchanged**.
     `entity` is not added. This is D1, and it is a one-line non-edit; put the
     reasoning in a block comment beside it or the next reader will "fix" it.
   - Thread `entity` into `_wall_total_pages` and `list_compliance_hub`.
3. **`GET /public/library/compliance/entities`** → `EntityListResponse`
   (`slug`, `label`, `count`, `total_pages`), memoised 5 min beside
   `_sector_counts_memo` (`:468`), mirroring `list_judgment_courts` (`:2845`).
   ⚠ **DECLARE IT ABOVE `/public/library/compliance/{slug}` (`:2562`)** — FastAPI
   matches in declaration order, and below it the literal `entities` would be
   swallowed as a guide slug. The same trap the courts route documents at `:2841`.
   Counts come from the corpus, not from §3's table (which is documentation only).
4. `get_compliance_guide` (`:2562`) refuses `RESERVED_SLUGS` ∪ `ENTITY_ORDER`
   with the same 404 it already gives an unknown slug (D2.3).

### 4.2 `backend/app/services/library_service.py`

- `_compliance_matches` (`:3099`) gains `entity`: **exact** match on
  `provider_name`, not the `ilike` substring `provider` uses. Two params, two
  semantics, and the docstring must say why — a section is exact by construction
  or its counts stop being fixed.
- Thread `entity` through `_compliance_published_rows` (`:3135`),
  `compliance_hub_total_pages` (`:3188`), `list_compliance_hub` (`:3216`).
- Ordering inside a section is unchanged: `_compliance_sort_key` — `most_used_rank`
  ASC, then title, then id.
- `_SECTION_SOURCES['compliance']` (`:2006`) and the sector-count RPC exclusion
  (`:2022`) are untouched — the entity axis does not go through sector counting.

---

## 5. Frontend

### 5.1 `frontend/lib/library/entities.ts` (new)

Mirror of §3: `ENTITY_ORDER`, `ENTITY_LABELS`, `isEntitySlug`, `entityPath`,
`entityHeading`, `ENTITY_FACET_LABEL = "الجهة"`. Same lockstep contract
`lib/library/courts.ts` has with `courts.py` — edited in the SAME commit, and the
Python side additionally owns the query predicate, which has no reason to exist
in the browser. **No `normalizeEntitySlug`**: the slugs are ASCII, so Next hands
them back unencoded and there is nothing to decode. (This is the whole reason D2
chose Latin — do not port the courts decode dance.)

### 5.2 `app/compliance/[slug]/page.tsx` — the dispatcher

```
const { slug } = await params;
if (isEntitySlug(slug)) return <ComplianceHubView page={1} entitySlug={slug} />;
const doc = await getComplianceGuide(slug);
if (!doc) notFound();
… existing guide render …
```

Entity-first, dict lookup, zero fetch (D2.1). `generateMetadata` branches the
same way: entity → H1/title «دليل خدمات {label}», canonical `/compliance/{slug}`,
**no `robots` key** (D1/D3 — indexable, like the guides beside it). Keep the
route WITHOUT `generateStaticParams`, as it is today: on-demand ISR means the 28
entity pages are not baked at build time and cannot bake as 404s if the backend
lags (memory `isr-bake-trap`).

### 5.3 `app/compliance/[slug]/page/[n]/page.tsx` (new)

Deep entity pages. `parsePage` (n ≥ 2, else `notFound`) copied from
`app/compliance/page/[n]`. `readVerifiedBotSignal()` for the §3.7 exemption —
this segment may go dynamic, page 1 must not. `robots: {index:false, follow:true}`
**when `cap_reached`**, exactly the rule `app/compliance/page/[n]` already
applies: don't index the wall, do let Googlebot follow it.

### 5.4 `EntityBrowseGrid.tsx` + `EntitySwitcher.tsx`

Straight copies of `CourtBrowseGrid` / `CourtSwitcher` with the vocabulary
swapped. The properties that matter and must survive the copy:

- A **grid, not a chip row** — 28 entries, several long («وزارة الموارد البشرية
  والتنمية الاجتماعية»).
- **Server-rendered, zero client state**, native `<details>`/`<summary>`. These
  links in the SSR HTML *are* the second browse axis, for readers and crawlers
  alike — verify with `view-source`, never devtools (memory `global-header`).
- A «جميع الجهات» tile back to `/compliance`, the first tile, matching the
  screenshot.
- Order is corpus volume, never re-sorted. `count === null` renders without a
  number rather than asserting «0».
- Rendered on `/compliance`, on `/compliance/page/{n}`, and on every entity page.
  Collapsed by default inside a section.

### 5.5 `ComplianceHubView.tsx`

- New optional `entitySlug`; passes `entity_slug` to `getComplianceHub`, drives
  the H1, the breadcrumbs («الرئيسية › دليل الخدمات › {label}»), `basePath`
  for `HubPagination`/`HubCtaWall` (`/compliance/{slug}`), and `activeSlug` on
  the switcher.
- **Delete the "NO SEARCH PANEL, deliberately" block comment** and wrap the body
  in `<HubSearchPanel section="compliance" sectorSlugs={sectorSlugs}>`, per
  `CircularsHubView:66`. Its own comment says to add it back with the corpus;
  §6 is the corpus.

### 5.6 `ComplianceCard.tsx`

`provider_name` (already printed, `:48`) becomes a `<Link>` to the entity
section — the card is where a reader learns the entity's name, so it is where the
axis should be discoverable without opening the switcher.

### 5.7 Types + client

`ComplianceFilters` in `lib/library/api.ts` gains `entity_slug?`. `buildQuery`
(`:579`) forwards truthy keys verbatim — no change. ⚠ If any hub view threads a
`query` string separately (the `JudgmentsHubView:64` trap), add `entity_slug`
there too; miss it and pagination silently drops the entity.

### 5.8 Sitemap

`lib/seo/sitemap.ts`: `SITEMAP_SECTIONS` += `"compliance-entities"`, plus a local
`getComplianceEntityUrls()` returning the 28 page-1 URLs and a case in
`app/sitemaps/[section]/route.ts` (`:59`) — the `sectors` pattern exactly: local
code registry, no backend feed, cannot 5xx. **Page-1 URLs only.** Deep entity
pages are `noindex` when walled, and a sitemap that lists a URL the page marks
`noindex` is what Search Console reports as "Submitted URL marked noindex".

---

## 6. The search corpus — `compliance` joins `search_index`

### 6.1 Migration `shared/db/migrations/1XX_compliance_search_corpus.sql`

Idempotent, `sql-migration` conventions, applied via MCP **before any deploy**.

1. Drop + re-add `search_index_corpus_check` with `'compliance'` appended,
   preserving the existing six values exactly. Keep text+CHECK; do not convert to
   an enum (the §3.0 rule from the wing's own migration).
2. `create or replace function public.refresh_search_index(p_corpus text)` with a
   new branch. Field assignment follows §4.5's table and the live `service`
   branch it sits next to:

```sql
elsif p_corpus = 'compliance' then
  delete from public.search_index where corpus = 'compliance';
  insert into public.search_index (corpus, content_id, slug, title,
                                   entity_text, facets_text, lead, facets)
  select 'compliance', g.id::text, m.slug,
         coalesce(g.title, ''),
         -- B: the issuing entity, the same slot `service`/`circular` give it.
         coalesce(g.provider_name, ''),
         concat_ws(' ', g.service_ref, array_to_string(g.sectors, ' ')),
         -- D: the guide, WHOLE. It is ours, it is published in full and
         -- ungated, so unlike `circular` there is no free-floor to compute.
         -- The regexp strips the image-hole token lines (§6.2).
         regexp_replace(coalesce(g.guide_md, ''), '^[ \t]*\d+_\d+[ \t]*$', '', 'gn'),
         jsonb_strip_nulls(jsonb_build_object(
           'provider_name', g.provider_name,
           'service_ref', g.service_ref,
           'sectors', to_jsonb(coalesce(g.sectors, array[]::text[]))))
  from public.library_compliance_v g
  join public.seo_item_meta m
    on m.content_type = 'compliance' and m.content_id = g.id::text
   and m.slug is not null;
```

   `search_doc`/`doc_len` need no code — `search_index_fill_trg` fires on INSERT.
3. `cron.job` `bm25_refresh_nightly` gains
   `select public.refresh_search_index('compliance');`.
4. Backfill in the same session: `select public.refresh_search_index('compliance');`
   → expect **337**.

### 6.2 ⚠ The image-hole tokens must be stripped before indexing

324 of the 337 guides carry lines that are only `\d+_\d+` — the screenshot
placeholders the renderer resolves by `image_ref` (REFERENCE.md §3–§4). Verified
live: the regex removes 46,525 chars. Two reasons this is not cosmetic:

- They tokenize as searchable numbers. `12_3` in the index is a term a query can
  hit, on a "document" the reader can never see that string in.
- **`doc_len` counts them.** `search_index_fill_trg` sets `doc_len` to the total
  token count, so 69 unstripped holes make the most heavily-illustrated guide —
  the one with the most screenshots, i.e. the most useful one — look long and
  thin to BM25's length normalization and rank DOWN for it.

⚠ **Corrected 2026-08-23:** an earlier draft of this section justified the strip
by "`lead` is what the snippet is cut from". **It is not.** `SearchHit` carries
no snippet and no `lead` at all — `bm25_navigation_search.md` §5.3 deleted
`ts_headline` and the whole highlight path on purpose, and every card renders its
own excerpt from its own always-free column (`ComplianceHubItem.summary` here).
`lead` is recall weight and nothing else. The strip is still required, for the
two reasons above.

**And that is why `summary` is NOT concatenated into `lead`.** Measured
2026-08-22: `summary` is a VERBATIM substring of `guide_md` on **337 of 337**
guides, so indexing it alongside the body adds no vocabulary and only
double-counts whatever the abstract repeats. If a future ingest ever authors
summaries independently of the body, re-run that check and revisit the line.

The pattern is `'^[ \t]*\d+_\d+[ \t]*$'` with flags `'gn'` — `n` is what makes
`^`/`$` line anchors. Same regex as the renderer's, one source of truth in the
comment. In-sentence «الصورة {n}» is NOT touched, and must not be.

### 6.3 `backend/app/services/search_service.py`

- `PUBLIC_CORPORA` (`:66`) → `("regulation", "judgment", "circular", "compliance")`.
- `CORPUS_SECTION` += `"compliance": "compliance"` — ⚠ this is what keeps a
  search hit and a browse hit on the SAME guide charging ONE item against the
  budget (§5.4). Getting it wrong forks the budget silently.
- `_URL_PREFIX` += `"compliance": "/compliance"`.
- `FACET_KEYS` += `"compliance": frozenset({"provider_name", "service_ref", "sectors"})`.
- ⚠ **Leave the `service` corpus exactly where it is — but be precise about what
  it is.** `search_index` + `bm25_search()` ARE the navigation index: one table,
  one RPC, read by every hub search panel (`/regulations`, `/circulars`,
  `/judgments`, `/library`, `/mine`) and by the cross-wing SearchBar. `service`
  is a **vestigial row-set inside that navigation index** — 100 rows keyed by
  `services.id` carrying the retired wing's Arabic slugs, out of
  `PUBLIC_CORPORA` with no URL prefix, so no navigation surface has ranked it
  since 2026-08-03. It is NOT "the agents' corpus": `manual_search.py:222` maps
  its `services` data_type onto it, but as **rung ③** (`:824-841`), behind
  `search_topics` and a full-table ILIKE over all 4,746 services — its own
  comment calls it "thin, but it carries the only exact-title pin". Deleting the
  rows would cost that pin and nothing else; keeping them costs a nightly
  `refresh_search_index('service')` and one confusing corpus name. Either way it
  is §10's decision, not this plan's.
  Rewrite the `:65-70` comment: it currently reads "the compliance corpus is
  gone". It should read — the government-services NAVIGATION corpus is
  `compliance` (the guides, Latin slugs, `service_guides.id`); `service` is the
  inert legacy row-set kept alive for `manual_search`'s exact-title pin.

### 6.4 `frontend/lib/search/corpora.ts`

`SEARCH_CORPORA` (`:34`) += `"compliance"`; `CORPUS_LIBRARY_TYPE` (`:51`) +=
`compliance: "compliance"`, so the chip label comes from
`LIBRARY_TYPE_META.compliance.label` («الخدمات») and cannot drift from the
`LibraryTypeChips` row rendered a few pixels away. Rewrite the `:45-49` comment —
it currently records the corpus's REMOVAL and its restore condition; the
condition is now met, and the note should say so rather than be deleted.

### 6.5 The hub `q` switches from substring to BM25

`_compliance_matches`'s `q` branch (`library_service.py:3099`, the
"⚠ `q` HERE IS NOT BM25 AND MUST NOT BECOME IT" block) is now wrong in its
premise. Replace the substring with the `corpus_search_ids` → `rank_map` path the
other wings use (`library_service.py:1876`), keeping the section/provider/sector
predicates as post-filters. Rewrite that comment to record the reversal and its
date; do not delete it.

`q` stays **registered-only** (D9). The route drops it for anon before the
service sees it — unchanged.

---

## 7. Rollout order (traps encoded)

1. **Review §3's slug table with the user.** Permanent URLs.
2. **Apply the §6.1 migration via MCP + backfill** → `refresh_search_index('compliance')`
   returns 337. Migration precedes deploy, always
   (memories `moyasar`, `free-window-ladder`).
3. **Commit + push.** ⚠ The tree is always dirty — diff EVERY file before
   `git add` and confirm new files are tracked (memory `git-add-dirty`).
   `entities.py` and `entities.ts` go in the SAME commit.
4. **Deploy the backend FIRST, then the frontend.** The frontend Docker build
   ISR-bakes against the prod backend; ship them the other way round and the 28
   entity pages bake as 404s (memory `isr-bake-trap`). Note the Railway trap:
   frontend root is `/frontend`, so a backend-only commit does not redeploy it
   (memory `master-pull-trap`).

   ⚠ **AND THE FAILURE IS WORSE THAN A 404** (found in build, 2026-08-23).
   FastAPI silently IGNORES an unknown query param, so a frontend that ships
   ahead of the backend does not error on `?entity_slug=…` — it renders the
   **unfiltered hub, all 337 guides, under an entity H1**, and ISR bakes that.
   A 404 is loud and self-heals on purge; a plausible wrong page is neither.
   The search half has the same window: until `compliance` is in
   `PUBLIC_CORPORA`, `clean_corpora` drops `?corpus=compliance` and the new
   «الخدمات» chip silently searches every wing instead. Both are why this
   ordering is load-bearing rather than tidy.
5. **Purge ISR — mandatory.** `/compliance`, `/compliance/page/*`, all 28
   `/compliance/{entity}`, and every `/compliance/{guide}` whose card now carries
   an entity link. Slugs are ASCII so there is no percent-encoding to get wrong
   here — but the revalidate call still has to be checked for a real 200
   (memory `isr-revalidate-encoding`).
6. **Smoke** — §9.
7. **GSC**: the `compliance-entities` sitemap section appears; indexing lags ~10
   days (`scripts/check_indexing.py`, memory `indexing-audit`).

---

## 8. Tests

- `backend/tests/test_library_entities.py` — the live distinct `provider_name`
  set equals `ENTITY_ORDER`'s names (drift caught in CI, the `test_library_courts.py`
  contract); **no live guide slug collides with an entity slug or a reserved word**;
  `name_for_slug` returns None for `page`/`entities`/`mine`.
- Hub route: `entity_slug` unknown → 400; reserved → 400; valid → real
  `total_pages` for anon (proves it is out of `filtered`); page 2 anon →
  `cap_reached` (proves the depth cap survived D1); `entity_slug` + `sector_slug`
  together → still paid-only (proves the sector rule survived D1).
- `/public/library/compliance/entities` returns 28 rows summing to 337.
- Search: `refresh_search_index('compliance')` = 337; no indexed `lead` matches
  `^\s*\d+_\d+\s*$`; a query hitting only guide BODY text returns the guide;
  `public_url('compliance', slug)` = `/compliance/{slug}`.
- Frontend: `npx tsc --noEmit` + `npm run lint` (the Latin-numerals ESLint rule is
  live — memory `latin-numerals`).

---

## 9. Smoke (post-deploy)

1. `/compliance` renders the switcher; `view-source` shows all 28 links in the
   SSR HTML with counts summing to 337.
2. `/compliance/ministry-of-justice` — 115 guides, 13 pages, paginator prints the
   real total, **anon (logged out, private window) sees the cards**. This is the
   D1 acceptance test; if it walls, the exemption was not wired.
3. `/compliance/ministry-of-justice/page/2` anon → CTA wall, `noindex, follow`.
4. `/compliance/sfda` — one card, renders, indexable, no empty-state.
5. `/compliance/page/2` still the unfiltered hub (static segment still wins).
6. A live guide slug still resolves to its guide, not to a section.
7. SearchBar with «الخدمات» chip returns guides; clicking one lands on
   `/compliance/{slug}`, not a 404. Hub search panel returns BM25 results.
8. `grep` the rendered HTML of the search results and any entity page for
   `\d+_\d+` token lines — must be **zero**.
9. `/sitemaps/compliance-entities` → 28 `<loc>`s, valid XML, 200.

---

## 10. Out of scope

- **Un-gating the sector axis** on any wing, and the courts axis restore
  checklist (`app/judgments/courts/[court]/page.tsx` header). D1 is one wing.
- **Retiring the vestigial `service` rows** from the navigation index (and its
  nightly `refresh_search_index('service')`), or repointing
  `manual_search._CORPUS_BY_TYPE['services']` at the new `compliance` corpus.
  Worth taking separately once `compliance` is live and measured: the only thing
  the 100 rows still do is give `manual_search` rung ③ an exact-title pin
  (`:838-841`), and the guides may serve that better. Not a coverage problem —
  rung ② already ILIKEs the full 4,746-row table.
- **Guide RAG into agent context** — still the user's "separate later step"
  (the wing plan §9).
- HowTo/VideoObject JSON-LD; the stale `seo_item_meta` `'service'` rows;
  `services.steps/requirements/required_documents` (retrieval-only).
- An entity OVERVIEW page (`/compliance/{entity}` as an entity profile with
  description, links, related أنظمة). This plan's entity page is a filtered hub
  and nothing more.

---

## 11. Build log (2026-08-23)

Built by three parallel agents on disjoint file sets (migration · backend ·
frontend). What follows is what the plan got wrong and what only surfaced once
the code met a compiler, a test, or the live corpus. Recorded here rather than
silently patched into the sections above, because each one is a trap that will
recur.

### 11.1 🚨 The `taqeem` label was corrupted IN THIS PLAN

§3 row 22 was retyped as `الهيئة السعودية للمقيّمين المعتمدين (تقييم)`.
The live `provider_name` is `الهيئة السعودية للمقَيّمين المعتمدين (تقييم)` —
**U+064E ARABIC FATHA after ق** (`قَيّ`), lost in
transcription. The entity predicate is `eq`, so shipping the plan's string
verbatim would have given `taqeem` **zero rows and a «0» tile, silently** — no
error, no 404, just an entity that looks empty.

Caught by the live-corpus test in `test_library_entities.py`, which is precisely
why that test asserts against the corpus instead of against this file. Fixed in
all three places (plan · `entities.py` · `entities.ts`).

⚠ **THE GENERAL RULE: an Arabic string that is a QUERY PREDICATE must be copied
from the corpus, never retyped from a document.** Diacritics survive a
copy-paste and die in a re-type, and the failure is silent because `eq` on a
non-existent value is a legal query. `courts.py` has the same exposure on its
raw-variant lists.

### 11.2 Files the plan did not know about

The frontend's corpus registry is more closed than §6.4 assumed — adding one
value to `SEARCH_CORPORA` broke three exhaustive maps:

| File | Why it had to change |
|---|---|
| `lib/search/copy.ts` | `SearchSurface` is a closed union pinned by `satisfies Record<SearchSurface, …>`; `HubSearchPanel section="compliance"` does not typecheck without its own Arabic placeholder/aria copy |
| `components/library/search/LibrarySearchResultRow.tsx` | `CORPUS_ICON: Record<SearchCorpus, …>` + `hitMeta`'s exhaustive switch |
| `SEARCH_LIBRARY_COPY` (in `copy.ts`) | its lead said **«الأقسام الثلاثة»** while the chip row renders `SEARCH_CORPORA.length` — now four. Prose that counts a collection it does not read |

Also already correct and needing nothing: `library_items_service._URL_PREFIX`
and `SHELF_CONTENT_TYPES` already carry `compliance`, so مكتبتي shelf search
picked up the corpus for free.

### 11.3 `provider_name` could not become a link in place

§5.6 asked for the card's entity name to become a `<Link>`. `CardShell` already
wraps the card body in the card's own `<a>`, so that is a nested anchor the
parser silently un-nests. Built via the `footer` slot instead — the mechanism
`JudgmentCard` uses for `SectorPills`. **Consequence: the entity chip sits at the
FOOT of the card, not above the title.** A deliberate deviation, worth a look on
the rendered page before ship.

### 11.4 Two `test_library_compliance.py` tests asserted the premise §6.5 reverses

`test_hub_q_matches_title_and_summary` and `test_hub_search_reports_an_exact_total`
encoded the OLD substring contract — the second's docstring literally reads
«never a BM25 set truncated at `HUB_SEARCH_LIMIT`». Repair also needed
`FakeSupabase.rpc`, which hard-asserted `name == _SECTOR_COUNTS_RPC` and so
raised on `bm25_search`. Not a rename: the fake needed a real seeded branch.

### 11.5 Deploy-order failures are all SILENT here

Verified against prod during the build — every one of these returns HTTP 200:

- **Backend before migration** → `/compliance?q=` returns `items: 0` with no
  error. A smoke test that only checks status codes passes.
- **Frontend before backend** → FastAPI ignores the unknown `entity_slug`, so an
  entity page renders **all 337 guides under an entity H1** — and ISR bakes it.
- **Frontend before backend, search half** → `clean_corpora` drops
  `?corpus=compliance`, so the new «الخدمات» chip searches every wing instead.

§7's ordering is load-bearing, and §9's smoke steps must assert CONTENT, never
status codes.

### 11.6 Smaller notes

- `refresh_search_index`'s current definition is migration **112**, not 111 — 112
  added `entity_text` at weight B. Based on 111 the new branch would not compile.
- `.gitignore` gained one `!backend/tests/test_library_entities.py` re-include:
  `backend/tests/*` is ignored with per-suite re-includes, so without it `git add`
  drops the new suite silently (memory `git-add-dirty`).
- `_compliance_published_rows` return arity changed 2 → 3 (`rows, slugs, truncated`).
- Every line number in §4 drifted (`list_compliance` :2537 not :2468,
  `_compliance_matches` :3149 not :3099, `CourtSummary` :1919 not :1854). Treat
  line numbers in this plan as of 2026-08-22 and re-grep.
- The tree still says "169 guides" in ~8 comments; most are measured RATIOS at
  n=169 ("168 of 169") that cannot be restated without re-querying. Left alone
  deliberately — a re-derived ratio would be worse than a stale one.

### 11.7 Two things §6.5 makes newly reachable — decide the UI copy before ship

Found while repairing the tests, both verified against the live corpus.

**a. The 200-hit cap is ORDINARY on this wing, not pathological.** Every one of
the 337 titles begins with the same 12 characters — `الدليل الشامل: {service} في
السعودية`, one distinct prefix across the whole corpus (verified 2026-08-23).
Title is weight A, so any query containing «الدليل», «الشامل» or «السعودية»
matches all 337, exceeds `HUB_SEARCH_LIMIT` (200) and returns
`total_count_is_exact = False`. On the other wings that flag is an edge case;
here it will be the common case. (The shared prefix is NOT a title-tag bug —
`lib/library/guide.ts` already treats it as a prefix REWRITE, not an append, and
its "all titles begin with `GUIDE_PREFIX`" note still holds at 337/337.)

**b. `total_count_is_exact` conflates truncation with post-filtering.**
`truncated` is computed from the PRE-filter id count (200); `total_count` is
`len(rows)` AFTER `provider`/`sector`/`entity` post-filtering. So
`?entity_slug=ministry-of-justice&q=…` can legitimately return
`total_count = 4, total_count_is_exact = False`. Conservative, not a lie — but
`_hub_result`'s docstring proposes the copy «أفضل 200 نتيجة» for the inexact
case, and 200 would then count nothing the reader can see.

This is shared with every wing, but **D1 + §6.5 make `entity_slug` + `q` a
combination that did not exist before**, so compliance entity pages are where
that copy reads wrong first. Whatever the frontend renders for the inexact case
must not name a number it cannot justify. Not fixed here — it is a copy decision
across four wings, not a compliance bug.
