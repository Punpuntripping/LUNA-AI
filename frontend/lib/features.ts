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
 * memories, and the case workspace route. Currently "under development"
 * (قيد التطوير): the sidebar entry is shown disabled and every entry point is
 * gated so the feature is unreachable. Set to `true` to bring it back.
 */
export const CASES_ENABLED = false;
