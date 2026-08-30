/**
 * Who this tab is signed in as — the Supabase auth uid, remembered in module
 * scope so the eject-to-/login paths can stamp it onto `?u=`.
 *
 * All three ejects run AFTER the session is already gone, at the exact moment
 * neither the auth store (`user: null`) nor the Supabase client can still
 * answer "who was that": `AuthSync`'s SIGNED_OUT branch, `AuthGuard`'s
 * unauthenticated redirect, and `apiFetch`'s 401 handler. Without a remembered
 * value they mint an unscoped `next`, and the account that signs in next
 * inherits the previous account's URL — the «المحادثة غير موجودة» bug that
 * `IDENTITY_PARAM` exists to close.
 *
 * `AuthSync` is the ONLY writer. It already subscribes to every auth event
 * (`INITIAL_SESSION` on mount, `SIGNED_IN`, `TOKEN_REFRESHED`, `SIGNED_OUT`)
 * and is the file that owns identity for the tab, so there is exactly one
 * place that decides what this holds.
 *
 * Module state rather than storage, deliberately: the value only has to
 * outlive the page that is ejecting, and what it produces travels in the URL,
 * which survives the navigation. Nothing persists it across a cold boot, where
 * a tab legitimately knows nobody — and an absent identity degrades to the
 * pre-existing behaviour (`next` honoured as-is), never to a broken one.
 */
let lastAuthUserId: string | null = null;

/** Record the account this tab is bound to. A null/empty id is ignored rather
 *  than stored: SIGNED_OUT carries no user, and it is precisely the departing
 *  identity the eject one line later still needs. */
export function rememberAuthIdentity(authUserId: string | null | undefined): void {
  if (authUserId) lastAuthUserId = authUserId;
}

/** The last account seen on this page, or null before the first auth event. */
export function getLastAuthIdentity(): string | null {
  return lastAuthUserId;
}
