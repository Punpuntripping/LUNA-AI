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
// ⚠ ONLY THE HEADERS are taken from `serverFetchInit`. Its `next.revalidate`
// window is meaningless on the `force-dynamic` routes these serve, and mixing
// it with `cache: "no-store"` would change the caching semantics. With
// `EDGE_SECRET` unset the value is `undefined`, which `fetch` treats exactly as
// an absent key, so the request on the wire is unchanged.
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

/** One anon GET against the backend, parsed as `T`. `null` on anything else. */
async function fetchPublic<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${SERVER_API_BASE}/api/v1${path}`, {
      cache: "no-store",
      headers: serverFetchInit(0).headers,
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
    const data = await fetchPublic<PublicBlogListResponse>(
      `/public/blogs?limit=${limit}`,
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
  const data = await fetchPublic<BlogSubjectsResponse>(`/public/blogs/subjects`);
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
    fetchPublic<BlogSubjectFeedResponse>(
      `/public/blogs/subjects/${encodeURIComponent(slug)}?limit=${limit}`,
    ),
);

/**
 * One blog by its Arabic slug — the CURRENT version. `null` ⇒ `notFound()`.
 *
 * A RETRACTED blog (`is_public=false`) resolves here and MUST: retraction
 * delists it from the gallery and the sitemap, and the returned `is_public`
 * is what makes the page `noindex` (plan §5/§7).
 */
export const getPublicBlog = cache(
  async (slug: string): Promise<PublicBlogDetail | null> =>
    fetchPublic<PublicBlogDetail>(`/public/blogs/${encodeURIComponent(slug)}`),
);

/**
 * A LEGACY `blog_posts` share snapshot by its 32-hex token — the 99 links that
 * are already in the wild (plan D7). Unchanged behaviour, moved here verbatim
 * from `app/blog/[token]/page.tsx` when that route became the dispatcher.
 */
export const getLegacyBlogPost = cache(
  async (token: string): Promise<BlogPostPublic | null> =>
    fetchPublic<BlogPostPublic>(`/public/blog/${encodeURIComponent(token)}`),
);
