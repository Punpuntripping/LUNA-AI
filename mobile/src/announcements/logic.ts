import type { Announcement, AnnouncementsResponse } from "@contracts";

import { WEB_URL } from "@/config/env";

// «رسائل ريحان» — pure rules shared by the bell, the messages screen and the
// popup (no native imports: unit-tested under node).

/** The messages screen's pathname (`app/(app)/messages.tsx`) — the popup
 *  never shows on it. */
export const MESSAGES_PATH = "/messages";

/** `POST /announcements/read` takes 1–50 ids. */
export const READ_BATCH_MAX = 50;
/** The bell shows «9+» above this. */
export const BADGE_MAX = 9;

function str(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function toAnnouncement(raw: unknown): Announcement | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const id = str(r.announcement_id);
  const title = str(r.title);
  const body = str(r.body_md);
  const published = str(r.published_at);
  if (!id || !title || !body || !published) return null;
  // Both or neither (migration 189 CHECK) — a half link is dropped, never guessed.
  const label = str(r.link_label);
  const url = str(r.link_url);
  const hasLink = label !== null && url !== null;
  return {
    announcement_id: id,
    title,
    body_md: body,
    link_label: hasLink ? label : null,
    link_url: hasLink ? url : null,
    published_at: published,
    read: r.read === true,
  };
}

/**
 * The GET body → a shape the UI can trust. The popup host lives in the
 * signed-in layout, so a malformed body must fail the query (bell without a
 * badge, no popup) instead of crashing every screen. Throws when the body is
 * not `{items: [...]}`; bad rows are dropped.
 */
export function normalizeAnnouncements(raw: unknown): AnnouncementsResponse {
  const body = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : null;
  if (!body || !Array.isArray(body.items)) throw new Error("announcements: unexpected body");
  const items = body.items.map(toAnnouncement).filter((a): a is Announcement => a !== null);
  const count = body.unread_count;
  const unread_count =
    typeof count === "number" && Number.isInteger(count) && count >= 0 ? count : items.filter((a) => !a.read).length;
  return { items, unread_count };
}

/** Flip `read` on `ids` and lower `unread_count` by the number flipped.
 *  Returns the same object when nothing changes. */
export function withRead(data: AnnouncementsResponse, ids: Iterable<string>): AnnouncementsResponse {
  const set = ids instanceof Set ? (ids as Set<string>) : new Set(ids);
  if (set.size === 0) return data;
  let flipped = 0;
  const items = data.items.map((item) => {
    if (item.read || !set.has(item.announcement_id)) return item;
    flipped += 1;
    return { ...item, read: true };
  });
  if (flipped === 0) return data;
  return { items, unread_count: Math.max(0, data.unread_count - flipped) };
}

/** Unread ids in list order. */
export function unreadIds(items: readonly Announcement[]): string[] {
  return items.filter((a) => !a.read).map((a) => a.announcement_id);
}

/** Distinct ids in batches the read endpoint accepts. */
export function readBatches(ids: readonly string[], size: number = READ_BATCH_MAX): string[][] {
  const distinct = [...new Set(ids)];
  const out: string[][] = [];
  for (let i = 0; i < distinct.length; i += size) out.push(distinct.slice(i, i + size));
  return out;
}

/** Bell badge text (Latin digits): null at 0, «9+» above 9. */
export function unreadBadgeLabel(count: number): string | null {
  if (!Number.isFinite(count) || count < 1) return null;
  return count > BADGE_MAX ? `${BADGE_MAX}+` : String(Math.floor(count));
}

/** Screen-reader value for the bell (Latin digits). */
export function unreadAccessibilityValue(count: number): string | undefined {
  return Number.isFinite(count) && count >= 1 ? `غير المقروءة: ${Math.floor(count)}` : undefined;
}

/**
 * `link_url` → what the browser opens: an in-site path (`/learn/…`) is the
 * public page on rayhanai.com, an absolute `https://` URL stays as it is.
 * Anything else (`//host`, `http:`, other schemes, blanks) → null = no button.
 */
export function announcementLinkUrl(linkUrl: string | null | undefined): string | null {
  const raw = (linkUrl ?? "").trim();
  if (!raw) return null;
  if (raw.startsWith("/") && !raw.startsWith("//")) return `${WEB_URL}${raw}`;
  if (/^https:\/\/[^/]/i.test(raw)) return raw;
  return null;
}
