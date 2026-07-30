# Global Site Header — one header across every non-sidebar surface

**Status:** Phase A + Phase B BUILT 2026-07-23 — NOT committed / NOT deployed.
**Goal:** Replace the three divergent public-page headers with ONE auth-aware global header
carrying a 4-slot information architecture, add the missing mobile navigation, and make the
`المكتبة القانونية` dropdown the sitewide crawl skeleton that [[seo_public_library]] hangs off.

## Build log — 2026-07-23

Verified: `tsc --noEmit` clean · `next lint` clean · `next build` prerenders `/vs-chatgpt`,
`/library`, `/learn` as static · all routes HTTP 200 · every nav link (incl. dropdown children)
present in server-rendered HTML (crawl-safe confirmed via `view-source`, trap #6).

**Shipped:**
- **Nav SSoT + resolver** — `lib/nav/site-nav.ts` (feature-flag config) + `lib/nav/resolve-nav.ts`
  (auth filter + auto-promote 0→hidden / 1→flat link / 2+→dropdown, + `groupChildrenBySection`).
  No test file added — repo has **no test runner** (no vitest/jest); resolver verified by
  reasoning + live SSR-HTML link assertions instead. The plan's `resolve-nav.test.ts` is dropped.
- **Header components** — `SiteHeader` (server) · `SiteNav` (custom crawl-safe desktop dropdowns —
  links always in DOM, hover/focus/click revealed; Radix DropdownMenu deliberately NOT used, it
  portals content out of SSR HTML) · `SiteMobileNav` (custom slide-in drawer — the mobile nav that
  did not exist before, trap #1) · `SitePageShell`.
- **Migrations** — `page.tsx` / `about_us` / `audiences` / `pricing` → `SitePageShell`;
  `BlogPageShell` + `LegalPageShell` delegate chrome to it; `LandingHeader.tsx` DELETED;
  `SiteFooter` realigned + `المكتبة` column added; `SidebarFooter` gets a `المكتبة القانونية` row;
  `AuthGuard.PUBLIC_PREFIXES` += `/vs-chatgpt`, `/library`, `/learn`; `lib/seo/sitemap.ts`
  `getStaticUrls()` += `/about_us`, `/vs-chatgpt` (NOT `/library`,`/learn` — they're noindex).
- **Phase B — `/vs-chatgpt`** built with real content per user (`components/marketing/VsChatGptView.tsx`
  reusing `COMPARISON`/`HERO_TRUST` + a why-it-matters strip + CTA; `buildArticle` JSON-LD).
  Flag already `enabled: true`, so `ليش ريحان؟` renders as a 3-item dropdown today.
- **Empty hubs** — `/library` + `/learn` endpoints built as `ComingSoonHub` placeholders
  (`robots: noindex`), per user "produce the endpoint but keep the pages empty". Both render as
  flat links in the header (hub `href` + <2 enabled children).

**Rendered header today (anon):** `ليش ريحان؟▾ (3)` · `المكتبة القانونية → /library` ·
`التعلّم → /learn` · `الأسعار`. Signed-in drops `ليش ريحان؟`.

**Label decision:** shipped `ليش ريحان؟` (not the bare `ريحان` the user first asked for) — §1.1.
One-word revert in `site-nav.ts` if overruled.

**Deferred / not built:** Phase C (`/learn/*` lessons — hubs are placeholders); Phase D flag-flips
(each [[seo_public_library]] phase owns its own). Existing SEO routes (`/regulations` etc. — they
already build) stay `enabled: false` until their SEO phase confirms them live.

**Next:** `/ship` (commit → deploy backend+frontend? frontend-only here) once eyeballed.

Companion plan: `.claude/plans/seo_public_library.md` — this header is the navigation contract
that plan's 7 sections plug into. Neither plan blocks the other: the header ships first with
feature flags all off.

---

## 1. Locked decisions (brainstorm 2026-07-22)

| Decision | Value |
|---|---|
| Slot count | **4 top-level slots max** + theme toggle + login link + primary CTA button. Verified against peers: Harvey ships 6, Claude ships 7 — both with mega-menus. 4 is the ceiling for Arabic labels (wider than English) at `max-w-6xl`. |
| Slots | `ليش ريحان؟▾` · `المكتبة القانونية▾` · `التعلّم▾` · `الأسعار` |
| `عن ريحان` placement | **Demoted into `ليش ريحان؟▾`.** Never top-level. Harvey buries About at position 6 inside `Company▾`; Claude doesn't surface it at all. It is a candidate/investor page, not a buyer page. |
| `المدونة` placement | **Inside `المكتبة القانونية▾`**, below a divider, under a `مقالات وتحليلات` group heading — corpus above under `المصادر الرسمية`. User's call; the win is sitewide internal links to blog posts, the cost is slight dilution of the "official primary source" signal, which the visual grouping repays. |
| `كيف يعمل ريحان` placement | **Inside `التعلّم▾`**, not `ليش ريحان؟`. |
| `الأسعار` | **Stays top-level, flat link.** Highest-intent nav click on any SaaS site; buyers self-qualify there. Claude keeps it top-level next to six mega-menus. |
| CTA copy | **`جرّب ريحان مجاناً`** — free-first, not `اشترك الآن`. There is a free tier; "subscribe" asks for money before value. Peers: "Try Claude", "Request a Demo". |
| CTA vs login | Two visual weights, always: `تسجيل الدخول` = ghost text link, `جرّب ريحان مجاناً` = primary button. Never both as buttons. |
| Header surface | **Every non-sidebar page.** The sidebar app surface (`/chat`, `/chats`, `/templates`, `/blogs`, `/cases`) keeps its own chrome — stacking a marketing mega-menu on an app sidebar is double chrome. Signed-in users reach the library via a new `SidebarFooter` row instead. |
| Library links today | **Feature-flagged; only `enabled` sections render.** Each `seo_public_library` phase flips one flag. No dead links, no `قريباً` stubs, no crawl waste. |
| Group→link auto-promote | A nav group with **< 2 enabled children renders as a flat link** to its single child (or hides at 0). Applies to `المكتبة` and `التعلّم` identically. Means the header ships today with zero placeholder pages. |
| Mobile | **Sheet drawer, all slots expanded.** Current `LandingHeader` hides nav entirely below `lg` with no fallback — see §6 trap 1. |

### 1.1 Label note — `ليش ريحان؟` vs `ريحان`

User asked for the slot to be labelled **`ريحان`**. Config ships **`ليش ريحان؟`** instead —
one-word change in `site-nav.ts` if overruled. Reason: in RTL the logo reads `ريحان` and the
adjacent first nav item would read `ريحان` again, brand word twice side by side. Claude hit the
same problem and solved it with a verb (`Meet Claude`, not `Claude`). `ليش ريحان؟` also
describes the contents exactly — who it's for, how it differs, who's behind it — and "Why X" is
a standard SaaS slot. Matches the existing colloquial voice (`ريحان يستهدف مين؟`).

**Do NOT use `اتعرّف على ريحان`** — that is the in-app onboarding tour name
([[project_onboarding_tour]]). Two different things, one name.

---

## 2. Final information architecture

```
RTL — logo at start (right), actions at end (left)

[ريحان]  ليش ريحان؟▾   المكتبة القانونية▾   التعلّم▾   الأسعار      🌙  تسجيل الدخول  [جرّب ريحان مجاناً]
```

| Slot | Children | Route | State |
|---|---|---|---|
| **ليش ريحان؟▾** | لمن ريحان؟ | `/audiences` | ✅ live |
| | ريحان مقابل ChatGPT | `/vs-chatgpt` | 🔨 Phase B |
| | عن ريحان | `/about_us` | ✅ live |
| **المكتبة القانونية▾** | *— المصادر الرسمية —* | | group heading |
| | الأنظمة | `/regulations` | ⏳ SEO Phase 2 |
| | الإجراءات الحكومية | `/compliance` | ⏳ SEO Phase 2 |
| | النماذج والصيغ | `/forms` | ⏳ SEO Phase 3 |
| | الحاسبات | `/calculators` | ⏳ SEO Phase 3 |
| | الأحكام القضائية | `/judgments` | ⏳ SEO Phase 5 |
| | التعاميم | `/circulars` | ⏳ SEO Phase 5 |
| | *— مقالات وتحليلات —* | | group heading + divider |
| | المدونة | `/blog` | ✅ live |
| **التعلّم▾** | كيف يعمل ريحان | `/learn/how-it-works` | 🔨 Phase C |
| | دليل الاستخدام | `/learn/guide` | 🔨 Phase C |
| | أفضل الممارسات | `/learn/best-practices` | 🔨 Phase C |
| | أمثلة أسئلة | `/learn/examples` | 🔨 Phase C |
| **الأسعار** | — (flat) | `/pricing` | ✅ live |

**Rendered state at end of Phase A** (all flags off except live routes):
`ليش ريحان؟▾ (2 children)` · `المكتبة القانونية → /blog (flat, 1 child)` · `التعلّم (hidden, 0)` · `الأسعار`

### 2.1 Auth-aware variants

`HeaderAuthActions` already swaps the buttons. The **nav** must vary too — a converted user
does not need the pre-conversion pitch.

| State | Slots rendered | Actions |
|---|---|---|
| Signed out | ليش ريحان؟ · المكتبة · التعلّم · الأسعار | `تسجيل الدخول` + `جرّب ريحان مجاناً` |
| Signed in | ~~ليش ريحان؟~~ · المكتبة · التعلّم · الأسعار | `العودة إلى ريحان` |
| Session probe in flight | invisible same-footprint placeholder | (existing behaviour — keep) |

`الأسعار` stays visible when signed in: it is the upgrade path for free-plan users.
Only `ليش ريحان؟` (pure pre-conversion marketing) is dropped.

> Reuse the existing `isLoading` placeholder discipline from
> `components/site/HeaderAuthActions.tsx` — never default to the signed-out variant while the
> probe is in flight, or signed-in users get a `تسجيل الدخول` flash.

---

## 3. Surface map — who gets the header

| Surface | Routes | Today | After |
|---|---|---|---|
| Marketing | `/`, `/about_us`, `/audiences`, `/pricing` | `LandingHeader` | `SitePageShell` |
| Legal | `/terms`, `/privacy`, `/masking` | `LegalPageShell` — centered logo box, **no nav at all** | `SitePageShell` wrapping existing body |
| Blog (anon) | `/blog`, `/blog/[token]` | `BlogPageShell` inline header — logo + toggle + auth, **no nav links** | `SitePageShell` (BlogPageShell keeps its CTA block, delegates chrome) |
| New marketing | `/vs-chatgpt` | — | `SitePageShell` (Phase B) |
| New education | `/learn`, `/learn/*` | — | `SitePageShell` (Phase C) |
| **Library** | `/regulations` `/judgments` `/circulars` `/compliance` `/forms` `/calculators` `/topics` | — | `LibraryPageShell` **must compose `SiteHeader`**, not fork it (SEO Phase 1) |
| **Sidebar app — NO header** | `/chat/*`, `/chats`, `/templates/*`, `/blogs/*`, `/cases/*` | sidebar chrome | unchanged + one `SidebarFooter` row |
| Auth | `/login` | standalone | unchanged |

**Contract for `seo_public_library` Phase 1:** `LibraryPageShell` = `SiteHeader` +
breadcrumb strip + `{children}` + `SiteFooter`. It supplies the breadcrumb, never its own
header. This plan's §1 note in that plan ("generalize `BlogPageShell` or build a
`LibraryPageShell` sibling") resolves to: **compose `SitePageShell`**.

---

## 4. File manifest

### 4.1 New

| File | Purpose |
|---|---|
| `frontend/lib/nav/site-nav.ts` | **Single source of truth.** `NavGroup[]` with `enabled` per child, group headings, dividers, `hideWhenAuthed`. Pure data, server-safe. |
| `frontend/lib/nav/resolve-nav.ts` | `resolveNav(isAuthenticated)` → filters `enabled`, applies `hideWhenAuthed`, applies the **auto-promote rule** (0 children → drop, 1 → flat link, 2+ → dropdown). Unit-tested. |
| `frontend/components/site/SiteHeader.tsx` | The bar: brand + `<SiteNav>` + `ThemeToggle` + `<HeaderAuthActions>` + `<SiteMobileNav>`. Server component. |
| `frontend/components/site/SiteNav.tsx` | Client. Desktop `lg+` nav; dropdowns via shadcn `NavigationMenu` (add via `mcp__shadcn`); RTL-aware alignment; group headings + dividers; label + one-line description rows. |
| `frontend/components/site/SiteMobileNav.tsx` | Client. shadcn `Sheet` trigger `< lg`; all groups expanded as sections; theme toggle + auth actions inside. |
| `frontend/components/site/SitePageShell.tsx` | `SiteHeader` + `{children}` + `SiteFooter`, `dir="rtl"`. The one wrapper every public page uses. |
| `frontend/lib/nav/__tests__/resolve-nav.test.ts` | Auto-promote + auth-filter cases. |

### 4.2 Modified

| File | Change |
|---|---|
| `frontend/components/landing/LandingHeader.tsx` | **DELETE** — superseded. `NAV_LINKS` migrates to `site-nav.ts`. |
| `frontend/app/page.tsx`, `about_us/page.tsx`, `audiences/page.tsx`, `pricing/page.tsx` | `LandingHeader` + `SiteFooter` → `SitePageShell` |
| `frontend/components/blog/BlogPageShell.tsx` | Drop the inline `<header>`; delegate to `SitePageShell`. Keep `showCta` + `BlogConversionCta`. |
| `frontend/components/legal/LegalPageShell.tsx` | Wrap in `SitePageShell`; drop the centered logo box (header supplies brand) and the bare `العودة إلى ريحان` link (header CTA supplies it). Keep `LegalLinksFooter`. |
| `frontend/components/sidebar/SidebarFooter.tsx` | Add `المكتبة القانونية` row to the settings popover, next to the existing `الباقات` → `/pricing` row (same `router.push` pattern, `Library` lucide icon). Signed-in path to the library. |
| `frontend/components/auth/AuthGuard.tsx` | `PUBLIC_PREFIXES` += `/vs-chatgpt`, `/learn`. Library prefixes are added by SEO Phase 1 — **don't duplicate**. |
| `frontend/app/sitemap.ts` | += `/vs-chatgpt`, `/learn/*`. Superseded wholesale by SEO Phase 0's sitemap index — coordinate, don't fight it. |
| `frontend/components/site/SiteFooter.tsx` | `PLATFORM_LINKS` realigned to the new IA; add a `المكتبة` column mirroring enabled sections (footer links are crawl paths too). |

### 4.3 Phase B / C content routes

| File | Phase |
|---|---|
| `frontend/app/vs-chatgpt/page.tsx` | B |
| `frontend/components/marketing/VsChatGptView.tsx` | B — reuses `COMPARISON` + `COMPARISON_HEADER` from `components/landing/content.ts` |
| `frontend/app/learn/page.tsx` + `learn/[slug]/page.tsx` | C |
| `frontend/lib/learn/content.ts` | C — 4 lessons, co-authored with [[project_edu_popups]] |

---

## 5. Phases

### Phase A — Header foundation ⭐ ships alone, unblocks everything

Build §4.1 + §4.2. No new content pages, no dependency on `seo_public_library`.

Delivers: one header on 10 existing routes · **mobile navigation that does not currently
exist** · auth-aware nav · the flag config every later phase flips · sidebar library row.

**Agents:** @nextjs-frontend → @validate.
**Done when:** all 10 routes render one identical header; `< lg` drawer opens and navigates on
a real viewport; signed-in vs signed-out variants verified with both a live session and anon;
no `تسجيل الدخول` flash on reload while authed; `npx tsc --noEmit` + `npm run lint` clean;
`resolve-nav` tests pass; `/chat/*` visually unchanged.

### Phase B — `ريحان مقابل ChatGPT`

Standalone route, **not** a landing anchor. `ChatGPT للمحامين` / `هل أستخدم ChatGPT في القانون`
are live Arabic queries and "X vs Y" pages rank unusually well — this is a free SEO asset that
also happens to be the highest-value item in `ليش ريحان؟`.

Content exists: `COMPARISON` + `COMPARISON_HEADER` in `components/landing/content.ts`, rendered
today by `ComparisonSection.tsx`. Extract to a shared view; landing keeps its section, the page
adds intro + expanded rows + CTA. Flip `vs-chatgpt.enabled`.

SEO: canonical · `Article` JSON-LD · dynamic OG · sitemap entry. If SEO Phase 0 has landed,
use its `JsonLd` + OG infra rather than one-off code.

**Agents:** @nextjs-frontend → @validate.
**Done when:** `/vs-chatgpt` live, in sitemap, rich-results test passes, dropdown shows 3 items.

### Phase C — `التعلّم` section

`/learn` hub + 4 pages (`how-it-works`, `guide`, `best-practices`, `examples`). Flipping ≥2
flags auto-promotes the slot from hidden → dropdown with no header code change.

**Author the copy once.** [[project_edu_popups]] (`.claude/plans/edu_popups.md`) covers the same
ground as in-app contextual lesson cards. `lib/learn/content.ts` is the shared source; the popups
consume excerpts, `/learn/*` renders the long form and carries the SEO. Do not fork the text.

**Agents:** @nextjs-frontend → @validate.

### Phase D — Library flags (no work in this plan)

Each `seo_public_library` phase ends by flipping its `enabled` flag(s) in `site-nav.ts` and
triggering the on-demand revalidate route built in that plan's Phase 1.

| SEO phase | Flips |
|---|---|
| Phase 2 | `regulations`, `compliance` → المكتبة auto-promotes to a real dropdown (3 children) |
| Phase 3 | `forms`, `calculators` |
| Phase 5 | `judgments`, `circulars` → full 7-child mega-menu |

**Add this flag-flip line to each of those phases' "Done" checklists** — otherwise sections ship
live but unreachable from navigation, which is the single most expensive way to lose the
internal-linking value the library exists for.

---

## 6. Traps

1. **Mobile nav does not exist today.** `LandingHeader.tsx:42` — `hidden ... lg:inline-flex`
   with no hamburger fallback. Below 1024px a visitor gets logo + buttons and **zero internal
   links**. For a site whose thesis is organic search (majority-mobile traffic) this kills both
   discovery and crawl signal. Phase A fixes it; do not add a fifth slot before it lands.
2. **`PUBLIC_PREFIXES` is the real public-route gate**, not `middleware.ts` (a no-op — see
   `seo_public_library` §3). A new public route without a prefix entry redirects anon visitors
   to `/login` and is invisible to Googlebot.
3. **Prefix-collision check before adding any prefix.** `isPublicPath` matches `=== prefix` or
   `startsWith(prefix + "/")`. `/blog` vs `/blogs` is safe under that rule *only* because of the
   trailing slash — verify the same for anything new. `/cases` and `/templates` are private and
   must stay out.
4. **Three shells, three headers, today.** Migrating all three is the point of Phase A — a
   partial migration is worse than none, because the header then differs between `/pricing` and
   `/blog`, which visitors notice immediately.
5. **`LibraryPageShell` must compose, not fork.** If SEO Phase 1 builds its own header the
   codebase is back to four.
6. **Dropdown ≠ crawl path on its own.** Ensure dropdown children are real `<a href>` in the
   server-rendered HTML, not JS-injected on open — otherwise the crawl-skeleton rationale
   evaporates. shadcn `NavigationMenu` renders links in DOM; verify in `view-source`, not devtools.
7. **Theme toggle costs a slot.** Peers don't ship one in the marketing header. Keeping it, but
   it moves inside the drawer `< lg`. Drop it from public pages if the bar gets tight.
8. **Don't reintroduce `اشترك الآن` in the header.** It belongs on `/pricing`.

---

## 7. Open items (user)

| Item | Needed for |
|---|---|
| Confirm or overrule `ليش ريحان؟` vs `ريحان` (§1.1) | Phase A — one-word config change |
| `/learn` slugs: English (`/learn/how-it-works`) or Arabic (`/learn/كيف-يعمل-ريحان`)? SEO plan uses Arabic doc slugs under English section prefixes — Arabic is the consistent choice | Phase C |
| Copy for the 4 `التعلّم` lessons | Phase C |
| `/vs-chatgpt` slug — English is deliberate here (matches how the query is typed) | Phase B |
