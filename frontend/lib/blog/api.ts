// Typed SERVER-SIDE fetchers for the public blog wing (`/api/v1/public/blogs/*`)
// plus the legacy `blog_posts` snapshot reader (`/api/v1/public/blog/{token}`).
// `.claude/plans/blog_subjects.md` §3.
//
// These run ONLY in server components: plain `fetch`, no auth header, never the
// token-aware `apiFetch` client. Every fetcher returns `null` / `[]` on a
// non-OK or unreachable backend so the caller can `notFound()` (documents) or
// render an empty state (feeds) — Google must never see a 5xx from a public
// page, and `npm run build` must survive an offline backend.
//
// SERVER→SERVER: the origin is `SERVER_API_BASE` (`INTERNAL_API_URL` →
// `NEXT_PUBLIC_API_URL` → localhost), so once the Railway private network is
// wired these calls leave the edge entirely — and therefore no longer pick up
// Cloudflare's `X-Edge-Secret`. `serverFetchInit` re-attaches it from the
// server-only `EDGE_SECRET`. Without that the origin lock would 403 every blog
// URL and every fetcher would return null, i.e. a 404 served on a live page.
//
// ⚠ THE WING IS ISR SINCE 2026-09-17, NOT `force-dynamic`. The routes no longer
// declare `dynamic`; each fetcher below picks its own `next.revalidate` window
// and Next caches the rendered route per path accordingly. `serverFetchInit`
// therefore contributes BOTH its headers and its window — read the caching note
// on `fetchPublic` before changing either. With `EDGE_SECRET` unset the header
// value is `undefined`, which `fetch` treats exactly as an absent key, so the
// request on the wire is unchanged.
//
// ⚠ SLUGS ARE ENCODED EXACTLY ONCE, HERE — and only for the wire. What arrives
// is the DECODED ref from `normalizeBlogRef` (`lib/blog/slug.ts`), so this
// `encodeURIComponent` is the single encode of the round trip. Starlette
// decodes the path param once on the way in, which is why the backend handler's
// docstring says «do not decode again».

import { cache } from "react";

import { SERVER_API_BASE, serverFetchInit } from "@/lib/library/api";
import type {
  BlogPostPublic,
  BlogSubject,
  BlogSubjectFeedResponse,
  BlogSubjectsResponse,
  PublicBlogCard,
  PublicBlogDetail,
  PublicBlogListResponse,
} from "@/types";

// ISR windows for the routes that cache. Deliberately SHORTER than the
// library's (`HUB_REVALIDATE` 1h / `DOC_REVALIDATE` 24h) because the blog is
// small, actively curated, and has two state flips the library does not: a
// `pending` article becoming `approved` (404 -> 200) and a retraction
// (`is_public=false`, which is what makes the page `noindex`). Both are
// invisible until the window expires, so the window IS the worst-case latency
// on an editorial decision. One hour buys ~99% of the crawl-efficiency win at a
// fraction of that risk.
//
// ⚠ ONLY `/blog/[slug]` CACHES. `/blog` and `/blog/subjects` stay
// `force-dynamic` (static routes would prerender at build and can bake empty —
// see the note in `app/blog/page.tsx`), so their fetchers pass `0`. A cached
// fetch under a `force-dynamic` route is the worst of both: the route re-renders
// per request and still serves data up to a window old.
const BLOG_DOC_REVALIDATE = 3600; // 1 hour — one article, ISR
const BLOG_SUBJECT_REVALIDATE = 900; // 15 min — a subject listing, ISR

/**
 * One anon GET against the backend, parsed as `T`. `null` on anything else.
 *
 * `revalidate` is the Next Data Cache window in seconds; pass `0` for
 * `no-store` (uncached, one round trip per render).
 *
 * ⚠ THIS IS WHAT MAKES THE ROUTE STATIC OR DYNAMIC. The route files declare no
 * `dynamic` export, so Next infers the mode from the fetches a render actually
 * performs: a cached fetch lets the rendered HTML into the Full Route Cache, a
 * `no-store` fetch opts that render out. The `/blog/[slug]` dispatcher relies
 * on exactly this — an Arabic slug renders through the cached `getPublicBlog`
 * and is cached, while a 32-hex legacy token renders through the uncached
 * `getLegacyBlogPost` and is not. One route, two caching behaviours, chosen per
 * request by which vocabulary the ref belongs to.
 */
