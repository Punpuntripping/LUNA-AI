import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

/**
 * ⚠ `lists()` vs `list(q)`. `lists()` is the PREFIX over every مدوناتي listing —
 * unfiltered and every live search — and is what an invalidation after a
 * publish / import / delete must target, or a search result set would keep
 * showing a post that no longer exists. `list(q)` is one exact cache entry;
 * `list()` (no argument) is the unfiltered one, which is the only entry a
 * `setQueryData` patch can meaningfully rewrite, since a BM25-ranked page is
 * the server's ordering and not ours to splice into.
 */
export const myBlogsKeys = {
  all: ["my-blogs"] as const,
  lists: () => [...myBlogsKeys.all, "list"] as const,
  list: (q = "") => [...myBlogsKeys.lists(), q] as const,
};

/**
 * مدوناتي — the caller's own blog_posts (both templates, owner-scoped) via
 * ``GET /blogs/mine``.
 *
 * ⚠ The response no longer carries ``can_publish_public`` (blog_subjects.md
 * §8): the «نشر في المدونة العامة» toggle it gated is gone, because the public
 * blog wing is its own table (``public_blogs``) written by the editorial
 * service key. These rows are share snapshots and مدوناتي is their owner view.
 *
 * ``q`` (bm25_navigation_search.md Wave D) hands the SAME endpoint a search
 * term and gets the SAME envelope back, BM25-ranked instead of newest-first —
 * so `MyBlogsGrid` keeps rendering the card it already renders and no snippet
 * or highlight apparatus is involved (D3). Callers must pass a term that has
 * already cleared the 3-character floor (`useSearchQuery`); a shorter one is a
 * 400 in Arabic.
 */
export function useMyBlogs(q = "") {
  const term = q.trim();
  return useQuery({
    queryKey: myBlogsKeys.list(term),
    queryFn: () => api.listMyBlogs(term),
    // Every keystroke past the floor is a new cache key. Without this the grid
    // would unmount to a spinner on each one; with it the previous cards stay
    // put and simply get replaced — the same feel `useHubSearch` gives the
    // public wings.
    placeholderData: keepPreviousData,
  });
}
