# SEO Public Library — /regulations · /judgments · /circulars · /compliance · /forms · /calculators

**Status:** PLANNED 2026-07-22 — nothing built
**Goal:** Win Google rankings for Saudi legal queries by publishing the corpus as a public
programmatic reference library (~68k+ URLs), gated so searchers get real value ungated (rank +
engagement) while full documents, AI شرح, forms, and اسأل ريحان answers require a free account.

## Strategy

- Head terms («نظام العمل») are owned by official gov sites — the winnable battlefield is the
  **long tail**: per-مادة queries, question queries, judgment-principle queries, service
  how-tos, form templates («صيغة عقد عمل»), calculators. Competitors (mohamah, mqyas) monetize
  exactly this and have no AI شرح, no cross-reference mesh, no chat.
- `cross_references_v2` (judgment ↔ مادة ↔ regulation graph) becomes an internal-linking mesh no
  Saudi competitor has.
- Every indexed page is genuinely useful ungated (no thin content, no cloaking); the *depth* —
  continuous reading, شرح, full judgments, forms, unlimited mesh, chat — is the signup carrot.

## Locked decisions

| Decision | Value |
|---|---|
| URL scheme | Flat top-level English sections + Arabic document slugs (stored columns, never computed). `/judgments` not `/cases` (private route); `/forms` not `/templates` (private قوالبي); `/compliance` reads the `services` TABLE (H1s keep خدمة/متطلبات keywords). |
| Template system | One shared block library (`components/library/blocks/`); wings = thin routes + block config. Blocks: TopicBreadcrumbs, TrustLine, MetadataCard(+StatusBadge), LeadSummary, TocList, ArticleBody+GateBanner, CalculatorBlock, PdfViewerBlock, MediaBlock, FaqBlock, ReferencesMesh, OfficialSources, ReadAfter, AskRayhanWidget, OpenInRayhanCta, LibraryPageShell. |
| Gating | Per-item `gate_override` (open\|gated\|NULL=inherit) over `seo_gate_defaults` section policy; regulations default via popularity `seo_tier` (open ~top 50–100 / gated long tail, re-tiered from GSC). **Storage = sidecar `seo_item_meta (content_type, content_id, slug, seo_tier, gate_override)` — NOT columns on corpus tables** (v2 "tables" are views over pipeline-owned schema `regulation_v2`; re-ingests would clobber columns — discovered live 2026-07-22). ONE resolver `library_service.resolve_gate()` feeds truncation + GateBanner + PDF proxy + paywall JSON-LD. Toggle via `scripts/set_gate.py` + on-demand ISR revalidate. |
| Gate mechanics | Server-side truncation — gated bytes NEVER reach anon clients (placeholder bars, not CSS blur). Same page for users and Googlebot + paywall markup (`isAccessibleForFree:false`+`hasPart`) + `max-snippet`. No `data-nosnippet` on free article text (featured snippets wanted). No UA sniffing ever (cloaking). |
| Hub browse-depth | Master-hub pagination **9 items/page (3×3 grid)** capped ~page 3 for anon/free (27 items browsable), server-enforced in hub endpoints («تصفح المكتبة كاملة» = account feature); topic hubs list ALL items (link equity). |
| Rendering | ISR (`revalidate: 86400` docs / `3600` hubs) — NOT blog's force-dynamic/no-store (blog's view_count-on-read conflicts with ISR; leave blog as is). |
| Taxonomy | Shared 2-level `topics` + `topic_map` across ALL content types → cross-type `/topics/{slug}` hubs (نظام + مواد + judgments + services + calculators + blogs per topic). |
| PDF viewer | Gate-consistent: reg/judgment PDFs = the full document → anon gets first-page preview + CTA; compliance PDFs free. Served via backend proxy, always `X-Robots-Tag: noindex`, lazy pdf.js. |
| Indexation ramp | Sitemap waves (regs+compliance → articles/forms/calculators → judgments → circulars), never 68k day one. |

## Default gating policy (seed for `seo_gate_defaults`)

| Always gated | Split | Never gated |
|---|---|---|
| اسأل ريحان answers (after teaser) · judgment full text · شرح (2-line teaser) · continuous-doc reading + reg/judgment PDFs · form bodies + downloads · mesh beyond first 3 | مادة text (open tier free / long tail first-lines) · circular body (length threshold ~800 chars) | compliance pages · calculators · summaries · TOCs · metadata cards · blog · form intro/شرح-when-to-use |

