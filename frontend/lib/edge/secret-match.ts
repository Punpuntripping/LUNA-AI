// Constant-time matching of the `X-Edge-Secret` header. One implementation for
// its two callers: the origin lock (`lib/edge/origin-lock.ts`, from middleware)
// and the verified-crawler signal (`lib/library/crawler-signal.ts`).
//
// ⚠ NO `next/headers` HERE, EVER. Middleware runs on every route, and a
// `headers()` call anywhere in its path opts routes out of static rendering,
// which collapses the anonymous ISR model. This file takes plain strings.
//
// ⚠ NEVER IMPORT `lib/edge/*` FROM CLIENT CODE. `node:crypto` does not resolve in
// the browser graph, so doing it fails the build. That failure is the guard.

import { createHash, timingSafeEqual } from "node:crypto";

/**
 * Split one header slot into its individual values.
 *
 * `Headers.get()` returns DUPLICATE headers comma-joined, and duplicates are the
 * normal case here: Cloudflare *appends* to client-supplied headers on some
 * paths (the same behaviour that makes leftmost `X-Forwarded-For` untrustworthy
 * — plan §3.5). A caller that reads the joined string as a single value gets a
 * forged prefix for free.
 */
export function splitValues(raw: string | null): string[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((value) => value.trim())
    .filter((value) => value.length > 0);
}

/**
 * Constant-time "does any copy of this header equal the expected secret".
 *
 * Digests, not raw values: `timingSafeEqual` throws on length mismatch, which
 * would leak the secret's length and turn a junk header into a 500. Matching ANY
 * copy mirrors `origin_lock._header_matches` on the backend — with Cloudflare
 * appending, a forged value can sit alongside the real one, and taking only the
 * first would reject legitimate proxied traffic. Accepting any match weakens
 * nothing: the attacker still has to produce the secret.
 *
 * No early `break` — every comparison runs, so total work does not depend on
 * which copy matched.
 */
export function anyValueMatches(values: string[], expected: string): boolean {
  const expectedDigest = createHash("sha256").update(expected, "utf8").digest();
  let matched = false;
  for (const value of values) {
    const digest = createHash("sha256").update(value, "utf8").digest();
    if (timingSafeEqual(digest, expectedDigest)) matched = true;
  }
  return matched;
}
