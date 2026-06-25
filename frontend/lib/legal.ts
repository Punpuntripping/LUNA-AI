/**
 * Single source of truth for the legal-document version users consent to.
 *
 * Bump this whenever the Terms or Privacy text changes materially. The signup
 * consent flow sends this value with registration so we can store WHICH version
 * the user agreed to (and later re-prompt if it no longer matches).
 *
 * Format: the "آخر تحديث" date carried at the top of the markdown docs.
 */
export const LEGAL_VERSION = "2026-06-22";

/** Public routes for the rendered legal documents. */
export const LEGAL_ROUTES = {
  terms: "/terms",
  privacy: "/privacy",
} as const;
