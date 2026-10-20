// «مدونة» in the composer — attach a published blog snapshot to a conversation
// (by share link, or from مدوناتي). Shared by the web and the mobile app.
//
// Additive: the web still declares `MyBlogItem`, `MyBlogsResponse` and
// `BlogItemResponse` in `@/types`, and `extractBlogToken` privately in
// `components/chat/ComposerPlusMenu.tsx`. Identical shapes / regexes.

import type { WorkspaceItem } from "./agents";

/** One row of `GET /api/v1/blogs/mine` (مدوناتي). */
export interface MyBlogItem {
  post_id: string;
  token: string;
  /** Question-mode posts have no title — fall back to the snippet. */
  title: string | null;
  snippet: string;
  subtype: string | null;
  display_mode: "question" | "title";
  is_public: boolean;
  /** A snapshot imported from someone else's share link («مستوردة»). */
  is_imported: boolean;
  created_at: string;
}

export interface MyBlogsResponse {
  posts: MyBlogItem[];
}

/**
 * `POST /api/v1/conversations/{id}/blog-items` with `{ token }` (a share URL or
 * a bare 32-hex token) — copies the post into the conversation as a
 * `kind='agent_search'` workspace item. `already_attached` = server dedup
 * returned an item the conversation already had.
 */
export interface BlogItemRequest {
  token: string;
}

export interface BlogItemResponse {
  item: WorkspaceItem;
  already_attached: boolean;
}

/** Max blog chips held in the composer at once (web MAX_BLOG_CHIPS). */
export const MAX_BLOG_CHIPS = 3;

/**
 * The 32-hex share token in a full share URL (`…/blog/<token>`) or a bare
 * token; `null` when nothing matches. Lower-cased. Same regexes as the web
 * «إضافة رابط» dialog (ComposerPlusMenu.extractBlogToken).
 */
export function extractBlogToken(input: string): string | null {
  const fromUrl = input.match(/\/blog\/([0-9a-f]{32})(?![0-9a-f])/i);
  if (fromUrl) return fromUrl[1].toLowerCase();
  const bare = input.trim().match(/^([0-9a-f]{32})$/i);
  return bare ? bare[1].toLowerCase() : null;
}

/** The row label of a post in «من مدوناتي» (web MyBlogMenuList). */
export function myBlogMenuLabel(post: Pick<MyBlogItem, "title" | "snippet">): string {
  return (post.title ?? "").trim() || post.snippet.trim() || "مدونة";
}

export type BlogQueueResult =
  | { ok: true; tokens: string[] }
  /** `tokens` empty and no error = nothing new (all duplicates) — a silent no-op. */
  | { ok: false; error: string | null };

/**
 * Which of `incoming` to import: drops tokens already held (`held`) or repeated
 * in `incoming`, then caps at `max - held.length` — the web ChatInput
 * queueBlogTokens rules, error text included.
 */
export function planBlogQueue(held: readonly string[], incoming: readonly string[], max: number = MAX_BLOG_CHIPS): BlogQueueResult {
  const already = new Set(held);
  const fresh = incoming.filter((t, i) => !already.has(t) && incoming.indexOf(t) === i);
  if (fresh.length === 0) return { ok: false, error: null };
  const room = max - held.length;
  if (room <= 0) return { ok: false, error: `الحد الأقصى ${max} مدونات في الرسالة` };
  return { ok: true, tokens: fresh.slice(0, room) };
}
