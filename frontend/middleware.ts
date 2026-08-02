import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

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

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
