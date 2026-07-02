import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export const myBlogsKeys = {
  all: ["my-blogs"] as const,
  list: () => [...myBlogsKeys.all, "list"] as const,
};

/**
 * مدوناتي — the caller's own blog_posts (both templates, owner-scoped) via
 * ``GET /blogs/mine``. The response also carries ``can_publish_public``, which
 * gates the «نشر في المدونة العامة» toggle on the management page.
 */
export function useMyBlogs() {
  return useQuery({
    queryKey: myBlogsKeys.list(),
    queryFn: () => api.listMyBlogs(),
  });
}
