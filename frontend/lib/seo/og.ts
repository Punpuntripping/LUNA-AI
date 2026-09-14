/**
 * The one builder for `/og` card URLs.
 *
 * ⚠ WHY A VERSION TOKEN. `ImageResponse` ships the card with
 * `Cache-Control: public, immutable, max-age=31536000`, and every unfurler —
 * X, LinkedIn, WhatsApp, and Cloudflare in front of them — keys its copy on
 * the URL. A redesign therefore never reaches a page that has already been
 * shared: the scraper keeps serving the card it captured. Bumping
 * `OG_VERSION` mints a new URL for every page at once, which is the only way
 * to retire a card already in circulation.
 *
 * So: bump `OG_VERSION` whenever `app/og/route.tsx` changes what the card
 * LOOKS like. Do not bump it for a refactor that renders identical pixels —
 * every bump throws away a warm CDN cache.
 *
 * Callers pass the raw title; encoding happens here exactly once.
 */

/** v1 = Satori text (never versioned, the unversioned URLs). v2 = the
 * deep-green HarfBuzz card, 2026-09-14. */
export const OG_VERSION = 2;

/**
 * `kind: "blog"` draws the «من مدوّنة ريحان» kicker and the «اقرأ المقال
 * كاملاً» CTA. The CTA is only truthful where the whole image is the link, so
 * it is for blog unfurls and nothing else.
 */
export function ogImageUrl(title: string, kind?: "blog"): string {
  const params = new URLSearchParams({ title, v: String(OG_VERSION) });
  if (kind) params.set("kind", kind);
  return `/og?${params}`;
}
