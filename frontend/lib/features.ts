/**
 * Frontend feature flags.
 *
 * Central place to gate features that exist in the codebase but are not yet
 * meant to be reachable by end users. Flipping a flag back to `true` re-enables
 * the feature with no other code changes — all hooks, components, routes, and
 * backend endpoints are left intact behind the flag.
 */

/**
 * Cases (القضايا) — case-specific mode: cases CRUD, case-scoped documents,
 * memories, and the case workspace route. Currently off: the sidebar row is not
 * rendered at all and every entry point is gated, so the feature is unreachable
 * and leaves no trace in the nav. (It used to render greyed out with a
 * «قيد التطوير» badge; a row that can never be clicked is clutter, not a
 * roadmap.) Set to `true` and the row, the case list and every route come back
 * with no other code change.
 */
export const CASES_ENABLED = false;
