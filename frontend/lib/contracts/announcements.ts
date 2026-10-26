// «رسائل ريحان» — product announcements (`.claude/plans/announcements.md`,
// migration 189). Shared by the web bell/popup/page and the app's equivalents.
//
// Behaviour both clients implement identically:
//   * every UNREAD item pops up once, oldest unread first, one at a time;
//     closing the popup (any way) or pressing its link marks it read;
//   * the bell beside the settings gear shows `unread_count` while > 0;
//   * the messages page lists every item and marks the unread ones read
//     once they are on screen.
// The server decides the audience (everyone vs paying subscribers) — clients
// never filter.

/** One message as `GET /api/v1/announcements` returns it. */
export interface Announcement {
  announcement_id: string;
  title: string;
  /** Markdown, written line by line: a single newline inside a paragraph is a
   *  VISIBLE line break (render it as written); a blank line is a paragraph gap. */
  body_md: string;
  /** Optional call-to-action. Both set or both null. */
  link_label: string | null;
  /** An in-site path (`/learn/usage-limits`) or an absolute `https://` URL. */
  link_url: string | null;
  /** ISO timestamp. */
  published_at: string;
  read: boolean;
}

/** `GET /api/v1/announcements` (authed) → newest first, at most 50. */
export interface AnnouncementsResponse {
  items: Announcement[];
  unread_count: number;
}

/** `POST /api/v1/announcements/read` body (authed) → 204. Idempotent; ids the
 *  caller cannot see are ignored. 1–50 ids. */
export interface MarkAnnouncementsReadRequest {
  ids: string[];
}

/** The popup queue: unread items, oldest first. */
export function unreadOldestFirst(items: readonly Announcement[]): Announcement[] {
  return items
    .filter((item) => !item.read)
    .sort((a, b) => Date.parse(a.published_at) - Date.parse(b.published_at));
}
