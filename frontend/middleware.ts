import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import {
  decideOriginLock,
  EDGE_SECRET_HEADER,
  type OriginLockRecord,
} from "@/lib/edge/origin-lock";

const publicPaths = ["/login", "/register"];

/**
 * Where a secret-less `*.up.railway.app` request goes. A literal, and always
 * concatenated in front of the path rather than passed to `new URL(path, base)`:
 * a path of `//evil.com` would otherwise replace the host and turn this into an
 * open redirect. Never built from the incoming Host, which the client controls.
 */
const CANONICAL_ORIGIN = "https://rayhanai.com";

/**
 * Same text and same envelope as the backend's `origin_locked_response`, so one
 * rollback note covers both services. Deliberately generic: the body must not
 * hint that a header is missing, or a prober learns the bypass from the error.
 */
const ORIGIN_LOCK_MESSAGE = "غير مصرح بالوصول";

/**
 * ⚠ `no-store` IS REQUIRED. A refusal that a shared cache keeps would be served
 * to every visitor of a real page (plan §5 trap 9).
 */
function originLockedResponse(): NextResponse {
  return NextResponse.json(
    {
      error: { code: "FORBIDDEN", message: ORIGIN_LOCK_MESSAGE, status: 403 },
      detail: ORIGIN_LOCK_MESSAGE,
    },
    {
      status: 403,
      headers: {
        "Cache-Control": "private, no-store",
        "X-Robots-Tag": "noindex",
      },
    },
  );
}

/** At most one log line per class in this window, the same as the backend's observe throttle. */
const ORIGIN_LOCK_LOG_WINDOW_MS = 300_000;

interface OriginLockLogState {
  lastLoggedAt: number;
  pending: number;
}

/**
 * Keyed by `(host class, outcome)`. Both are closed vocabularies
 * (`lib/edge/origin-lock.ts`), so this holds a handful of entries at most: a
 * stream of junk hosts all folds into `other` and cannot grow memory or flood
 * the logs.
 */
const originLockLog = new Map<string, OriginLockLogState>();

/**
 * Logs a class the first time it is seen, then at most once per window, in
 * observe and enforce alike. `count` is the requests folded into this line since
 * the last one. There is no timer, so the tail of a burst stays uncounted until
 * that class is seen again. Holds no secret, raw Host or header value.
 */
function reportOriginLock(record: OriginLockRecord): void {
  const key = `${record.hostClass}:${record.outcome}`;
  const state = originLockLog.get(key) ?? { lastLoggedAt: -Infinity, pending: 0 };
  originLockLog.set(key, state);
  state.pending += 1;

  const now = Date.now();
  if (now - state.lastLoggedAt < ORIGIN_LOCK_LOG_WINDOW_MS) return;

  console.warn(
    JSON.stringify({
      event: "origin_lock",
      host_class: record.hostClass,
      outcome: record.outcome,
      value_count: record.valueCount,
      mode: record.mode,
      action: record.action,
      count: state.pending,
    }),
  );
  state.lastLoggedAt = now;
  state.pending = 0;
}

/**
 * ⚠ MUST STAY EXEMPT. Railway's deploy healthcheck probes the container directly,
 * so it never carries the edge secret. Refusing it fails every deploy, including
 * the one that sets `ORIGIN_LOCK_MODE=enforce`, which would leave the old observing
 * image running while the operator believes the lock is on. Exact match, like the
 * backend's `EXEMPT_PATHS`. The route answers from this process and returns nothing
 * worth protecting.
 */
const HEALTHCHECK_PATH = "/api/v1/health";

let disarmedLockReported = false;

/**
 * `enforce` with no secret passes everything, and the quiet that follows looks
 * like a healthy night. One error line per process, the frontend's half of the
 * backend's `origin_lock.disarmed`.
 */
function reportDisarmedLock(): void {
  if (disarmedLockReported) return;
  if (process.env.ORIGIN_LOCK_MODE?.trim() !== "enforce") return;
  if (process.env.EDGE_SECRET?.trim()) return;
  disarmedLockReported = true;
  console.error(
    JSON.stringify({ event: "origin_lock", outcome: "disarmed", mode: "enforce" }),
  );
}

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
  const { pathname, search, searchParams } = request.nextUrl;

  // The origin lock runs first, so a public path is not a way around it.
  //
  // ⚠ `request.headers` ONLY. `headers()` from `next/headers` would opt routes
  // out of static rendering and collapse the anonymous ISR model (plan §5 trap
  // 4). And the Host comes from the header because `request.nextUrl.host` is
  // always `localhost:PORT` in standalone. Env is read here, per request, so the
  // lock arms from the running container's variables, not from the build.
  reportDisarmedLock();
  if (pathname === HEALTHCHECK_PATH) return NextResponse.next();

  const lock = decideOriginLock({
    host: request.headers.get("host"),
    edgeSecretHeader: request.headers.get(EDGE_SECRET_HEADER),
    mode: process.env.ORIGIN_LOCK_MODE,
    edgeSecret: process.env.EDGE_SECRET,
  });
  if (lock.record) reportOriginLock(lock.record);
  if (lock.action === "redirect") {
    return NextResponse.redirect(`${CANONICAL_ORIGIN}${pathname}${search}`, 308);
  }
  if (lock.action === "block") {
    return originLockedResponse();
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
 *
 * `brand/` is excluded because of the origin lock. Next's image optimizer fetches
 * a local file through an internal request that carries NO headers, and that
 * request still runs middleware. Even when the outer `/_next/image` call has a
 * valid secret, the inner one has no Host and no secret, so the lock refuses it
 * and the optimizer returns 400. That is the `BrandLockup` logo in the site
 * header, login, sidebar and chat hero. Proven on a Next 15.5.18 standalone test
 * build, and a curl of the HTML cannot catch it.
 *
 * ⚠ THE RULE: a local image rendered through `next/image` without `unoptimized`
 * must live in `public/brand/`. Anywhere else the lock refuses the optimizer's
 * internal request in production only, and dev will not show it because
 * `EDGE_SECRET` is unset there.
 *
 * One folder rather than an image-extension suffix, on purpose: a suffix would
 * let anyone force page renders and backend calls on the Railway host with
 * random `*.png` paths. Every other public image is requested directly by
 * browsers, which carry the secret through Cloudflare.
 *
 * `runtime: "nodejs"` because the lock's constant-time compare uses `node:crypto`.
 */
export const config = {
  runtime: "nodejs",
  matcher: ["/((?!_next/static|_next/image|favicon.ico|\\.well-known|brand/).*)"],
};
