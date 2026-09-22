/**
 * The 96th Saudi National Day campaign window.
 *
 * For its duration the anonymous front door — the bare `/` — is not the
 * marketing page but the greeting-card generator at `/national-day`. The
 * redirect lives in `middleware.ts`; this module owns nothing but the clock, so
 * that «when does it stop?» has exactly one answer in the repo and retiring the
 * campaign is a grep for this file.
 *
 * ⚠ THE WINDOW CLOSES BY ITSELF. Nothing needs to be deployed on the 24th. That
 * is deliberate: a campaign whose teardown is a manual deploy is a campaign
 * that runs a week too long because someone was asleep. If you retire this
 * early, delete the middleware branch — do NOT just move the date, or the next
 * reader finds a dead constant and cannot tell whether it is live.
 */

/** Where the front door points while the window is open. */
export const NATIONAL_DAY_PATH = "/national-day";

/**
 * The instant the window shuts, as epoch milliseconds: 2026-09-24T00:00:00+03:00
 * — i.e. the moment the 23rd ends in Riyadh, which is 2026-09-23T21:00:00Z.
 *
 * ⚠ WRITTEN IN UTC ON PURPOSE. `new Date("2026-09-24")` and every other
 * local-time constructor read the *server's* zone, and Railway containers run
 * UTC while the audience lives in +03 — a local-time constant would cut the
 * campaign three hours short of the Saudi midnight it is named after. `Date.UTC`
 * pins an absolute instant, so the comparison is correct from any zone.
 * (Month is 0-indexed: `8` is September.)
 */
export const NATIONAL_DAY_WINDOW_ENDS_AT = Date.UTC(2026, 8, 23, 21, 0, 0);

/**
 * Open-ended at the start: the window begins the moment this ships, so there is
 * no opening bound to get wrong. National Day eve is when greetings are
 * actually sent, and a campaign that waits for midnight misses that evening.
 */
export function isNationalDayWindow(now: number = Date.now()): boolean {
  return now < NATIONAL_DAY_WINDOW_ENDS_AT;
}
