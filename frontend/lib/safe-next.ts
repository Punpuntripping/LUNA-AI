/**
 * `?next=` — the return-to-page carrier, and its open-redirect guard.
 * (.claude/plans/anon_conversion_popup.md §7.3, trap T3.)
 *
 * A visitor reading a public library page clicks a conversion CTA → they land
 * on `/login?next=<where they were>` → after email+password login, Google OAuth,
 * or email verification they are sent back there instead of the hardcoded
 * `/chat`. The three paths converge on this one parameter, so this one file is
 * the single place the value is validated.
 *
 * `next` arrives from a URL and is therefore attacker-controlled: a public,
 * indexed page linking to `/login?next=https://evil.com` would be an open
 * redirect. Validation is an **allowlist**, never a denylist, and it runs on
 * EVERY read — client (`LoginForm`) and server (`app/auth/callback/route.ts`)
 * alike. Anything not explicitly allowed silently produces today's exact
 * behaviour: `/chat`.
 *
 * Deliberately dependency-free — no `next/*`, no `window`, no `node:*` — because
 * it is imported by both a client component and a route handler.
 */

/** Fallback target. Also the pre-existing destination, so a rejected `next`
 *  degrades to the behaviour that shipped before this feature. */
export const DEFAULT_NEXT = "/chat";

/**
 * Site-relative prefixes a visitor may be returned to: the public content
 * surfaces plus the app itself. A path matches when it equals a prefix or sits
 * under it — `/blog` and `/blog/x` pass, `/blogs` does not (the `/` boundary is
 * what stops prefix-collision surprises).
 *
 * ⚠ This is NOT the same list as `lib/anon-cta/eligibility.ts`'s `WINGS`, and
 * the two must not be merged. `WINGS` governs where the popup FIRES (documents
 * on the content wings). This governs where a visitor may be RETURNED TO after
 * authenticating — still the wider set, and it must stay a SUPERSET: every wing
 * has to be returnable, or the popup's own «ابدأ الآن» would drop `next` and
 * land the reader on `/chat` instead of the page that earned the pitch. It is
 * wider than the wings for two more reasons — `BlogConversionCta` is mounted by
 * `LibraryPageShell` on `/library` too, and the app's own surfaces (`/chat`,
 * `/pay`, the session-expiry returns below) are here for reasons that have
 * nothing to do with the popup. The two lists answer different questions and
 * are kept separate on purpose; when a wing is added there, check it is here.
 *
 * As of 2026-08-25 `WINGS` covers all seven document wings — regulations,
 * circulars, judgments, blog, compliance, forms, calculators — and every one of
 * them already appears below, so the superset rule holds with no change needed.
 *
 * Every entry is a route `AuthGuard` already treats as public (`PUBLIC_PREFIXES`)
 * or the app itself, so returning a freshly-authenticated user here is never a
 * privilege question — only a navigation one. `/library/mine` is reachable via
 * the `/library` prefix and is correct: it is the reader's own shelf, which they
 * can see precisely because they just signed in.
 */
const ALLOWED_PREFIXES = [
  "/regulations",
  "/compliance",
  "/circulars",
  "/judgments",
  "/blog",
  "/forms",
  "/calculators",
  "/library",
  "/chat",
  // Checkout. An anonymous visitor clicking a /pricing CTA goes to
  // `/login?next=/pay/pro&mode=register` and must land ON the checkout after
  // signing up — dropping `next` here would deposit them on /chat with no idea
  // the purchase they started still needs finishing, which is the single
  // highest-intent moment the funnel has. `/pay` is an authed app route (it is
  // NOT in AuthGuard's PUBLIC_PREFIXES), so this is a navigation decision and
  // not a privilege one, exactly like `/chat` above.
  "/pay",
  // Session-expiry return targets. When a live session dies out from under the
  // user (time-boxed, revoked, logged out in another tab), the eject paths —
  // AuthGuard's /login redirect, apiFetch's 401 handler, AuthSync's SIGNED_OUT
  // handler — carry the page the user was on so re-login puts them straight
  // back. These are the private app surfaces a user actually sits on; exactly
  // like `/chat` and `/pay` above, returning a freshly-authenticated user to
  // their own pages is a navigation decision, not a privilege one.
  "/chats",
  "/templates",
  "/blogs",
  "/settings",
  // Password-reset landing. `/auth/callback?next=/reset-password` exchanges the
  // emailed code for a session and then sends the user here to choose the new
  // password. Without this entry the value is dropped and they land on /chat
  // still not knowing their password — the one destination the whole flow
  // exists to reach. Like /chat and /pay above this is an authed app route, so
  // returning a freshly-authenticated user here is navigation, not privilege.
  "/reset-password",
] as const;

/**
 * True when the string contains a C0 control character or DEL. Written with
 * char codes rather than a regex literal so no raw control byte ever lands in
 * this file. See the call site in `safeNext` — this check is not cosmetic.
 */
