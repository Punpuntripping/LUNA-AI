// The FRONTEND half of plan step 3.7 — let a VERIFIED search crawler past the
// anonymous hub depth cap.
//
// THE PROBLEM. Anonymous callers are capped at hub page 1. Googlebot is
// anonymous, so it is capped too, and once §3.2b closes the sitemap feed to the
// public internet a capped crawler has NO discovery path into the corpus at all.
// The backend already ships the exemption (`public_library.is_verified_crawler`)
// — but it never fires on the REAL crawl path, because the hop in the middle
// destroys every signal it keys on: Googlebot hits a Next page, and the Next page
// makes its own server-side `fetch` that forwards no User-Agent, no cookies and
// no edge headers. This module restores exactly one bit across that hop.
//
// ⚠ WHY THIS IS A SEPARATE FILE FROM `api.ts`. `api.ts` is compiled into the
// BROWSER bundle (`hub/RegulationCard.tsx` value-imports `toDocStatus` from it,
// and two `'use client'` modules import that card — see the `EDGE_SECRET` note
// there). A `next/headers` or `node:crypto` import in `api.ts` would therefore
// land in the client graph and fail the build. This file is `server-only` and is
// imported ONLY by deep-hub route segments.

import "server-only";

import { createHash, timingSafeEqual } from "node:crypto";
import { headers } from "next/headers";

/** Set by Cloudflare's Transform Rule from `cf.client.bot` — `0` on every request, `1` for a verified bot. */
const VERIFIED_BOT_HEADER = "x-verified-bot";

/** Set by Cloudflare's Transform Rule on every PROXIED request (plan §3.4a). */
const EDGE_SECRET_HEADER = "x-edge-secret";

/** Mirrors `public_library._TRUTHY_HEADER_VALUES` — one vocabulary, two layers. */
const TRUTHY_VALUES = new Set(["1", "true", "yes", "on"]);

/**
 * Split one header slot into its individual values.
 *
 * `Headers.get()` returns DUPLICATE headers comma-joined, and duplicates are the
 * normal case here: Cloudflare *appends* to client-supplied headers on some
 * paths (the same behaviour that makes leftmost `X-Forwarded-For` untrustworthy
 * — plan §3.5). A caller that reads the joined string as a single value gets a
 * forged prefix for free.
 */
function splitValues(raw: string | null): string[] {
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
function anyValueMatches(values: string[], expected: string): boolean {
  const expectedDigest = createHash("sha256").update(expected, "utf8").digest();
  let matched = false;
  for (const value of values) {
    const digest = createHash("sha256").update(value, "utf8").digest();
    if (timingSafeEqual(digest, expectedDigest)) matched = true;
  }
  return matched;
}

let warnedAboutMissingProof = false;

/**
 * Whether THIS request may be forwarded to the backend as a verified crawler.
 *
 * Returns `true` only when both hold:
 *
 *   1. **The claim.** `X-Verified-Bot` is present and EVERY value in the slot is
 *      truthy. Every, not any: if a client forged `1` and the edge appended its
 *      own `0` (or vice versa), the two copies disagree and we fail closed. A
 *      boolean flag with two answers is not a signal.
 *   2. **The proof.** `X-Edge-Secret` matches `EDGE_SECRET`, i.e. the request
 *      demonstrably transited our Cloudflare edge, so its `X-Verified-Bot` was
 *      written by Cloudflare's `cf.client.bot` evaluation rather than typed by
 *      the caller.
 *
 * ⚠ STEP 2 IS THE WHOLE POINT — do not drop it for convenience. The Next server
 * stays reachable at its raw `*.up.railway.app` hostname forever (Railway offers
 * no IP allowlist, which is why the origin lock exists at all). Without the
 * proof, anyone could `curl` that hostname with `X-Verified-Bot: 1`, and this
 * renderer would re-emit it over the private network carrying its own valid
 * `X-Edge-Secret` — laundering a forged header into a backend that, once
 * `TRUST_CF_HEADERS` is on, treats it as AUTHORITATIVE and gives the UA fallback
 * no second chance. That would silently re-open the exact hole the trusted-edge
 * mode was built to close.
 *
 * ⚠ FAILS CLOSED, AND STAYS INERT UNTIL CUTOVER. `EDGE_SECRET` unset — today,
 * local dev, and the `npm run build` prerender pass — returns `false` BEFORE
 * `headers()` is ever called. Two things follow, both deliberate:
 *
 *   - The calling route does not become dynamic, so deep hub pages keep exactly
 *     today's ISR behaviour until the secret is set.
 *   - The exemption cannot fire, so the ISR-cached HTML of a deep hub page is
 *     always the capped wall and can never be a crawler's uncapped body replayed
 *     to anonymous humans. The two states are each safe on their own terms; it is
 *     the ordering of these checks that makes that true.
 *
 * ⚠ NEVER wrap the `headers()` call in a try/catch. Reading it during a static
 * render throws Next's `DynamicServerError` as the signal to bail to dynamic
 * rendering; swallowing that breaks the bailout.
 *
 * ⚠ TRUST_CF_HEADERS IS THE OTHER HALF. The backend ignores `X-Verified-Bot`
 * entirely until `TRUST_CF_HEADERS` is on, and it ignores the User-Agent
 * fallback (which this hop cannot restore anyway) once it is. So 3.7 only
 * actually works when `EDGE_SECRET` and `TRUST_CF_HEADERS` are both set, behind
 * the orange cloud. Flip them together.
 */
export async function readVerifiedBotSignal(): Promise<boolean> {
  const expectedSecret = process.env.EDGE_SECRET?.trim();
  if (!expectedSecret) return false;

  const incoming = await headers();

  const claims = splitValues(incoming.get(VERIFIED_BOT_HEADER));
  if (claims.length === 0) return false;
  if (!claims.every((value) => TRUTHY_VALUES.has(value.toLowerCase()))) {
    return false;
  }

  if (!anyValueMatches(splitValues(incoming.get(EDGE_SECRET_HEADER)), expectedSecret)) {
    // A crawler claim with no proof of edge transit. Either someone is probing
    // the raw Railway hostname (working as intended — we just refused it), or
    // the `X-Edge-Secret` Transform Rule is scoped to `api.` only and never
    // reaches the frontend, in which case §3.7 is silently dead and the rule's
    // hostname scope is the one-line fix. PART 4 needs to be able to tell those
    // apart, so say it once per process rather than never.
    if (!warnedAboutMissingProof) {
      warnedAboutMissingProof = true;
      console.warn(
        "[library] X-Verified-Bot present without a valid X-Edge-Secret — " +
          "refusing the §3.7 crawler exemption. If Googlebot is hitting deep hub " +
          "pages, check that the Cloudflare Transform Rule covers the frontend hostname.",
      );
    }
    return false;
  }

  return true;
}
