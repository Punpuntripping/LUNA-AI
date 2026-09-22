import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import {
  NATIONAL_DAY_PATH,
  isNationalDayWindow,
} from "@/lib/campaigns/national-day";

const publicPaths = ["/login", "/register"];

/**
 * `noindex` for every internal-search URL (`bm25_navigation_search.md` §0.1,
 * success criterion «any `?q=` URL emits noindex»).
 *
 * ⚠ WHY A HEADER AND NOT `generateMetadata`. Emitting the robots meta from the
 * page would mean reading `searchParams` in `app/{wing}/page.tsx`, and that one
 * line opts the route out of static generation. Page 1 of every wing is the
 * whole anonymous-serving strategy — `app/regulations/page/[n]/page.tsx` spells
 * this out at length: making it dynamic «would be a far worse regression than
 * the problem it solves». `X-Robots-Tag` is honoured by Google exactly like the
 * meta tag, is applied per REQUEST (so a statically prerendered response still
 * carries it), and covers every wing from one place.
 *
 * Search results are thin near-duplicates of the hub they filter; not indexing
 * them is the correct SEO posture rather than a concession. `follow` stays on
 * so a crawler that does land on one still reaches the real document pages.
 */
const NOINDEX_SEARCH = "noindex, follow";

export function middleware(request: NextRequest) {
  const { pathname, searchParams } = request.nextUrl;

  // ── اليوم الوطني السعودي 96 ────────────────────────────────────────────
  // While the campaign window is open the anonymous front door is the greeting
  // card generator, not the marketing page. The window's clock lives in
  // `lib/campaigns/national-day.ts` and closes at Saudi midnight on the 24th —
  // this branch then goes inert on its own, with nothing to deploy and nothing
  // to remember.
  //
  // ⚠ ANONYMOUS VISITORS ONLY, and not as a hedge. `/` already bounces
  // authenticated users to /chat (AuthGuard), so the marketing page this
  // replaces is one they never see — but `SiteHeader`'s brand link points at
  // `/` from every public page, and redirecting them too would land every
  // signed-in user who clicks the logo on a card generator instead of the app.
  // The Supabase session cookie is the only session signal a middleware has;
  // @supabase/ssr CHUNKS it (`sb-<ref>-auth-token.0`, `.1`) once it outgrows
  // the 4 KB cookie limit, so this matches the prefix — an equality test on the
  // unchunked name reads «signed out» for exactly the users with the biggest
  // sessions.
  //
  // ⚠ MIDDLEWARE, NOT A `redirect()` IN THE PAGE. `/` is ISR-prerendered
  // (`x-nextjs-prerender: 1`, `s-maxage=60`); redirecting from inside the
  // component would force the route dynamic and surrender that cache
  // permanently for a one-day campaign. Middleware runs ahead of the cache
  // lookup, so the prerendered page sits untouched and resumes serving the
  // instant the window shuts.
  if (pathname === "/" && isNationalDayWindow()) {
    const signedIn = request.cookies
      .getAll()
      .some((cookie) => /^sb-.+-auth-token/.test(cookie.name));

    if (!signedIn) {
      // `clone()` carries the query string across, so utm_* attribution on a
      // campaign link survives the hop instead of dying at the front door.
      const destination = request.nextUrl.clone();
      destination.pathname = NATIONAL_DAY_PATH;

      const redirect = NextResponse.redirect(destination, 307);
      // 307 is already uncacheable by default — say it out loud anyway. A proxy
      // that stored this response would keep bouncing `/` long after the window
      // closed, and the fix would be a cache purge nobody would know to run.
      redirect.headers.set("Cache-Control", "no-store");
      return redirect;
    }
  }

  // Allow public paths
  if (publicPaths.some((p) => pathname.startsWith(p))) {
    return NextResponse.next();
  }

  const response = NextResponse.next();

  // Any page rendering a search query — whichever wing, whichever depth.
  // Deliberately not scoped to a list of routes: a `q` param means «this is a
  // filtered slice of a collection» everywhere in this app, and a list here
  // would silently miss the next surface that grows a search box.
  if ((searchParams.get("q") ?? "").trim().length > 0) {
    response.headers.set("X-Robots-Tag", NOINDEX_SEARCH);
  }

  // Check for refresh token in cookie/localStorage is not possible in middleware.
  // Instead, the AuthGuard client component handles redirect.
  // Middleware can check for a cookie-based session if desired later.
  return response;
}

/**
 * `.well-known` is excluded deliberately, not incidentally. Moyasar's Apple Pay
 * Web Merchant Registration validates
 * `/.well-known/apple-developer-merchantid-domain-association` — an
 * EXTENSIONLESS static file served from `public/` — by fetching it from Apple's
 * infrastructure, which follows no redirects and tolerates no interception. The
 * matcher above would otherwise run middleware on it, and every future addition
 * here (a redirect, an auth probe, a rewrite) would silently break Apple Pay on
 * the next domain re-validation, with the failure surfacing as «the Apple Pay
 * button stopped rendering» weeks later. Excluding the whole prefix keeps the
 * path a pure static read. (`.claude/plans/moyasar_payments.md` Phase D.)
 *
 * The association file itself is NOT in the repo — it is downloaded from the
 * Moyasar dashboard during domain registration and dropped into
 * `frontend/public/.well-known/`.
 */
export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|\\.well-known).*)"],
};