async function fetchPublic<T>(
  path: string,
  revalidate: number,
): Promise<T | null> {
  try {
    const base = serverFetchInit(revalidate);
    const res = await fetch(`${SERVER_API_BASE}/api/v1${path}`, {
      ...(revalidate > 0 ? { next: base.next } : { cache: "no-store" }),
      headers: base.headers,
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    // Backend unreachable / network error: treat as missing rather than
    // crashing the render. Document callers turn null into notFound().
    return null;
  }
}

/**
 * The gallery feed — current, public, published, newest first.
 *
 * `cache()` is React's per-request memo: `generateMetadata` and the page body
 * both call these fetchers, and without it every blog URL would cost two
 * identical backend round trips.
 */
export const getPublicBlogGallery = cache(
  async (limit = 50): Promise<PublicBlogCard[]> => {
    // `0`: its only caller, `/blog`, is `force-dynamic`, and the gallery is the
    // sole crawl path into the articles — a window here would delay a newly
    // approved article's discovery for no cache benefit.
    const data = await fetchPublic<PublicBlogListResponse>(
      `/public/blogs?limit=${limit}`,
      0,
    );
    return data?.blogs ?? [];
  },
);

/**
 * The whole ACTIVE browse vocabulary with honest counts — including subjects
 * sitting at zero. The `>= 1` filter is the caller's (plan D13 + §7): a curator
 * looking at `/blog/subjects` should not be the last to learn a subject is
 * empty, but an empty subject must never reach the grid or the sitemap.
 */
export const getBlogSubjects = cache(async (): Promise<BlogSubject[]> => {
  // `0`: both callers (`/blog` hub tiles, `/blog/subjects` index) are
  // `force-dynamic`, and these counts gate which subjects reach the grid AND
  // the sitemap — a stale zero would hide a subject that just gained its first
  // article.
  const data = await fetchPublic<BlogSubjectsResponse>(
    `/public/blogs/subjects`,
    0,
  );
  return data?.subjects ?? [];
});

/**
 * One subject and its blogs, newest first. `null` = unknown OR inactive
 * subject — indistinguishable on purpose: retiring a subject is
 * `is_active=false`, never a delete, and it must take the page down the same
 * way a typo does.
 */
export const getBlogSubjectFeed = cache(
  async (slug: string, limit = 50): Promise<BlogSubjectFeedResponse | null> =>
    // Cached: this one is served by `/blog/[slug]`, the ISR dynamic segment.
    fetchPublic<BlogSubjectFeedResponse>(
      `/public/blogs/subjects/${encodeURIComponent(slug)}?limit=${limit}`,
      BLOG_SUBJECT_REVALIDATE,
    ),
);

/**
 * One blog by its Arabic slug — the CURRENT version. `null` ⇒ `notFound()`.
 *
 * A RETRACTED blog (`is_public=false`) resolves here and MUST: retraction
 * delists it from the gallery and the sitemap, and the returned `is_public`
 * is what makes the page `noindex` (plan §5/§7).
 *
 * ⚠ CACHED for `BLOG_DOC_REVALIDATE`, so a retraction takes up to that long to
 * reach the served page. The sitemap and gallery drop it immediately (they read
 * the DB per request through their own shorter window), so the exposure is a
 * still-indexable page nobody links to, not a leak of a held draft — a
 * `pending` article 404s at the backend and can never be cached as a 200.
 */
export const getPublicBlog = cache(
  async (slug: string): Promise<PublicBlogDetail | null> =>
    fetchPublic<PublicBlogDetail>(
      `/public/blogs/${encodeURIComponent(slug)}`,
      BLOG_DOC_REVALIDATE,
    ),
);

/**
 * A LEGACY `blog_posts` share snapshot by its 32-hex token — the 99 links that
 * are already in the wild (plan D7). Unchanged behaviour, moved here verbatim
 * from `app/blog/[token]/page.tsx` when that route became the dispatcher.
 *
 * ⚠ DELIBERATELY UNCACHED (`0`) while the rest of the wing moved to ISR. Two
 * reasons, either one sufficient. These links are unlisted and hand-delivered,
 * so they earn nothing from a crawl-efficiency change aimed at Google. And the
 * legacy read carries a server-side `view_count` bump whose whole audience is
 * the sender wondering whether the recipient opened it — a revalidation window
 * would silently stop counting reads of the 99 links, which is the exact
 * regression the original `force-dynamic` comment on the route existed to
 * prevent. Keeping this one fetch `no-store` preserves it: a token-shaped ref
 * renders dynamically even though its sibling vocabulary is cached.
 */
export const getLegacyBlogPost = cache(
  async (token: string): Promise<BlogPostPublic | null> =>
    fetchPublic<BlogPostPublic>(`/public/blog/${encodeURIComponent(token)}`, 0),
);
