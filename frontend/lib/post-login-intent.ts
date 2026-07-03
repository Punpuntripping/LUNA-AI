/**
 * Post-login intent — "resume after login" (.claude/plans/blog_import.md §D6).
 *
 * An anonymous visitor clicks «اتحدث مع المدونة» on a public blog page → the
 * intent is stashed here → they log in (or register, or OAuth round-trip) →
 * ONE consumer (AuthGuard) reads it back and finishes the flow.
 *
 * sessionStorage, not the Zustand store: the Google OAuth flow is a full-page
 * redirect that would wipe any in-memory slot, while sessionStorage survives
 * same-tab navigation and expires with the tab. Every accessor is
 * try/catch-guarded — storage can be unavailable (SSR, privacy modes) and the
 * intent is always best-effort.
 */

const KEY = "luna_pending_intent";

/** Intents older than this are silently dropped (stale tab, abandoned login). */
const MAX_AGE_MS = 30 * 60 * 1000;

export interface ChatWithBlogIntent {
  type: "chat_with_blog";
  /** Blog share token to import into the fresh conversation. */
  token: string;
  /** Epoch ms at stash time (expiry check). */
  at: number;
}

export type PendingIntent = ChatWithBlogIntent;

export function setPendingIntent(intent: Omit<PendingIntent, "at">): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify({ ...intent, at: Date.now() }));
  } catch {
    // Storage unavailable — the visitor just lands on /chat normally.
  }
}

/** Read-and-clear. Returns null for missing, malformed, or expired intents. */
export function consumePendingIntent(): PendingIntent | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    sessionStorage.removeItem(KEY);
    const parsed = JSON.parse(raw) as Partial<PendingIntent>;
    if (parsed?.type !== "chat_with_blog" || typeof parsed.token !== "string") {
      return null;
    }
    if (typeof parsed.at !== "number" || Date.now() - parsed.at > MAX_AGE_MS) {
      return null;
    }
    return parsed as PendingIntent;
  } catch {
    return null;
  }
}
