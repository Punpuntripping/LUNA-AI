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

import type { LibraryItemPageType } from "@/types";

const KEY = "luna_pending_intent";

/**
 * The page types the Case-B carrier accepts (§12a C3) — the ONE list.
 *
 * Exported because three call sites need it and a second copy is how they drift:
 * `isCarryablePageType` (`ChatWithPageCta`) gates the CTA on it, this module
 * re-validates an intent on the way out of storage, and `AskRayhanLoginIntent`
 * validates a page type that arrived in a URL. Kept byte-for-byte in step with
 * `library_item_service.SUPPORTED_PAGE_TYPES` — a type the backend refuses is a
 * button that 400s in Arabic.
 */
export const LIBRARY_ITEM_PAGE_TYPES: readonly LibraryItemPageType[] = [
  "regulation",
  "article",
  "judgment",
  "blog",
  "compliance",
];

/** A carryable page type, or null — for narrowing an untrusted string (a URL
 * parameter, a stale storage payload). */
export function asLibraryItemPageType(
  raw: string | null | undefined,
): LibraryItemPageType | null {
  const value = (raw ?? "").trim();
  return (LIBRARY_ITEM_PAGE_TYPES as readonly string[]).includes(value)
    ? (value as LibraryItemPageType)
    : null;
}

/** Intents older than this are silently dropped (stale tab, abandoned login). */
const MAX_AGE_MS = 30 * 60 * 1000;

export interface ChatWithBlogIntent {
  type: "chat_with_blog";
  /** Blog share token to import into the fresh conversation. */
  token: string;
  /** Epoch ms at stash time (expiry check). */
  at: number;
}

/**
 * An anon visitor asked اسأل ريحان on a public library page → got a teaser →
 * clicked «سجّل مجاناً لعرض الإجابة كاملة». After login the AuthGuard consumer
 * claims the full answer, stashes it for the widget, and returns the visitor to
 * `return_to` (the same page) — the "continuity moment".
 */
export interface ClaimAnonAnswerIntent {
  type: "claim_anon_answer";
  question_id: string;
  session_key: string;
  /** Site-relative path to send the visitor back to after the claim. */
  return_to: string;
  at: number;
}

/**
 * An anon visitor clicked «افتح هذا النموذج في ريحان» on a /forms page. After
 * login the AuthGuard consumer copies the form into قوالبي and opens the writer.
 */
export interface OpenFormInWriterIntent {
  type: "open_form_in_writer";
  slug: string;
  at: number;
}

/**
 * An anonymous visitor clicked «تحدّث مع ريحان عن هذه الصفحة» on a public
 * library page (`.claude/plans/simple_search_family.md` §8 — "Anon return
 * path"). After login the AuthGuard consumer creates a conversation, stashes
 * the page in the chat-store carry slot, and lands the visitor in the chat —
 * where the destination `ChatInput` performs the `library-items` POST and
 * shows the chip, so the object rides `attachment_ids` on their first message.
 *
 * The `chat_with_blog` twin, one level up: a blog is addressed by an opaque
 * share token, a library page by its `(page_type, page_id)` pair.
 */
export interface ChatWithLibraryItemIntent {
  type: "chat_with_library_item";
  page_type: LibraryItemPageType;
  /** Public slug; for an `article` the composite `{reg_slug}/{article_slug}`. */
  page_id: string;
  /** Page heading — the chip label until the POST returns the real title. */
  title: string | null;
  /**
   * The question the reader had already typed into «اسأل ريحان» when they were
   * sent here, prefilled into the destination composer so the resume finishes
   * their sentence instead of handing them a blank box (`pendingComposerDraft`).
   *
   * ⚠ IT TRAVELS IN sessionStorage AND NOWHERE ELSE. It is never appended to the
   * `/login?…` URL: a legal question is exactly the text this product masks by
   * default («وضع السرية»), and a query string is written into CDN access logs,
   * browser history and any `Referer` the next page sends. Storage is same-tab,
   * same-origin, and dies with the tab.
   */
  question?: string | null;
  at: number;
}

export type PendingIntent =
  | ChatWithBlogIntent
  | ClaimAnonAnswerIntent
  | OpenFormInWriterIntent
  | ChatWithLibraryItemIntent;

/**
 * `Omit` does not distribute over unions (it collapses to the common keys, i.e.
 * only `type`), which breaks excess-property checks at every call site — so
 * distribute it manually across the union members.
 */
type DistributiveOmit<T, K extends keyof any> = T extends any ? Omit<T, K> : never;

export function setPendingIntent(intent: DistributiveOmit<PendingIntent, "at">): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify({ ...intent, at: Date.now() }));
  } catch {
    // Storage unavailable — the visitor just lands on /chat normally.
  }
}

/**
 * True when SOMETHING is already stashed — without reading or clearing it.
 *
 * The one caller is `AskRayhanLoginIntent`, which turns a `/login?intent=…` URL
 * into a stored intent. A URL is the WEAKER claim: `«اسأل ريحان»` puts that
 * querystring on every login link it renders, including the one under a teaser
 * whose «سجّل مجاناً لعرض الإجابة كاملة» button has just stored a
 * `claim_anon_answer`. Overwriting that would swap the reader's paid-for answer
 * for a generic carry, so the URL yields to whatever is already there.
 */
export function hasPendingIntent(): boolean {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return false;
    // Freshness matters here, not just presence: an abandoned intent from 40
    // minutes ago is one `consumePendingIntent` will DROP, so yielding to it
    // would trade a working carry for nothing at all.
    return isFresh((JSON.parse(raw) as Record<string, unknown>)?.at);
  } catch {
    return false;
  }
}

/** True when the stashed timestamp is present and inside the freshness window. */
function isFresh(at: unknown): boolean {
  return typeof at === "number" && Date.now() - at <= MAX_AGE_MS;
}

/** Read-and-clear. Returns null for missing, malformed, or expired intents. */
export function consumePendingIntent(): PendingIntent | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    sessionStorage.removeItem(KEY);
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    if (!parsed || !isFresh(parsed.at)) return null;

    switch (parsed.type) {
      case "chat_with_blog":
        return typeof parsed.token === "string"
          ? (parsed as unknown as ChatWithBlogIntent)
          : null;
      case "claim_anon_answer":
        return typeof parsed.question_id === "string" &&
          typeof parsed.session_key === "string" &&
          typeof parsed.return_to === "string"
          ? (parsed as unknown as ClaimAnonAnswerIntent)
          : null;
      case "open_form_in_writer":
        return typeof parsed.slug === "string"
          ? (parsed as unknown as OpenFormInWriterIntent)
          : null;
      case "chat_with_library_item":
        // The page type is re-validated on the way OUT, not just on the way in:
        // a stale tab could hold an intent written before the carryable set
        // changed, and an unsupported type only fails at the backend with an
        // Arabic error the user never asked for.
        return typeof parsed.page_id === "string" &&
          parsed.page_id.length > 0 &&
          asLibraryItemPageType(parsed.page_type as string) !== null
          ? (parsed as unknown as ChatWithLibraryItemIntent)
          : null;
      default:
        return null;
    }
  } catch {
    return null;
  }
}
