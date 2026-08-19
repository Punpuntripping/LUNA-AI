# /compliance — Service Guides Wing (`service_guides`)

**Date:** 2026-08-19 · **Status:** PLANNED
**Decision trail:** /reflect session 2026-08-18/19 with the user. Supersedes the
`compliance_table` placeholder design recorded in
`.claude/plans/…` comments and `memory/project_compliance_wing_rebuild.md`.

---

## 0. The decision (what changed and why)

The `/compliance` wing — live, wired, and deliberately EMPTY since 2026-08-04 —
gets its content: **`service_guides`**, built 2026-08-18 by the
`agentic_for_ministry` ingestion pipeline **directly into Luna's own Supabase
project** (`dwgghvxogtwyaxmbgjod`). The hypothetical `compliance_table` will
never exist; `service_guides` IS the table the wing was waiting for.

**The founding rule is formally superseded.** The old wing was retired for
republishing the `services` corpus (someone else's procedure text). A service
guide is different in kind: it is **our own authored rewrite of the issuing
entity's official PDF user-guide**, with our own screenshots pipeline. The user
confirmed: "we rewrote it." Every comment in the codebase that still tells the
"must never grow a copy of a procedure" story must be rewritten to the new one
(§8 — those comments are load-bearing here and MUST NOT survive stale).

**User decisions (binding):**
1. Target = the public `/compliance` library wing: hub + new `/compliance/{slug}`
   detail pages. Consumer contract = `C:\Programming\agentic_for_ministry\ingestion\service_guides\REFERENCE.md`.
2. **Fully ungated, SEO target.** No FullContentGate, no metered unlock on the
   guide body, anon sees everything including screenshots.
3. **Latin slugs**, derived per service.
4. **`source_pdf_url` is NEVER shown.** The only outbound link is the service's
   own page (`services.service_url`). Enforced structurally: the API payload
   simply does not carry `source_pdf_url`.
5. Chat integration now = **sources-level only**: when a cited service has a
   guide, the reference dialog's library exit reads
   **«افتح الدليل الشامل للخدمة في ريحان»** → `/compliance/{slug}`.
   Full guide RAG into agent context is a LATER separate step.
6. Wing lists ONLY services that have guides (~169) — that's the story:
   «دليل مبسط لأكثر الخدمات استخداماً». No fallback listing of guide-less services.
7. **Every guide title carries «الدليل الشامل بالصور»** (user, 2026-08-19).
   ⚠ VERIFIED LIVE: **all 169 titles already start with the exact prefix
   «الدليل الشامل: »** — so this is a PREFIX REWRITE, not an append. Appending
   would produce «الدليل الشامل: … — الدليل الشامل بالصور».
   `الدليل الشامل:` → `الدليل الشامل بالصور:`, leaving the rest untouched.
   Carve-out: the **10 zero-image guides keep «الدليل الشامل:»** — «بالصور» on a
   guide with no صور is a lie. One helper owns it (§5.1).

---

## 1. Verified live state (queried 2026-08-19 — do not re-derive)

| Fact | Value |
|---|---|
| `service_guides` rows | **169**, all `is_canonical=true`, 0 aliases, 0 duplicate `service_id` |
| `service_guide_images` rows | **3,180**, `uploaded_at` NULL count = **0** (all bytes in Storage) |
| Zero-image guides | 10 (legitimate text-only guides) |
| Distinct entities | 29 |
| `most_used_rank` NULLs | 0 — safe to order by it directly |
| Guides missing `services.service_url` | 0 |
| RLS | already ENABLED on both tables |
| Bucket `service-guide-images` | already **PUBLIC** (user flipped it) — plain URLs, no signing |
| `seo_item_meta` types | article 5 · circular 1,843 · judgment 10,000 · regulation 3,513 (1,686 slugged) · **service 4,717 (100 slugged — STALE Arabic slugs from the retired wing, keyed by `services.id`. DO NOT touch, DO NOT reuse)** |

Public image URL shape (no auth, no expiry):
`https://dwgghvxogtwyaxmbgjod.supabase.co/storage/v1/object/public/service-guide-images/{storage_path}`

**The rendering contract** (REFERENCE.md §3–§4, non-negotiable):
- Hole = a line that is ONLY `\d+_\d+` → `^[ \t]*(\d+_\d+)[ \t]*$` (multiline).
- Resolve by `image_ref` ONLY. Never by position (28% of guides are out of
  numeric order), never by `الصورة {n}` (2,804 in-sentence occurrences would corrupt prose).
- Unresolved hole ⇒ emit NOTHING. A raw token leaking to a user page is THE failure mode.
- `description` is the alt text / caption — a real Arabic sentence.

---

## 2. Slug design (the one new data artifact)

**Home:** `seo_item_meta`, **`content_type='compliance'`**, `content_id =
service_guides.id::text`, `indexable=true`, `rank = most_used_rank`. A NEW
content_type — the stale `'service'` rows are a different key space (services.id)
with Arabic slugs; leave them untouched.

**Shape:** short English kebab-case, `^[a-z0-9]+(-[a-z0-9]+)*$`, 2–6 words,
derived from the guide title + provider (e.g. `issue-work-visa`,
`renew-commercial-registration`). PERMANENT once written — same rule as
`build_seo_slugs.py`: never rewrite an existing slug.

**New script `scripts/build_compliance_slugs.py`** (mirror the discipline of
`scripts/build_seo_slugs.py`, read its header first):
- LLM pass (tier_2 flash via the existing model plumbing) proposes the Latin slug
  from `{title, provider_name}`; deterministic fallback `service-{service_ref}`.
- Validate against the regex; collisions within `(content_type, slug)` get `-2`, `-3`…
  in stable id order (the sidecar's partial unique index enforces it).
- MERGE-upsert on `(content_type, content_id)` writing only `slug`, `rank`,
  `indexable`, `updated_at`.
- `--dry-run` DEFAULT printing the full 169-row table for human review;
  `--apply` to write; `--unpublish --ids-file` for the committed reverse
  (unpublish = slug→NULL UPDATE, never DELETE). No `--unpublish-all`.

---

## 3. Migration — `shared/db/migrations/142_compliance_wing.sql`

### 3.0 ⚠ THE BLOCKER — CHECK constraints (found 2026-08-19, mid-build)

The plan originally said "the migration is one view". **That was wrong.** Three
live CHECK constraints reject `content_type='compliance'`:

| constraint | table | needed? |
|---|---|---|
| `seo_item_meta_content_type_check` | `seo_item_meta` | **REQUIRED** — the slug IS the publish mechanism; without it nothing can be published at all |
| `library_items_content_type_valid` | `library_items` | **REQUIRED** — else the shelf beacon silently no-ops and «حفظ في مكتبتي» 500s |
| `library_unlocks_content_type_valid` | `library_unlocks` | parity only — nothing writes it (ungated wing; hub metering stores string keys in a window, NOT rows here). Included so a future decision to gate a guide isn't a production 500. |

Drop + re-add each idempotently, appending `'compliance'` and preserving the
existing values exactly. Keep text+CHECK; do NOT convert to an enum.
`topic_map_content_type_check` is deliberately NOT touched (unified-topics scope).

Why a NEW content_type rather than reusing `'service'`: the 4,717 existing
`'service'` sidecar rows are a different key space — keyed by `services.id`,
carrying stale Arabic slugs from the retired wing. Reusing that type collides
with them and ships 404s.

### 3.1 The view

RLS is already on and the bucket is already public, so the rest is one view:

```sql
-- Hub/read surface: service_guides ⋈ services, because the sector axis
-- (`sectors` array) and provider/service_url live on `services` while the guide
-- body lives on `service_guides`. A view keeps app-owned shape OFF the
-- pipeline-owned tables (the ingest may rebuild them).
create or replace view public.library_compliance_v
with (security_invoker = true) as
select g.id, g.service_id, g.service_ref, g.title, g.summary, g.guide_md,
       g.image_count, g.most_used_rank,
       s.provider_name, s.service_url, s.sectors
from   public.service_guides g
join   public.services s on s.id = g.service_id
where  g.is_canonical;
```

Idempotent, `sql-migration` agent conventions. Apply via MCP `apply_migration`
BEFORE any deploy (migration-before-deploy trap — memory `moyasar`,
`free-window-ladder`).

---

## 4. Backend

### 4.1 `backend/app/services/library_service.py`
- **Rename `COMPLIANCE_TABLE_READY` → `COMPLIANCE_WING_READY`** and flip to
  `True` (there is no `compliance_table`; the name must stop lying). Sweep:
  `__all__` (line ~215), both listers, `public_library.py` docstrings, tests.
- **`list_compliance_hub`** (line ~2972) — real implementation, sample-mode style
  (the whole corpus is ≤169 rows; mirror `list_circulars_hub`'s published-set
  path, line ~3539): published ids = slugged sidecar rows
  (`_published_ids('compliance')`); fetch from `library_compliance_v`; filters in
  Python — `provider` ilike on `provider_name`, `sector` = §7.1 containment on
  `sectors`, `q` = title+summary contains (signed-in only, route already gates);
  **order `most_used_rank` asc** (most-used first), tiebreak `(title, id)`; page
  size `HUB_PAGE_SIZE` (9). Card dict: `{slug, title, provider_name, summary,
  image_count}`.
- **`compliance_hub_total_pages`** (line ~2953) — same filtered set, `ceil/9`, floor 1.
- **New `get_compliance_guide(supabase, slug)`** — mirror `get_circular_doc`
  (line ~3639): sidecar `('compliance', slug)` → `content_id` →
  `library_compliance_v` row → images by `guide_id` ordered `image_index`.
  Returns `{slug, title, summary, provider_name, service_url, image_count,
  guide_md, images: [{image_ref, description, url, width, height}]}` where `url`
  is the public bucket URL. **No `source_pdf_url` anywhere in the payload** —
  that is how decision #4 is enforced. **Server-side hole hygiene:** before
  returning, blank any hole line whose `image_ref` has no image row (defense in
  depth — REFERENCE.md §3.2 rules 1–2; today that set is empty, invariant §8).
  No gate resolution — the wing is open by design.
- **`_SECTION_SOURCES`** (line 1977) += `"compliance": ("library_compliance_v",
  "compliance", "sectors")`. `SECTOR_COUNT_SECTIONS` follows automatically —
  sector pages grow the compliance tab, `SectorPreview.compliance` fills, and
  `LibraryCounts.compliance` goes real. Update the 2026-08-03 block comment
  (lines 1963–1976) and the `LibraryCounts` docstring
  (`public_library.py:1716-1732`).

### 4.2 `backend/app/api/public_library.py`
- `ComplianceHubItem` (line 1415) += `image_count: int = 0`; REWRITE its
  docstring (§8 story).
- **New `ComplianceGuideDoc` model + route `GET
  /public/library/compliance/{slug}`** — mirror the circulars doc route's guards
  and caching exactly (`_LIBRARY_CACHE_CONTROL` 1h public — the body is identical
  for every caller, so the anon cache is safe); 404 Arabic «الدليل غير موجود».
  Rate limiting already collapses `/compliance/:item`
  (`rate_limit.py:187-193` anticipated this — no change).
- `_LIBRARY_SITEMAP_SECTIONS` (line 110) += `"compliance": ("compliance",
  "compliance")`; rewrite the 2026-08-03 comment (lines 115–117). The sidecar
  drives it — only slugged+indexable guides are listed.

### 4.3 Chat/library exits — `backend/app/services/library_items_service.py`
- `_URL_PREFIX` (line 127) += the compliance arm → `/compliance`.
- `public_page_urls_for_reference_rows` (line ~986): add the compliance-domain
  resolution — reference row identity → `service_guides` (canonical, by
  `service_id` or `service_ref`) → sidecar `('compliance', guide_id)` slug →
  `/compliance/{slug}`. **First verify live what
  `workspace_item_references.item_id` / `ref_id` actually hold for
  `domain='compliance'` rows** (the URA mints `ref_id =
  compliance:{hash(service_ref)}` — `references_service.py:772-790`); one SQL
  look before coding, per the fail-soft rules already in that function. Batched,
  ≤1 extra round-trip. Rewrite "COMPLIANCE NEVER GETS A URL" (lines 992, 1008–1010, 1082).
- `references_service.py` line ~264 comment (compliance → None) — now resolves.
- ⚠ **VERIFIED 2026-08-19:** compliance reference rows key on **`item_id` = `services.id`**
  (509/509 rows, 100%). `ref_id` is `compliance:{sha1(service_ref)[:16]}` — a
  one-way digest, so unlike every other wing there is **no `ref_id` fallback
  arm**; a row without `item_id` gets no button rather than a guess.
- ⚠ **TWO RESOLVERS, ONLY ONE FIXED.** The card's URL comes from the LIST path
  (`public_page_urls_for_reference_rows`, now compliance-aware); the DIALOG's
  comes from the REVEAL path via `reference_resolver.py`, which maps a compliance
  citation to `('service', services.id)` → `public_page_url('service', …)` →
  always `None`. Result without a fix: card shows the button, dialog drops it.
  Worked around in `ReferencePanel.tsx` (`library_url ?? reference.library_url`).
  **FOLLOW-UP:** fix `reference_resolver.py` properly so the two resolvers agree.
- **My-library shelf:** extend the my-library `content_type` set with
  `compliance` (backend validation + slug→id resolution via the sidecar, mirror
  the circular arm) so the guide page's `LibraryUseBeacon` writes shelf rows and
  `ShelfCard` links back.

---

## 5. Frontend

### 5.1 Detail page — NEW `frontend/app/compliance/[slug]/page.tsx`
Mirror `app/circulars/[slug]/page.tsx` structurally: `generateMetadata`
(title/description from doc, canonical `/compliance/{slug}`, OG via `/og?title=`,
**no `robots` key — indexable**), breadcrumbs `الرئيسية / دليل الخدمات / {title}`,

**Title treatment (decision #7):** a single helper
`guideDisplayTitle(title, image_count)` in `frontend/lib/library/guide.ts`:

```ts
const GUIDE_PREFIX = "الدليل الشامل:";
const GUIDE_PREFIX_IMAGES = "الدليل الشامل بالصور:";
// All 169 corpus titles start with GUIDE_PREFIX (verified live 2026-08-19), so
// this is a REWRITE of that prefix — appending would double it. A title that
// somehow lacks the prefix is returned untouched: inventing a prefix for an
// unknown title shape is worse than leaving it alone.
export function guideDisplayTitle(title: string, imageCount: number): string {
  const t = (title ?? "").trim();
  if (imageCount <= 0 || !t.startsWith(GUIDE_PREFIX)) return t;
  return GUIDE_PREFIX_IMAGES + t.slice(GUIDE_PREFIX.length);
}
```

It feeds the page **H1**, the SEO `<title>`
(`{display} | ريحان`), OG/Twitter titles, the `/og?title=` image, and the
JSON-LD `headline` — **and the HUB CARDS** (`ComplianceCard`), which already
repeat «الدليل الشامل:» today because the prefix is in the corpus; rewriting it
there too is what makes «بالصور» consistent everywhere the user sees a title.
The BREADCRUMB last crumb stays plain (it sits under a «دليل الخدمات» crumb
already). `image_count` is in both payloads (§4.1/§4.2), so no backend change.
`LibraryPageShell maxWidth="doc"`, JSON-LD = `buildArticle` (NO paywall fragment
— open content; HowTo schema deliberately out of scope v1),
`LibraryUseBeacon contentType="compliance" gate="open"`,
`OfficialSources` = one link, label **«صفحة الخدمة على موقع الجهة الرسمي»** →
`service_url`, `AskRayhanWidget`.

### 5.2 Guide body — NEW `frontend/components/library/blocks/GuideBody.tsx`
The renderer that owns the REFERENCE.md contract client-side:
- Split `guide_md` on `/^[ \t]*(\d+_\d+)[ \t]*$/gm`; text segments through the
  existing library markdown body component; each resolved hole renders
  `<figure><img src={url} width height alt={description} loading="lazy"
  decoding="async"/></figure>` (width/height reserve the box — CLS). First image
  may skip `loading="lazy"`.
- ⚠ **NO `<figcaption>`** (decision #8, user 2026-08-19): `description` is
  400–1,031 chars of analysis written for the **agent/RAG layer**, not reader
  copy. It stays as `alt` only — invisible to sighted users, and removing it
  would blind screen readers and crawlers on an SEO wing. **This OVERRIDES
  REFERENCE.md §3.2 rule 3 ("alt text and/or caption") and §3.3** (text-only
  channels printing the description) — those remain right for an agent rendering
  a guide into a chat reply, and wrong for this page.
- Token with no entry in the images map ⇒ render NOTHING (contract rule 1).
- Keep the split helper a pure function in `frontend/lib/library/guide.ts` so
  it's testable and reusable; images map keyed by `image_ref`.
- Arabic `description` text is corpus body text — the Latin-numerals ESLint
  policy's carve-out applies (memory `latin-numerals`).

### 5.3 Existing files
- `frontend/lib/library/api.ts`: `ComplianceHubItem` += `image_count` (line 434);
  new `ComplianceGuideDoc` type + `getComplianceGuide()` fetcher (same ISR
  revalidate window as `getCircularDoc`); rewrite comment block 424–432.
- `app/compliance/page.tsx` **and** `app/compliance/page/[n]/page.tsx`: DELETE
  `EMPTY_WING_ROBOTS` (+ its comment) — the four-flip rule, same change as the
  backend flag.
- `frontend/lib/seo/sitemap.ts`: `SITEMAP_SECTIONS` += `"compliance"`; plus its
  case in `app/sitemaps/[section]/route.ts`.
- `frontend/lib/anon-cta/eligibility.ts`: `WINGS` += `"compliance"` (line 16) —
  the anon conversion popup may now fire on guide pages.
- `frontend/components/library/hub/ComplianceCard.tsx`: optionally surface
  `image_count` («{n} خطوة مصوّرة» hint) — small, not load-bearing.
- `frontend/lib/api` `MyLibraryContentType` += `'compliance'` (see §4.3 shelf).
- Site nav: the wings nav currently has NO compliance entry (deliberately
  withheld). Locate the library dropdown/links in the global header components
  and add «دليل الخدمات» → `/compliance`. (`LibraryTypeChips` on `/library`
  starts showing the real count automatically once §4.1 lands.)

### 5.4 Reference dialog — `frontend/components/workspace/ReferencePanel.tsx`
- The two-exit action bar (lines 719–860) already renders `libraryUrl` when the
  backend supplies it — §4.3 makes that happen for `gov_service`. Add the
  definite-type/label arm so the button reads
  **«افتح الدليل الشامل للخدمة في ريحان»** (find the «فتح ال… في ريحان» label
  composition next to line 839).
- `gov_service` body copy (lines 1162–1177) + `types/index.ts` `gov_service`
  view docstring (lines 777–793): rewrite the retired-wing story (§8).

---

## 6. Tests

- **NEW `backend/tests/test_library_compliance.py`**: lister ordering
  (`most_used_rank` asc), filters, pagination floor, hub item shape incl.
  `image_count`; guide payload — **asserts `source_pdf_url` is absent**, image
  URL shape, images ordered by `image_index`, zero-image guide works; the
  server-side hole-hygiene pass; sitemap section returns slugged guides; sector
  counts include compliance.
- **Update `backend/tests/test_library_sector_wing.py`** (line ~79: "compliance
  is 0 and STAYS 0" — no longer true) and any test pinning
  `COMPLIANCE_TABLE_READY` / the empty-wing behavior.
- **`backend/tests/test_reference_library_links.py`**: gov_service reference →
  `/compliance/{slug}` when a guide exists; None when not (guide-less services
  must still render title+URL only).
- Frontend: `npx tsc --noEmit` + `npm run build`; the split helper is pure — if
  a frontend unit-test harness exists, cover: token line resolved, unknown token
  dropped, `الصورة {n}` inside prose untouched.

---

## 7. Rollout order (traps encoded)

1. **Migration 142** via MCP `apply_migration` (view only — data already live).
2. **Slugs:** `python scripts/build_compliance_slugs.py` (dry-run, eyeball all
   169), then `--apply`. Data is inert in prod — the deployed flag is still off.
3. **One deploy** carrying ALL the flips together (flag + `_SECTION_SOURCES` +
   both sitemap sides + robots deletion + routes): backend first, then frontend
   (its Docker build ISR-bakes against prod backend — memory `isr-bake-trap`).
4. **Purge ISR** — mandatory, baked pages otherwise serve the empty wing
   indefinitely; percent-encode revalidate paths or the 200 lies
   (memory `isr-revalidate-encoding`).
5. **Smoke:** `/compliance` hub shows guides (169 ⇒ 19 pages; anon sees page 1 +
   CTA wall — SEO does NOT depend on hub depth, the sitemap section carries all
   169 detail URLs); open a multi-image guide, a zero-image guide, and the
   69-image guide; **grep the rendered HTML for `\d+_\d+` token lines — must be
   zero**; H1/`<title>` carry «— الدليل الشامل بالصور» (and the zero-image guide
   drops «بالصور»); source exit goes to `service_url`, PDF URL appears nowhere; chat: cite
   a guided service → dialog shows «افتح الدليل الشامل للخدمة في ريحان».
6. GSC: sitemap picks up `compliance`; indexing lags ~10 days
   (`scripts/check_indexing.py`).

## 8. The comment-story rewrite (explicit step, not incidental)

Every retired-wing comment must tell the new story: *guides are Rayhan's own
authored rewrite of the issuing entity's official PDF; the wing publishes them
in full, ungated; the entity's service page is the only outbound link; the
source PDF is never surfaced.* Sites: `library_service.py` 1963–1976 +
2922–2950; `public_library.py` 115–117, 1415–1428, 1716–1732, compliance route
docstring ~2407; `api.ts` 424–432; `types/index.ts` 777–793;
`ReferencePanel.tsx` 1162–1177; `library_items_service.py` 122–126, 1008–1010,
1082; `references_service.py` ~264; `AuthGuard.tsx` 28–30; hub page headers in
`app/compliance/`. Memory file `project_compliance_wing_rebuild.md` gets
superseded at ship time.

## 9. Explicitly OUT of scope (v1)

- **BM25 navigation corpus** for guides (SearchBar's registered surfaces) — the
  old `service` corpus was deliberately removed; re-adding is its own decision.
- **Guide RAG into agent context** (chat answering from `guide_md` + showing
  step screenshots) — the user's "separate later step".
- HowTo/VideoObject JSON-LD (the retired wing had it; start with Article).
- Touching the stale `seo_item_meta` `'service'` rows.
- `services.steps/requirements/required_documents` — stay retrieval-only.