Principle: **gate what is scarce and professionally valued; open what is public-domain and does
the ranking work.**

## Data inventory (live-verified 2026-07-22)

`regulations_v2` 3,373 · `chunks_v2` 37,943 · `chunk_titles_v2` (⚠ NEVER render from it —
known wrong-title bug) · `articles_v2` · `cross_references_v2` · `cases` 20,671 (bigger ingest
coming) · `circulars` 1,843 · `services` 4,717 (structured steps[]/requirements[]/
required_documents[]/youtube_url/is_most_used) · `entities` 338.
⚠ `agents_reports/db_state.md` (2026-03) counts are stale. Corpus tables are RLS-OFF internal —
public access ONLY via backend anon endpoints (service role).
⚠ **Discovered live 2026-07-22:** `regulations_v2`, `chunks_v2`, `chunk_titles_v2`,
`articles_v2`, `cross_references_v2` in `public` are **VIEWS** over the pipeline-owned schema
`regulation_v2` (base tables `regulation_v2.regulations`, `.chunks`, …). Only `cases`,
`circulars`, `services` are base tables in `public`. Never ALTER the corpus surface — all SEO
state lives in the `seo_item_meta` sidecar (migration 095).

## Content sources (where every page's content comes from)

No external fetching at runtime — pages read ONLY our Supabase tables via the backend.

| Content | Source | State |
|---|---|---|
| Reg doc pages (metadata, summary, status, TOC) | `regulations_v2` | already in DB |
| مادة text | `chunks_v2.content` via derived `seo_articles` (+`articles_v2`) | already in DB (index built by script) |
| شرح / FAQs | AI-generated from chunk content → `seo_sharh` / `seo_faqs` caches | generated Phase 3 |
| Mesh (related content) | `cross_references_v2` + `topic_map` | already in DB |
| Compliance pages | `services` (structured columns) | already in DB |
| Circulars | `circulars` | already in DB |
| Judgments | `cases` now; **bigger external ingest** (Phase 5) + AI-generated titles/summaries/principles | external prereq |
| Forms | created by `scripts/draft_forms.py` (AI draft) → human review → `forms` table | generated Phase 3 |
| Calculators | code registry (formulas) | written, no data |
| PDFs | ONE-TIME optional mirror of official `pdf_url` files → Supabase Storage | batch, Phase 1 |

Runtime path: page → Next server component → `/api/v1/public/library/*` → `library_service`
(service role, gate truncation) → ISR cache. No live gov-site calls, no scraping, no
third-party content APIs.

## Key infra facts (codebase scan 2026-07-22)

- Copy `/blog/*` anon pattern (`backend/app/api/blog.py` — public = no `get_current_user` dep;
  server components + plain fetch) but with ISR + Cache-Control, no per-read counter writes.
- Real public-route gate = `AuthGuard.tsx` `PUBLIC_PREFIXES` (~line 23); `middleware.ts` is a
  no-op. robots.ts keeps `/cases` + `/templates` disallowed.
- SEO greenfield: zero JSON-LD/canonicals/OG images; sitemap = 4 static URLs, blog absent.
- Post-login intent mechanism (chat-with-blog → AuthGuard consumer) is the template for the
  popup claim flow and the forms→writer handoff.

---

# PAGE TEMPLATES & BLOCK SYSTEM (design reference — phases below build this)

## Block matrix — which blocks render on which page type

| Block | Component | Reg doc | مادة | Judgment | Circular | Compliance | Form | Blog | Calculator |
|---|---|---|---|---|---|---|---|---|---|
| Breadcrumbs + topic chips | `TopicBreadcrumbs` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| H1 + trust line (آخر تحديث, disclaimer, entity) | `TrustLine` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Metadata card (المعلومات الأساسية + status badge) | `MetadataCard` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ (use-case, category) | – | – |
| Description / summary lead | `LeadSummary` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| TOC | `TocList` | ✓ | – | – | – | steps | – | ✓ | – |
| Article body (+ gate boundary) | `ArticleBody`+`GateBanner` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ (intro free, body gated) | ✓ | intro |
| Calculator (embedded) | `CalculatorBlock` | where relevant | where relevant | – | – | where relevant | – | where relevant | ✓ (main) |
| PDF viewer (gate-consistent, lazy) | `PdfViewerBlock` | ✓ gated | – | ✓ gated | if pdf | ✓ free | download gated | – | – |
| Media (YouTube / VideoObject) | `MediaBlock` | – | – | – | – | ✓ | – | ✓ | – |
| FAQ (FAQPage schema) | `FaqBlock` | ✓ | ✓ | – | – | ✓ | ✓ | opt | ✓ |
| References — cited sources, internal mesh | `ReferencesMesh` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ (legal basis: المواد) | ✓ | ✓ |
| Official sources — outbound (BOE, ناجز…) | `OfficialSources` | ✓ | ✓ | ✓ | ✓ | ✓ | opt | – | ✓ |
| Read after («اقرأ أيضاً» — engagement, ≠ references) | `ReadAfter` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| اسأل ريحان (widget + inline trigger) | `AskRayhanWidget` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Open-in-Rayhan CTA (writer handoff) | `OpenInRayhanCta` | – | – | – | – | – | ✓ (main conversion) | – | – |

