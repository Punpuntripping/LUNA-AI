# Public blog wing — mobile parity with the library reading surfaces

Bring `/blog/{slug}` up to what `/regulations/{slug}` already does on a phone:
the «اسأل ريحان» FAB, a working «تحدّث مع ريحان» carry, a TOC pill that names the
section the reader is actually in, breadcrumbs, and «اقرأ تاليًا».

Measured on a 390×844 viewport against production, 2026-09-21.

---

## What the two wings actually render today

| Affordance | `/regulations/{slug}` | `/blog/{slug}` |
|---|---|---|
| Inline collapsed TOC | ✅ `TocList` | ✅ `TocList` |
| Phone TOC pill + sheet | ✅ `TocFloating` | ✅ but **names the wrong section** |
| Desktop sticky rail | ✅ `TocRail` | ✅ (same wrong-section bug) |
| «اسأل ريحان» FAB | ✅ `AskRayhanWidget` | ❌ **absent** |
| Carry into a chat | ✅ `ChatWithPageCta` | ❌ **dead button** |
| Breadcrumbs + `BreadcrumbList` | ✅ `TopicBreadcrumbs` | ❌ absent |
| «اقرأ تاليًا» | ✅ `RelatedStrip` | ❌ absent |

Every other reading wing (regulations, مادة, judgments, circulars, compliance,
forms, calculators) mounts `AskRayhanWidget`. The blog is the only one that does
not — and it is the wing anonymous readers land on from Google.

---

## The three defects, with causes

### D1 — «اتحدث مع المدونة» renders nothing in production

`ChatWithBlogButton` reads `useParams<{ token?: string }>()` and returns `null`
when there is no `token`. The route was renamed `[token]` → `[slug]` when it
became the three-vocabulary dispatcher, so `params.token` is `undefined` on
**every** `/blog/*` URL. Both callers are affected:

* `BlogArticleView` → `/blog/{slug}` (public wing) and `/blog/{token}` (legacy
  title-mode)
* `PublicAnswerView` → `/blog/{token}` (legacy question-mode)

Verified live: the article's only action is «نسخ المقال». The button survives
only at `/blogs/{token}`, the مدوناتي owner view, whose segment is still `token`.

### D2 — the TOC names the wrong section on a blog

`useTocScrollspy` sets `activeId` from the **first intersecting** target. The
library wings observe `<section id="sec-…">` elements that SPAN their content,
so at any scroll position something intersects and "first" is a true answer.

Blog anchors are bare `slugifyHeading` ids on the `<h2>` itself — 53px tall.
With `rootMargin: "-72px 0px -60% 0px"` the band is ~265px; most of the time no
heading is inside it, `inView` is empty, and `activeId` keeps whatever it last
saw. Observed on the labour-claim article: the pill read «الخلاصة» (heading 5 of
5) while the viewport was between headings 1 and 2.

Fix in the hook, not the wing: when nothing intersects, fall back to the last
target whose top is above the band. That is a no-op for the section-based wings
(something always intersects there) and makes a heading-anchored page correct.

### D3 — the public wing is invisible to every blog-keyed backend path

`ask_service._ground_blog` queries `blog_posts` **by token**. `_title_blog` does
the same. The public wing is `public_blogs` **by slug**. So a `blog` page_type
carrying a slug grounds on `""` today.

⚠ **This is the load-bearing item.** Mounting `AskRayhanWidget` without it ships
a widget that answers about the page from no page text at all, and a carry that
puts an empty note in the workspace. Same failure shape as the router
title-and-summary gap: the surface looks wired, the content never arrives.

`library_item_service` imports `fetch_grounding` from `ask_service`, so **one**
dual-vocabulary `_ground_blog` fixes the widget and the carry together.

---

## Wave A — backend: address the public wing by slug

**A1. `public_blog_service.get_body_by_slug(supabase, slug)`** *(new)*
Title + body via the existing `_fetch_current_row` predicate. NOT `get_by_slug`:
that one bumps `view_count`, and neither grounding an answer nor titling a
workspace item is a page view — the counter drives the hub ranking, and the
`blog_lastmod` incident is what a silently-inflated counter costs.

**A2. `ask_service._ground_blog`** — dual vocabulary, shape-dispatched.
32-hex → `blog_posts.token` (the 99 live share links, unchanged). Anything else
→ `get_body_by_slug`. Fall through rather than branch exclusively, matching the
frontend's `resolveBlogRef`.