function hasControlChar(value: string): boolean {
  for (let i = 0; i < value.length; i += 1) {
    const code = value.charCodeAt(i);
    if (code < 0x20 || code === 0x7f) return true;
  }
  return false;
}

/**
 * Validate a raw `next` value into a site-relative path that is safe to
 * redirect to. Returns `/chat` for anything not explicitly allowed — including
 * `null`, absolute URLs, protocol-relative paths and malformed encodings.
 *
 * ⚠ A QUERY STRING AND FRAGMENT SURVIVE; ONLY THE PATH IS MATCHED. Added for
 * the search CTA (`bm25_navigation_search.md` D9): an anonymous visitor who
 * clicks a search box is returned to `/regulations?q=إجازة الأمومة`, i.e. the
 * search they were reaching for, not just the wing. Matching the allowlist
 * against the WHOLE string used to reject that — `"/regulations?q=…"` is
 * neither equal to `"/regulations"` nor prefixed by `"/regulations/"` — so the
 * value was silently dropped and the reader landed on `/chat`.
 *
 * The split happens AFTER the control-character, leading-slash and
 * protocol-relative checks, so it can only ever narrow what is matched, never
 * widen it: everything those three checks reject is still rejected. And the
 * result stays same-origin regardless of what the query holds, because the
 * PATH is what decides the origin.
 */
export function safeNext(raw: string | null | undefined): string {
  if (!raw) return DEFAULT_NEXT;

  let path: string;
  try {
    path = decodeURIComponent(raw);
  } catch {
    // Malformed percent-encoding ("%", "%E0%A4%A") — decodeURIComponent throws
    // a URIError. Note the value has usually been decoded once already by
    // URLSearchParams, so this second pass is what catches a double-encoded
    // "%252F%252Fevil.com"; validating the fully-decoded string (and returning
    // that same string) is what makes the check unbypassable.
    return DEFAULT_NEXT;
  }

  // Browsers strip tab/LF/CR out of a URL before resolving it, so "/\t/evil.com"
  // resolves as the protocol-relative "//evil.com" and would slip past the
  // prefix checks below. Rejecting every control character is simpler — and
  // safer — than modelling that stripping. It also rules out CR/LF header
  // injection on the server side.
  if (hasControlChar(path)) return DEFAULT_NEXT;

  // Absolute URL or a scheme ("https://evil.com", "javascript:alert(1)").
  if (!path.startsWith("/")) return DEFAULT_NEXT;

  // Protocol-relative: both forms are browser-valid cross-origin redirects.
  if (path.startsWith("//") || path.startsWith("/\\")) return DEFAULT_NEXT;

  // Match the allowlist against the PATH alone — `?` and `#` end it, whichever
  // comes first. `indexOf` on both rather than a split, because a fragment may
  // legitimately contain a `?` and must not be mistaken for the query start.
  const queryAt = path.indexOf("?");
  const hashAt = path.indexOf("#");
  const cut = [queryAt, hashAt].filter((i) => i >= 0).sort((a, b) => a - b)[0];
  const pathname = cut === undefined ? path : path.slice(0, cut);

  if (
    !ALLOWED_PREFIXES.some((p) => pathname === p || pathname.startsWith(`${p}/`))
  ) {
    return DEFAULT_NEXT;
  }

  return path;
}

/**
 * Build the `/login` URL that carries a return-to target, so callers never
 * hand-roll the encoding.
 *
 *   loginHref("/blog/x")                     → "/login?next=%2Fblog%2Fx"
 *   loginHref("/blog/x", { register: true }) → "/login?next=%2Fblog%2Fx&mode=register"
 *   loginHref("/settings/x")                 → "/login?next=%2Fsettings%2Fx"
 *   loginHref("/pricing")                    → "/login"   (not allowlisted → dropped)
 *
 * `mode=register` opens the form directly on signup, so a button that says
 * «ابدأ الآن» means what it says (§7.7).
 *
 * `identity` is the Supabase auth uid of the session being ejected, passed by
 * the three callers that know it — `AuthSync`'s SIGNED_OUT branch,
 * `AuthGuard`'s redirect and `apiFetch`'s 401 handler. It ships as `?u=` so
 * the consume side can refuse to hand this `next` to a DIFFERENT account; see
 * `IDENTITY_PARAM`. Every other caller is an anonymous CTA with no identity to
 * declare, and its URLs are unchanged.
 */
