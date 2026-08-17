/**
 * Product analytics — the session identity
 * (`.claude/plans/product_analytics.md` §2, §5.1).
 *
 * ONE random key per TAB, living in `sessionStorage`. It dies with the tab and
 * is never linked across visits: a VISIT is tracked, a PERSON is not. That is
 * the same posture `lib/anon-cta/session.ts` already took, and §2 of the plan
 * makes it the privacy contract of the whole feature — no persistent visitor
 * ID, no cross-session attribution, nothing to declare in /privacy.
 *
 * ⚠ T3 — do NOT reuse `rayhan_ask_session` from `lib/library/ask.ts`. That key
 * lives in **localStorage**, deliberately, because claiming an anon answer after
 * signup needs it to survive the tab. Borrowing it here would silently convert
 * this feature into persistent visitor tracking and break §2 without anyone
 * noticing. Analytics owns its own **sessionStorage** key, below.
 *
 * ⚠ FAILS CLOSED, exactly like `anon-cta/session.ts`: every accessor is
 * try/catch-guarded, and unusable storage — privacy mode, SSR, a blocked
 * third-party context — yields `null`, which every caller must read as
 * "tracking is off for this tab". An unkeyed event is worse than no event: it
 * cannot be grouped into a session, so it would inflate every denominator in
 * §6 while answering nothing. Never throws (T9).
 *
 * Pure logic. No React, no DOM beyond `sessionStorage` — the storage is
 * injectable so the behaviour can be asserted against a fake.
 */

/**
 * The storage namespace. `v1` versions the VALUE shape (currently a bare
 * opaque string); a tab's key dies with the tab, so there is nothing to
 * migrate and no second key to clean up.
 */
export const ANALYTICS_SESSION_STORAGE_KEY = "rayhan_analytics_v1";

/** The slice of `Storage` this module needs — injectable for tests. */
export type AnalyticsStorage = Pick<Storage, "getItem" | "setItem">;

/** What `ensureAnalyticsSession()` reports back to the tracker. */
export interface AnalyticsSessionInit {
  /** The tab's session key, or `null` when storage is unusable. */
  sessionKey: string | null;
  /**
   * `true` only on the call that MINTED the key. `session_start` is keyed off
   * this rather than off component mount: the key survives a full page reload
   * inside the same tab, so a mount-keyed event would fire once per document
   * and multiply every session count by the number of reloads.
   */
  created: boolean;
}

/** Opaque, 16 bytes of randomness. Long enough never to collide, short enough to index. */
const MAX_KEY_LENGTH = 64;
const KEY_PATTERN = /^[A-Za-z0-9._-]+$/;

/**
 * The backing store, or `null` when there is none. `window.sessionStorage` can
 * THROW on access (not merely be absent) in privacy modes and blocked embeds,
 * hence the try/catch around the property read itself.
 */
function resolveStore(store?: AnalyticsStorage): AnalyticsStorage | null {
  if (store) return store;
  try {
    if (typeof window === "undefined") return null;
    return window.sessionStorage;
  } catch {
    return null;
  }
}

/**
 * A fresh opaque key. `crypto.randomUUID` where available (it needs a secure
 * context, so localhost and production have it and little else does), then
 * `getRandomValues`, then a last-resort time+random string — the key only has
 * to be unique among concurrent tabs, not unguessable.
 */
function newSessionKey(): string {
  try {
    const webCrypto = typeof globalThis === "undefined" ? undefined : globalThis.crypto;
    if (webCrypto && typeof webCrypto.randomUUID === "function") {
      return webCrypto.randomUUID();
    }
    if (webCrypto && typeof webCrypto.getRandomValues === "function") {
      const bytes = new Uint8Array(16);
      webCrypto.getRandomValues(bytes);
      return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
    }
  } catch {
    // Fall through to the arithmetic fallback.
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

/** A stored value is trusted only if it still looks like a key we wrote. */
function isUsableKey(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value.length <= MAX_KEY_LENGTH &&
    KEY_PATTERN.test(value)
  );
}

/**
 * Get-or-create the tab's session key, reporting whether THIS call created it.
 *
 * Returns `{ sessionKey: null, created: false }` when storage is unusable — the
 * fail-closed case. A write that fails is treated the same way: a key that
 * cannot be persisted would be re-minted on the next page and shatter one visit
 * into many sessions, which is a worse lie than silence.
 */
export function ensureAnalyticsSession(
  store?: AnalyticsStorage,
): AnalyticsSessionInit {
  const target = resolveStore(store);
  if (!target) return { sessionKey: null, created: false };

  let existing: string | null;
  try {
    existing = target.getItem(ANALYTICS_SESSION_STORAGE_KEY);
  } catch {
    // The STORAGE itself is unusable → tracking silently off.
    return { sessionKey: null, created: false };
  }
  if (isUsableKey(existing)) return { sessionKey: existing, created: false };

  const minted = newSessionKey();
  try {
    target.setItem(ANALYTICS_SESSION_STORAGE_KEY, minted);
  } catch {
    return { sessionKey: null, created: false };
  }
  return { sessionKey: minted, created: true };
}

/**
 * The tab's session key, minting one if this is the first call of the tab, or
 * `null` when storage is unusable. The convenience wrapper `track()` uses —
 * callers that need to know whether the session just began want
 * `ensureAnalyticsSession()` instead.
 */
export function getAnalyticsSessionKey(
  store?: AnalyticsStorage,
): string | null {
  return ensureAnalyticsSession(store).sessionKey;
}