**A3. `library_item_service._title_blog`** — same dispatch. Its docstring's
claim that the chain matches the rendered `<h1>` 100/100 is about `blog_posts`;
the public wing's `<h1>` is `public_blogs.title`, so the same rule holds.

**A4. `_public_path` for blog** — percent-encode the segment. An Arabic slug in
a recorded metadata URL must be a real address, and `/blog/{token}` was ASCII so
nothing encoded it before.

**A5. `PageIdentity` blog key** — `blog:{page_id.casefold()}` already works for
an Arabic slug; the comment claiming "hex, hence casefold-safe" needs to say why
it still holds.

## Wave B — backend: «اقرأ تاليًا» by cited نظام

The relatedness rule is **the regulations the two articles both cite**, not the
editorial subject chip. `public_blogs.type` is `judicial_research` on 23 of 25
live rows, so it discriminates nothing; the subject chips are broad. The frozen
`references_json` already carries, per entry, `domain`, `doc_type`,
`regulation_title` and `library_url` — «نظام العمل» is literally in the row.

**B1. Topic key.** Only `domain == "regulations"` entries with a non-empty
`doc_type` count. ⚠ `regulation_title` on a `cases` entry holds a COURT name
(«وزارة العدل», «ديوان المظالم») — keying on it unfiltered would relate every
judicial article to every other one through the court that issued the judgments.

Key = `library_url` when present, else a folded `regulation_title`. `library_url`
is null on roughly a third of the entries for regulations that DO have a page, so
neither field alone is sufficient; the fold (NFKC, diacritics, alef/ya/ta-marbuta)
is what keeps «نظام الإثبات» and a bare-alef spelling one topic.

**B2. `public_blog_service.list_related_by_topic(supabase, slug, limit)`**
Bounded scan of `root_id, references_json` over qualifying rows (the
`_JOIN_SCAN_CAP` pattern this wing already uses for the subject join), score,
then one `_CARD_FIELDS` fetch for the winners only — so the heavy projection
never fans out.

Score: shared-topic count first; ties broken by a rarity weight (`1/√df`) so a
shared «ضوابط التمويل الاستهلاكي» outranks a shared «نظام العمل», then newest
first. Zero shared topics ⇒ the article is omitted entirely, never padded with
recency — a strip that pads is a strip a reader learns to ignore.

**B3. `GET /public/blogs/{slug}/related`** + `PublicBlogRelatedResponse`.
No route-order hazard: the extra segment cannot collide with `/{slug}` or with
the literal `/subjects`.

## Wave C — frontend

**C1. `AskRayhanWidget` on `BlogArticleView`** — `pageType="blog"`,
`pageId={sourceKey}` (already slug-on-public / token-on-legacy by construction),
`pageTitle={title}`.

**C2. Retire `ChatWithBlogButton` from both reading surfaces** in favour of
`ChatWithPageCta` (`pageType="blog"`). This fixes D1 by removing the route-param
coupling that caused it, and routes the carry through `library-items` +
`fetch_grounding` — the path Wave A repairs — instead of the token-only
`createBlogItem` import endpoint, which cannot serve a slug.

**C3. Scrollspy fallback** in `use-toc-scrollspy` (D2).

**C4. Breadcrumbs** — «الرئيسية / المدونة / {title}» via `TopicBreadcrumbs`, plus
`BreadcrumbList` JSON-LD on the route.

**C5. «اقرأ تاليًا» strip** — `RelatedStrip` + a blog card, full page width below
the references, above the conversion CTA, matching the library wings.

---

## Collision check (phone)

`TocFloating`'s pill is `start-4` (RTL right); `AskRayhanWidget`'s FAB is
`left-5` (physical left). Both `z-40`, opposite corners, both offset from
`env(safe-area-inset-bottom)`. `SiteMobileNav` portals above at `z-50`.
`AnonCtaPopup` already coexists with the FAB on every library wing.

## Success criteria

1. `/blog/{slug}` on a phone shows the «اسأل ريحان» FAB, and a submitted
   question comes back grounded in THAT article's text (verify the teaser is
   about the article, not generic).
2. «تحدّث مع ريحان عن هذه المدونة» carries a workspace item whose body is the
   article, not a bare title frame.
3. The TOC pill names the section under the header at any scroll position.
4. «اقرأ تاليًا» on a نظام العمل article lists other نظام العمل articles, and is
   absent (not padded) when nothing shares a topic.
5. The 99 legacy `/blog/{32-hex}` links render and behave exactly as before.