export function loginHref(
  returnTo: string,
  opts?: { register?: boolean; identity?: string | null },
): string {
  const params = new URLSearchParams();

  // Validated at build time too: a value that could not survive `safeNext` on
  // the way back is left off the URL entirely rather than shipped as a dead
  // parameter.
  const next = safeNext(returnTo);
  if (next !== DEFAULT_NEXT) params.set("next", next);

  // Only meaningful alongside a surviving `next` — with nothing to return to
  // there is nothing to scope, and the same "no dead parameters" rule applies.
  if (next !== DEFAULT_NEXT && opts?.identity) {
    const tag = identityTag(opts.identity);
    if (tag) params.set(IDENTITY_PARAM, tag);
  }

  if (opts?.register) params.set("mode", "register");

  const query = params.toString();
  return query ? `/login?${query}` : "/login";
}

/**
 * `?u=` — the identity a `next` was minted FOR.
 *
 * `next` is minted per-PAGE but consumed per-SESSION, and those stop being the
 * same thing the moment a DIFFERENT account signs in. Signing out of
 * `/chat/<id>` produces `/login?next=/chat/<id>` (AuthSync's SIGNED_OUT
 * branch); if the next account to authenticate in that browser is not the one
 * that just left, it is deposited on a conversation it does not own and the
 * app answers «المحادثة غير موجودة» with a live composer that 404s again on
 * send. Observed in production on 2026-08-30: an account switch on
 * `/chat/d0d29f71-2588-43e1-aca5-f809ecb4a577` — logout 14:04:30, login as the
 * other account 14:04:38, `GET`+`POST` 404 at 14:04:40 and 14:06:17.
 *
 * So the eject paths that KNOW who is leaving stamp this parameter, and the
 * consume side — `LoginForm` and `/auth/callback` — honours `next` only when
 * the account that arrives is the account that left.
 *
 * A `next` minted with NO identity carries no `u` and is honoured exactly as
 * before. That is every anonymous CTA on the public wings — the whole
 * conversion funnel — and it is deliberately untouched.
 */
export const IDENTITY_PARAM = "u";

/**
 * A short, stable, NON-CRYPTOGRAPHIC fingerprint of a Supabase auth user id.
 *
 * The raw uid does not go in the URL: it lands in browser history and in the
 * address bar of what may be a shared machine, and the only question being
 * asked is "same account or not". FNV-1a/32 answers that in eight hex
 * characters synchronously — this module is imported by BOTH a route handler
 * and a client component and must stay dependency-free, which rules out the
 * async `crypto.subtle` digest.
 *
 * ⚠ Not a security boundary. `u` decides NAVIGATION, never access: every
 * target is still authorised server-side on every request, which is exactly
 * what produced the 404 this parameter exists to stop the user from ever
 * seeing. A forged or collided tag can only reproduce the pre-existing
 * behaviour (the `next` is honoured); it can never widen `safeNext`'s
 * allowlist or reach another account's data.
 */
export function identityTag(authUserId: string | null | undefined): string | null {
  if (!authUserId) return null;

  let hash = 0x811c9dc5;
  for (let i = 0; i < authUserId.length; i += 1) {
    hash ^= authUserId.charCodeAt(i);
    // × 16777619 (the 32-bit FNV prime), written as shifts because a plain
    // multiply leaves the 32-bit range and silently loses the low bits through
    // the float64 mantissa.
    hash =
      (hash +
        ((hash << 1) + (hash << 4) + (hash << 7) + (hash << 8) + (hash << 24))) >>>
      0;
  }

  return hash.toString(16).padStart(8, "0");
}

/**
 * True for a value shaped like `identityTag` output. Arriving values are
 * attacker-controlled like `next` is, and `/auth/callback` echoes `u` back
 * onto its `/login?...` error redirects — so junk is dropped at the door
 * rather than carried around the auth round trip.
 */
export function isIdentityTag(value: string | null | undefined): value is string {
  return typeof value === "string" && /^[0-9a-f]{8}$/.test(value);
}

/**
 * Resolve where a freshly-authenticated session should land.
 *
 * The three cases, in the order they are decided:
 *
 *   1. `next` fails `safeNext` (unlisted, hostile, absent) → `/chat`, exactly
 *      as before. The allowlist is still the outer gate and this function
 *      never widens it.
 *   2. No `u` was stamped → honour `next`. An anonymous reader returning to
 *      the library page they were on has no previous identity to match, and
 *      this is the path the conversion funnel takes.
 *   3. `u` was stamped → honour `next` only for the SAME account. A different
 *      account gets `/chat`, its own home, instead of a stranger's URL.
 *
 * `authUserId` is the Supabase auth uid of the session that just came into
 * existence — read AFTER the exchange/sign-in, never before.
 */
export function nextForIdentity(
  rawNext: string | null | undefined,
  rawIdentity: string | null | undefined,
  authUserId: string | null | undefined,
): string {
  const next = safeNext(rawNext);
  if (next === DEFAULT_NEXT) return DEFAULT_NEXT;

  if (!isIdentityTag(rawIdentity)) return next;

  const tag = identityTag(authUserId);
  return tag !== null && tag === rawIdentity ? next : DEFAULT_NEXT;
}
