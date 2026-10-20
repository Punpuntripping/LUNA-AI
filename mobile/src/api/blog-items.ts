import type { BlogItemRequest, BlogItemResponse, MyBlogsResponse } from "@contracts";

import { apiFetch } from "./client";

// «مدونة» in the composer — backend/app/api/blog.py (web `api.createBlogItem`,
// `api.listMyBlogs`, `workspaceApi.delete`).

/** Copy a published post (share URL or bare token) into the conversation as a
 *  `kind='agent_search'` workspace item; server-deduped per conversation. */
export function createBlogItem(conversationId: string, tokenOrUrl: string): Promise<BlogItemResponse> {
  const body: BlogItemRequest = { token: tokenOrUrl };
  return apiFetch<BlogItemResponse>(`/conversations/${encodeURIComponent(conversationId)}/blog-items`, {
    method: "POST",
    body,
  });
}

/** مدوناتي — the caller's own posts, newest first. */
export function listMyBlogs(): Promise<MyBlogsResponse> {
  return apiFetch<MyBlogsResponse>("/blogs/mine");
}

/** Removing a blog chip deletes the item ONLY when that chip's import created
 *  it (never a server-dedup reuse) — same rule as the web. */
export function deleteBlogChipItem(itemId: string): Promise<{ success: boolean }> {
  return apiFetch<{ success: boolean }>(`/workspace/${encodeURIComponent(itemId)}`, { method: "DELETE" });
}