## Template specs

### `/regulations/{slug}` — document page
H1 official title + **status badge** (ساري/معدَّل/**ملغي** — repealed law must NEVER render as
current). Metadata card: الجهة المصدرة, رقم وتاريخ المرسوم, القطاع, آخر تعديل. AI summary
(`summary` col — the anti-duplicate-content layer vs BOE). **Full TOC free always** — every
فصل/مادة listed, each linking to its مادة page. First 2–3 articles inline → gate «سجّل مجاناً
لعرض النظام كاملاً». Mesh: judgments applying it, circulars, cross-refs (3 free → gated). FAQ
4–6 Q&As. Outbound `pdf_url`/`landing_url`. Gated PdfViewer.
JSON-LD: `Legislation`+paywall. Title: `{clean_title} — ملخصه ومواده | ريحان`.

### `/regulations/{slug}/{article-slug}` — مادة page ⭐ highest-value template
H1 «المادة {N} من {نظام}». Body per `resolve_gate`: open tier → **full text free** (public
domain + it's exactly what the searcher googled → engagement signals); gated tier → first lines
only. شرح: 2-line teaser → gated (**gate on value-add, not public-domain text**).
Cited-judgments list (teaser → gated). Prev/next مادة, parent link, CalculatorBlock where
relevant, «اسأل ريحان عن هذه المادة». Title: `المادة {N} من {نظام} — نصها وشرحها | ريحان`.

### `/judgments/{slug}` — judgment page
H1 = generated `seo_title` («حكم محكمة الاستئناف — تعويض عن فصل تعسفي — 1445هـ»). Metadata:
المحكمة، السنة، رقم القضية، نوع الدعوى، النتيجة. **المبدأ القضائي FREE** + **ملخص الوقائع
FREE** (what lawyers search + respect). Full text (الأسباب والمنطوق) **GATED** — the scarcest,
highest-value gate. Cited-articles mesh both directions (`cross_references_v2`): «استند الحكم
إلى: المادة 77 من نظام العمل» → مادة pages, and مادة pages list judgments back. Gated PDF.

### `/circulars/{slug}`
Metadata (الجهة، `source`) + summary free; body gated only above ~800 chars (a 4-line تعميم
90% gated looks silly). Role = mesh glue between regs and judgments.

### `/compliance/{slug}` — fully free HowTo (reads `services` table)
H1 service name (keep خدمة/متطلبات/خطوات keywords — URL says compliance, content says what
users search) + provider badge. intro → المتطلبات (`requirements[]`) → المستندات المطلوبة
(`required_documents[]`) → الخطوات numbered (`steps[]`). YouTube embed where `youtube_url`
(`HowTo`+`VideoObject` — double rich-result eligibility). CTA to official `service_url` +
free `pdf_link`. Title: `{service_name_ar} — الشروط والخطوات والمستندات | ريحان`.

### `/forms/{slug}` — نماذج page
H1 «صيغة/نموذج …» + metadata (الفئة، متى تستخدمه، الأساس النظامي). شرح-when-to-use **free**
(the SEO food) → **template body preview → gate** → gated download (docx/pdf via PDF proxy).
Legal-basis links into المواد. `OpenInRayhanCta` «افتح هذا النموذج في ريحان» = main conversion
(post-login intent `open_form_in_writer` → template lands in قوالبي writer). Disclaimer +
«راجع مختصاً» on every page. Title: `{title_ar} — نموذج جاهز {السنة} | ريحان`.

### `/calculators/{slug}`
H1 «حاسبة …» + inputs + instant client-side result + شرح of the formula + legal-basis links to
the exact مواد (bidirectional: those مادة pages embed this calculator) + FAQ + «اسأل ريحان عن
حالتك». Title: `حاسبة {X} {السنة} — احسبها مجاناً | ريحان`.

### Hubs (`/regulations`, `/judgments`, `/circulars`, `/compliance`, `/forms`, `/calculators`)
9 cards/page (3×3 RTL grid; 1-col mobile). Card = title + entity/provider badge + status badge
+ 2-line snippet + topic chips. Search + filters (جهة, topic, type; judgments add محكمة/سنة) +
featured strip. Path pagination `/regulations/page/2` (self-canonical, «— صفحة {N}» titles);
filters = query params with `noindex,follow`. Anon cap page ~3 → CTA wall (noindex, same for
Googlebot — no cloaking).

### `/topics/{slug}` — cross-type topic hub
One hub per topic aggregating: the نظام + top مواد + judgments + services + calculators + blog
posts. Sub-topics via parent. The internal-linking powerhouse targeting mid-tail queries.

## Backend surface (`backend/app/api/public_library.py` + `services/library_service.py`)

| Endpoint | Notes |
|---|---|
| `GET /public/library/{section}` | hub lists; filters; pagination; **anon depth cap here** |
| `GET /public/library/regulations/{slug}` | doc payload, tier-aware truncation |
| `GET /public/library/regulations/{slug}/articles/{article_slug}` | مادة payload |
| `GET /public/library/{judgments\|circulars\|compliance\|forms}/{slug}` | same shape; forms = approved only |
| `GET /public/library/pdf/{kind}/{id}` | PDF proxy; `X-Robots-Tag: noindex`; anon-gated → preview |
| `GET /public/library/sitemap/{section}` | paged URL+lastmod feed for sitemap routes |
| `POST /public/ask` | anon popup (SSE, Turnstile, limits, kill switch) |
| `POST /ask/claim` | authed claim of anon answer |

All anon endpoints: no `get_current_user` dep, Cache-Control headers, NO per-read counter
writes. Gated truncation decided ONLY by `resolve_gate()`.

---

# PHASES

## Phase 0 — SEO foundation (standalone; instantly lifts blog/landing too)

- **Sitemap index**: rewrite `frontend/app/sitemap.ts` as index + `app/sitemaps/[section]/route.ts`
  XML handlers (50k/file, real lastmod) fed by backend feed endpoint
  `GET /api/v1/public/library/sitemap/{section}`. First sections: `static`, `blog`.
- **Canonicals**: `alternates.canonical` on ALL public pages.
- **JSON-LD infra**: `components/seo/JsonLd.tsx` + `lib/seo/schema.ts` builders (Organization,
  WebSite, Article, BreadcrumbList, FAQPage, HowTo, VideoObject, Legislation, paywall fragment).
  Wire Organization+WebSite on root now.
- **Dynamic OG images**: `ImageResponse` route, param-driven, Arabic title on brand background
  (font = **Noto Naskh Arabic**, [[project_blog_linkedin_deck]]).
- **E-E-A-T**: «آخر تحديث» convention, methodology page, disclaimer links.
- **GSC**: register + verify (**user manual step**), submit sitemap index.

**Agents:** @nextjs-frontend + @fastapi-backend → @validate.
**Done:** GSC verified; sitemap accepted; rich-results test passes on landing + one blog
article; OG card renders Arabic correctly in WhatsApp preview.

## Phase 1 — Block system + gating engine + taxonomy (the shared machinery)

- **Blocks**: full component set (Locked decisions table) + `LibraryPageShell` (align with
  [[project_public_site_chrome]]).
- **Gating engine migrations** — ✅ APPLIED to prod 2026-07-22:
  `095_seo_gate_defaults_and_item_meta` (policy table + seed + `seo_item_meta` sidecar) and
  `096_topics_taxonomy` (`topics` + `topic_map`). Originally-drafted per-table `gate_override`
  columns + `097` slug columns were SUPERSEDED by the sidecar (corpus v2 "tables" are VIEWS
  over pipeline-owned schema `regulation_v2`; ALTER fails + re-ingest would clobber).
- **`resolve_gate()` + `truncate_for_gate()`** in `library_service.py` — the ONLY gating
  decision point. Hub depth-cap enforcement in list endpoints.
- **`scripts/set_gate.py`** + secret-protected on-demand revalidate route.
- **Topics**: `topics` + `topic_map` migrations, seed from `sectors[]`; `app/topics/[slug]/page.tsx`.
- **PDF proxy**: mirror bucket `library-pdfs` (+first-page preview thumbnails) +
  `GET /public/library/pdf/{kind}/{id}` (noindex header; anon-gated → preview only).
- **Routes registration**: PUBLIC_PREFIXES += `/regulations`, `/judgments`, `/circulars`,
  `/compliance`, `/forms`, `/topics`, `/calculators`.

**Agents:** @sql-migration → @fastapi-backend → @nextjs-frontend → @validate +
@security-reviewer (gating leaks, cap bypass, PDF proxy auth).
**Done:** anon curl on gated item = truncated payload + correct paywall JSON-LD; authed = full;
`set_gate.py` flip live in seconds; hub page 4 anon = CTA; PDF proxy anon-gated = preview only.

## Phase 2 — First content launch: /regulations docs + /compliance (both data-ready)

- **Data prep**: populate `seo_item_meta` rows (content_type='regulation'/'service') with
  Arabic slugs via `scripts/build_seo_slugs.py` + seed `seo_tier='open'` for the curated list
  (**user input**); topic seeding from `sectors[]` via script; topic_map rows.
  (Schema already live — migrations 095/096 applied.)
- **Doc page** `/regulations/{slug}`: H1 + status badge (ملغي NEVER renders as current —
  hard rule) · MetadataCard · AI summary (anti-duplicate-content vs BOE) · full TOC free
  (links to future مادة pages) · first 2–3 articles then gate · mesh (3 free) · FAQ ·
  OfficialSources (`pdf_url`) · gated PdfViewer. JSON-LD `Legislation`+paywall.
- **Compliance page** `/compliance/{slug}` (all free): provider badge · intro · المتطلبات ·
  المستندات · الخطوات · YouTube (`VideoObject`+`HowTo`) · CTA to `service_url` · mesh.
- **Hub/pagination template** (built once here, reused by every wing's hub):
  - Layout: **9 cards/page — 3×3 grid** desktop (RTL), 1-col mobile. Card = title + entity/
    provider badge + status badge (regs) + 2-line summary snippet + topic chips.
  - Above the grid: search box + filters (جهة, topic, doc type; judgments later add محكمة/سنة)
    + featured strip (`is_most_used` for compliance; curated for regs).
  - **Pagination URLs**: path-based crawlable segments `/regulations/page/2` (ISR-friendly,
    clean canonicals). Filters = query params ONLY, and filtered views get `noindex,follow`
    (or canonical → unfiltered base) — avoids faceted-navigation index bloat.
  - **SEO per page**: page 1 canonical = `/regulations`; deeper pages self-canonical with
    «— صفحة {N}» appended to the title.
  - **Anon depth cap**: pages 1–3 render normally; page 4+ for anon/free returns the CTA wall
    («تصفح المكتبة كاملة — سجّل مجاناً») — CTA pages carry `noindex` (don't index signup
    walls; deep directory pages have ~zero SEO value anyway — discovery is sitemap + mesh).
    Same response for Googlebot (no special-casing = no cloaking).
- **Sitemap waves**: `regulations` + `compliance` (is_most_used first).
- **Forms drafting starts in parallel** (see Phase 4 pipeline) so review queue fills early.

**Agents:** @sql-migration → @fastapi-backend → @nextjs-frontend → @validate.
**Done:** 3,373 + 4,717 pages live; GSC indexed sample; Lighthouse SEO ≥95; ملغي badge
verified; gated-tier truncation verified via anon curl.

## Phase 3 — Long-tail bomb: مادة pages (~38k) + شرح + forms + calculators

- **`seo_articles` index**: `scripts/build_seo_article_index.py` from `chunks_v2.owns`/
  `articles_v2` (render from `chunks_v2.content`, NEVER `chunk_titles_v2`); spot-QA random
  sample first.
- **Article page** `/regulations/{slug}/{article-slug}` ⭐: open tier = full text free / gated
  tier = first lines; شرح 2-line teaser then gated (**gate on value-add, not public-domain
  text**); cited-judgments teaser; prev/next; CalculatorBlock where relevant. Title: «المادة
  {N} من {نظام} — نصها وشرحها | ريحان».
- **`seo_sharh` cache**: pregenerate open-tier only (~5–8k, flash, ledger slot
  `sharh_generator`); on-demand+cache for long tail. `seo_faqs` batch for top regs.
- **Forms** `/forms/{slug}`: `forms` table (`review_status 'draft'|'approved'` — **only
  approved publishes, liability hard gate**; disclaimer + «راجع مختصاً» on every page);
  `scripts/draft_forms.py` (top ~100–200 by search volume, **user priority list**); page =
  metadata + when-to-use شرح free + body preview→gate + gated docx + legal-basis links;
  **`OpenInRayhanCta`** → post-login intent `open_form_in_writer` → template lands in قوالبي
  writer ([[project_writer_user_templates_plan]]).
- **Calculators**: `lib/calculators/registry.ts` + standalone `/calculators/{slug}` + embedded
  in مواد. First batch (**user validates formulas + worked examples = test suite**): مكافأة
  نهاية الخدمة (م84/85), مدة الإشعار (م75), أجر العمل الإضافي (م107), رسوم المحاكم. Free,
  never gated — link magnets.
- **Sitemap waves**: `articles` (open-tier regs first) + `forms` (approved only) + `calculators`.

**Agents:** @sql-migration → @fastapi-backend → @nextjs-frontend → @validate.
**Done:** «المادة 80 من نظام العمل» passes rich-results paywall test; spot-QA clean; forms
E2E (anon preview → signup → writer handoff); calculator results match user worked examples.

## Phase 4 — Conversion layer: اسأل ريحان popup (can be pulled earlier — independent)

- Floating pill (bottom-left RTL) on all wings + blog + inline triggers («اسأل ريحان عن هذه
  المادة») pre-seeded with page context.
- Anon flow: 1 question/session → **grounded ONLY in current page's chunks** (no deep_search),
  tier_2 flash, capped tokens, SSE → first ~2–3 lines visible, rest server-truncated (stored in
  `anon_questions`, RLS ON) → «سجّل مجاناً لعرض الإجابة كاملة» → post-login intent
  `claim_anon_answer` reveals stored answer (continuity moment). Authed → real conversation.
- Controls: session cap · IP rate limit · Turnstile on 2nd+ attempt (**user keys**) ·
  `ANON_ASK_ENABLED` kill switch + `ANON_ASK_DAILY_BUDGET` · ledger rows (anon attribution) +
  anon spend in daily /model-consumption.
- Endpoints: `POST /public/ask` (anon, SSE) + `POST /ask/claim` (authed).

**Agents:** @sql-migration → @fastapi-backend → @nextjs-frontend → @security-reviewer (abuse
surface) → @validate.
**Done:** anon ask → teaser → signup → claim E2E; 2nd question hits Turnstile; kill switch
verified; ledger rows present.

## Phase 5 — /judgments + /circulars (judgments gated on external ingest)

**Circulars: DONE** (1,843 pages, 100 published in the stage-1 sample).
**Judgments: BUILT 2026-07-24 as CONTENT-ONLY + NOINDEX** — see the sub-section below. The
PDPL audit was explicitly deferred by the user; the wing therefore ships complete but
un-indexable, and indexation is the ONLY thing the audit still gates.

- **Prereqs (order)**: bigger judgments corpus ingested (user pipeline) → **PDPL anonymization
  audit = HARD publish blocker** (batch flash → `pdpl_status`; only `passed` publishes;
  @security-reviewer samples; masking tech: [[project_pdpl_number_masking]]).
- **Circulars** `/circulars/{slug}`: slug migration; metadata + summary free; body gated only
  above ~800 chars; mesh glue to regs. 1,843 pages, cheap close-out.
- **Sitemap waves**: `judgments` (passed-audit only) → `circulars`.

### Judgments wing as actually built (2026-07-24) — corpus facts verified live

The plan above assumed judgments needed migrations and AI generation. The live corpus made
both unnecessary — recorded here because the assumption was wrong, not merely incomplete:

- **Corpus** = `public.cases`, **30,531 rows** (NOT `lawyer_cases`, the private user table).
  Already structured per judgment: `court`, `court_level` (first_instance 23,932 / appeal 6,474
  / supreme 125), `city`, `case_number`, `judgment_number`, `date_hijri`, `date_gregorian`,
  `legal_domains[]`, and eleven separate narrative columns (`facts`, `claims`,
  `plaintiff_grounds`, `defendant_response`, `defendant_grounds`, `reasoning`, `ruling`,
  `objection_grounds`, `appellee_response`, `appeal_reasoning`, `appeal_ruling`), plus
  `short_summary` (29,567 rows), `referenced_regulations` (jsonb) and `details_url`.
- **NO migration needed.** The `seo_item_meta` sidecar is content-type agnostic and migration
  095 already seeded the `('judgment','gated')` row in `seo_gate_defaults`. `cases` is
  pipeline-owned — never ALTERed, never written to.
- **NO AI generation needed for titles.** There is no title column, so title + slug are DERIVED
  deterministically in `shared/seo/judgment_naming.py` — the single source of truth imported by
  BOTH the publish script (permanent slug) and the read path (display title). The first line of
  `short_summary` is a one-sentence statement of the dispute and makes a genuinely good SEO
  title. Truncation cuts at an Arabic clause boundary («،»), never mid-thought.
  `case_ref` is UNIQUE across all 30,531 rows → slug tails are unique; over-long Arabic refs
  collapse to a deterministic blake2s digest.
- **Free/gated split** (replaces the plan's «principle + summary free», since the corpus has no
  principle field): FREE = `short_summary` lead + `facts` + `ruling` + `appeal_ruling`.
  GATED = the eight argumentation columns, above all `reasoning`. Rationale: the free layer says
  WHAT happened and WHAT was decided (ranks, and is genuinely useful); the gated layer is the
  legal ARGUMENTATION, which is what a lawyer signs up for.
- **Mesh**: `referenced_regulations[].regulation_id` → `regulations_v2.reg_ref` →
  `seo_item_meta` slug. 36,087 of 51,663 refs resolve to a regulation, 32,480 to a PUBLISHED
  page. The citation list is regulation NAME + article NUMBER only (no content), so it is
  ungated — gating the internal-linking mesh would defeat its purpose. One-line revert:
  `JUDGMENT_CITED_FREE_LIMIT = 3`.
- **NOINDEX (PDPL)**: `/judgments`, its paginated hub and `/judgments/{slug}` all emit
  `robots: {index:false, follow:false}`, and no judgments sitemap section is served. To publish
  after the audit passes: drop the `robots` block from the three `generateMetadata`s and
  register the sitemap section (both marked `TODO(pdpl)` in code). Un-indexing is not reliably
  reversible once Google has crawled — hence noindex first, audit second.
- **Stage-1 sample**: ~100 judgments published via `scripts/build_judgment_slugs.py`, spread
  across court level / court / domain (staying ≤300 keeps the hub in sample mode). Fully
  reversible with `--unpublish-all`.

**Agents:** ingest = user (+@pipeline-builder) → @sql-migration → @fastapi-backend →
@nextjs-frontend → @security-reviewer → @validate.
**Done:** audit coverage 100% + failures excluded; anon curl = principle+summary only; mesh
resolves both directions; short circular renders ungated.

## After Phase 5 — anti-scraping hardening

Moved to its own plan: **`.claude/plans/cloudflare_protection.md`** (needs account setup, DNS
migration, and manual dashboard processes — an ops track, not a build wave). Runs after the SEO
phases. Until it ships: free layer + sitemap enumeration are unprotected (backend IP rate limits
= interim baseline); the gated layer is scrape-proof from Phase 1 truncation regardless.
Canary text (from that plan) can be seeded early during Phase 3's شرح generation — it's free.

---

## Cross-cutting risks

- PDPL (judgments) + lawyer review (forms) = hard publish blockers.
- ملغي regulations must never render as current (status badge everywhere).
- Scaled-content policy: skip/backfill pages missing summaries; never raw-text-only pages.
- Naming map: `/compliance` ↔ `services` table ↔ internal `compliance_search`; `/forms` ↔
  private `/templates`; `/judgments` ↔ private `/cases`. Public API under
  `/api/v1/public/library/`.
- AI Overviews eat some clicks — snippet controls + brand repetition + popup conversion.

## Open items (user)

| Item | Needed for |
|---|---|
| GSC ownership verification | Phase 0 |
| Curated open-tier regulation list (~50–100) | Phase 2 |
| PDF mirroring OK? (re-hosting official PDFs) | Phase 1/2 |
| Forms priority list + review workflow (who approves) | Phase 3 |
| Calculator formulas + worked examples | Phase 3 |
| Turnstile keys | Phase 4 |
| Judgments ingest timeline + final size | Phase 5 |
| Cloudflare account/plan + AI-bot policy | separate plan: `cloudflare_protection.md` |
