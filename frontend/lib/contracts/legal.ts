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
  /**
   * Promotional-offer terms. A SATELLITE legal page, deliberately not part of
   * the signup consent bundle: it binds only the subset of users who take an
   * offer, and folding it into /terms made §5 unreadable for everyone else.
   * Linked from wherever a promotional price is shown — see
   * `PROMO_TERMS_NOTE` in lib/pricing.ts.
   */
  promoTerms: "/promo-terms",
} as const;
