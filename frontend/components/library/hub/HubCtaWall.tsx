"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Library, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { buttonVariants } from "@/components/ui/button";
import { getAccessToken } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import {
  hubWallCopy,
  rateLimitedCopy,
  sectionWallCopy,
  type RefusalCardCopy,
  type SectionScope,
} from "@/lib/library/gate-copy";
import { HubPagination } from "@/components/library/hub/HubPagination";
import {
  HubCards,
  type HubItem,
  type HubSection,
} from "@/components/library/hub/HubCards";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/**
 * Re-exported for callers that predate the split. The type — and the card
 * switch that used to live here — moved to `HubCards.tsx` when the live search
 * panel became a second client-side producer of hub cards.
 */
export type { HubSection };

/** The hub envelope, as read from the CLIENT-side authed call. */
interface AuthedHubPayload {
  items: HubItem[];
  page: number;
  total_pages: number;
  cap_reached?: boolean;
  /** The caller's real cap: anon 1 · free 3 · paid unbounded (wire: 9999). */
  max_page?: number;
  /** @deprecated same value as `max_page`. */
  max_anon_page?: number;
}

interface HubCtaWallProps {
  section: HubSection;
  /** Section base path, e.g. "/regulations" — for the revealed pagination. */
  basePath: string;
  page: number;
  totalPages: number;
  /**
   * The cap carried by the SERVER render. Because the server fetch is
   * unauthenticated (and ISR-cached), this is always the ANON cap — it is the
   * fallback for when the authed call cannot report a better one.
   */
  anonMaxPage: number;
  /**
   * Already-encoded filter query (no leading "?") sent with the AUTHED FETCH,
   * so a revealed page shows the same filtered slice the server render asked
   * for.
   */
  query?: string;
  /**
   * Query carried on the revealed PAGINATION LINKS. Defaults to `query`.
   *
   * Pass `""` when the filter lives in the PATH rather than the query string —
   * `/library/{sector}/{type}` already says which sector it is, and appending
   * `?sector_slug=…` to those links would mint a second URL for one page.
   */
  linkQuery?: string;
  /** `name_ar → slug` for the sector pills on the revealed cards (D11). */
  sectorSlugs?: Record<string, string>;
  /**
   * Set when this list is narrowed to a SECTION (a sector or a court) rather
   * than being a page of the unfiltered wing. Swaps BOTH refusal cards for the
   * section wall: the bound being reported is no longer depth, so neither
   * «سجّل مجاناً» nor «باقتك الحالية تتيح ٣ صفحات» describes what happened.
   *
   * It changes copy ONLY. The authed fetch below still runs and still decides —
   * a paid reader gets the real cards on exactly this path.
   */
  sectionScope?: SectionScope;
}

type Phase = "loading" | "ready" | "capped" | "error" | "rate_limited";

/**
 * The browse-depth wall — and, for signed-in readers, the way PAST it.
 *
 * ⚠ THE OTHER REFUSAL ON THIS PATH. Depth is not the only bound a signed-in
 * reader can hit: the reach meter (navigation-hardening 2.2) refuses with a
 * plain 429 once one user has been served its per-hour budget of DISTINCT
 * library items. That is a real answer, not a broken page, so it must never
 * collapse into the neutral «تعذّر تحميل هذه الصفحة» — see `fetchAuthedHubPage`.
 *
 * ⚠ THE ISR CONSTRAINT (PART 9 trap 2 / D11). The library runs ISR with a
 * SHARED cache and no auth variance (hub 3600s), so the server render of a hub
 * page is the ANONYMOUS one for everybody: `cap_reached` is true from page 2 and
 * `max_page` reads 1, whoever is looking. Per-user bytes may reach the browser
 * ONLY through a client-side authed fetch — which is exactly what this component
 * does. Nothing here may ever move into `lib/library/api.ts` or a server
 * component; that would poison the cache for every subsequent visitor.
 *
 * So:
 *   anonymous          → the signup wall (unchanged behaviour, new threshold:
 *                        the anon cap is now 1 page, so the wall starts at
 *                        page 2 instead of page 4)
 *   signed-in, in cap  → the real cards + pagination, fetched client-side
 *                        (free reaches page 3; paid is unbounded)
 *   signed-in, past it → the UPGRADE wall, sized by the caller's own `max_page`
 *
 * …and orthogonally to all three, `sectionScope` (2026-08-11): on a sector or
 * court list the backend refuses every page below `paid`, so BOTH walls become
 * the section wall and only a paid reader ever reaches the cards branch. The
 * mechanism is unchanged — same fetch, same phases, different copy — because the
 * refusal arrives in the same `cap_reached` envelope.
 *
 * The route's `generateMetadata` keeps deciding `noindex` off the ANON
 * `cap_reached`, which is correct and unchanged: what Googlebot sees is the wall,
 * and a signup/upgrade wall carries no SEO value. Same response for Googlebot and
 * for a signed-out human — no cloaking.
 */
export function HubCtaWall({
  section,
  basePath,
  page,
  totalPages,
  anonMaxPage,
  query,
  linkQuery,
  sectorSlugs,
  sectionScope,
}: HubCtaWallProps) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const [phase, setPhase] = useState<Phase>("loading");
  const [data, setData] = useState<AuthedHubPayload | null>(null);
  const [callerMaxPage, setCallerMaxPage] = useState<number>(anonMaxPage);

  useEffect(() => {
    if (!isAuthenticated) {
      setPhase("loading");
      setData(null);
      return;
    }
    let active = true;
    setPhase("loading");
    void (async () => {
      const result = await fetchAuthedHubPage(section, page, query);
      if (!active) return;
      if (!result.ok) {
        // A refused budget and a broken backend are different answers and get
        // different cards — the whole point of the union this returns.
        setPhase(result.error === "rate_limited" ? "rate_limited" : "error");
        return;
      }
      const { payload } = result;
      setCallerMaxPage(payload.max_page ?? payload.max_anon_page ?? anonMaxPage);
      if (payload.cap_reached || payload.items.length === 0) {
        setData(null);
        setPhase("capped");
        return;
      }
      setData(payload);
      setPhase("ready");
    })();
    return () => {
      active = false;
    };
  }, [isAuthenticated, section, page, query, anonMaxPage]);

  // ── Anonymous: the signup wall (also the SSR + Googlebot render). ──────────
  // On a SECTION list it is the section wall instead — a free account buys
  // nothing here, so «سجّل مجاناً» would be a promise the next screen breaks.
  if (!isAuthenticated) {
    return (
      <Wall
        copy={sectionScope ? sectionWallCopy(sectionScope) : hubWallCopy.anon}
      />
    );
  }

  if (phase === "loading") {
    return (
      <p
        dir="rtl"
        className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground"
      >
        <Loader2 aria-hidden="true" className="h-4 w-4 shrink-0 animate-spin" />
        {hubWallCopy.loading}
      </p>
    );
  }

  // The reach meter refused this page (2.2). Reuses the gate's EXISTING 429 card
  // verbatim — «طلبات كثيرة في وقت قصير», which already says, correctly, that
  // nothing was charged: this meter counts items SEEN, and never touches the
  // unlock ledger. It carries no CTA, which is right at 3600s of Retry-After —
  // a «حاول مرة أخرى» button here could only fail again.
  if (phase === "rate_limited") {
    return <Wall copy={rateLimitedCopy} />;
  }

  if (phase === "error") {
    return (
      <p dir="rtl" className="py-16 text-center text-sm text-muted-foreground">
        {hubWallCopy.error}
      </p>
    );
  }

  // Signed in and still refused. On a section list that is the section gate, not
  // the depth cap — quoting a page allowance would name the wrong bound (a free
  // reader's 3 pages are real, they just do not apply to a pre-cut slice).
  if (phase === "capped" || !data) {
    return (
      <Wall
        copy={
          sectionScope
            ? sectionWallCopy(sectionScope)
            : hubWallCopy.upgrade(callerMaxPage)
        }
      />
    );
  }

  return (
    <>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <HubCards
          section={section}
          items={data.items}
          sectorSlugs={sectorSlugs}
        />
      </div>
      <HubPagination
        basePath={basePath}
        currentPage={data.page}
        totalPages={data.total_pages || totalPages}
        query={linkQuery ?? query}
      />
    </>
  );
}

// ------------------------------------------------------------------
// Wall card
// ------------------------------------------------------------------

/**
 * The conversion card itself. Deep browsing is an account/plan feature — this
 * reads as a feature, not a paywall slap (§1.2): neutral primary tone, no
 * scolding, and a single clear action.
 *
 * Typed with `RefusalCardCopy` — the SAME shape `FullContentGate`'s refusal card
 * takes, so every «this page is not for you (yet)» string in the library gate
 * flows through one interface out of `gate-copy.ts`. Its CTA is optional, which
 * is what lets a bare rate-limit card render here without inventing a button
 * that cannot help.
 */
function Wall({ copy }: { copy: RefusalCardCopy }) {
  return (
    <section
      dir="rtl"
      // Tagged for the anon conversion POPUP's gate 5 (T6) — never two calls to
      // action on screen at once.
      data-anon-cta
      className="mx-auto my-8 max-w-xl overflow-hidden rounded-2xl border border-primary/20 bg-gradient-to-b from-primary/5 to-card p-8 text-center shadow-md sm:p-10"
    >
      <div className="mx-auto mb-5 flex h-16 w-16 items-center justify-center rounded-2xl bg-primary/10 text-primary ring-1 ring-primary/15">
        <Library aria-hidden="true" className="h-8 w-8" />
      </div>
      <h2 className="text-xl font-bold text-foreground">{copy.title}</h2>
      <p className="mx-auto mt-2.5 max-w-md text-sm leading-relaxed text-muted-foreground">
        {copy.body}
      </p>
      {copy.ctaHref && copy.ctaLabel && (
        <Link
          href={copy.ctaHref}
          className={cn(buttonVariants({ size: "lg" }), "mt-6 px-8 shadow-sm")}
        >
          {copy.ctaLabel}
        </Link>
      )}
    </section>
  );
}

// ------------------------------------------------------------------
// Client-side authed hub fetch
// ------------------------------------------------------------------

/**
 * Why a hub page produced no cards, when it was NOT a depth cap.
 *
 *   rate_limited → 429: the per-user reach meter (2.2) is full for this window.
 *                  A real, explainable answer — it gets its own card.
 *   failed       → dead session, transport fault, 5xx, unparsable body. All
 *                  indistinguishable to a reader, and all mean «try again».
 */
type HubFetchError = "rate_limited" | "failed";

type AuthedHubResult =
  | { ok: true; payload: AuthedHubPayload }
  | { ok: false; error: HubFetchError };

/**
 * Is this non-OK response the project's standard rate-limit refusal?
 *
 * Status first, because that is the one signal every layer preserves: the
 * backend's 429 is JSON, but an edge-injected one (Cloudflare) is HTML and would
 * never parse. The `RATE_LIMITED` code is checked as a second chance only.
 */
async function isRateLimited(res: Response): Promise<boolean> {
  if (res.status === 429) return true;
  try {
    const body = (await res.json()) as { error?: { code?: string } };
    return body?.error?.code === "RATE_LIMITED";
  } catch {
    return false;
  }
}

/**
 * Fetch one hub page WITH the bearer, so the backend applies the caller's own
 * depth cap and answers `private, no-store`.
 *
 * Plain `fetch`, not the shared `apiFetch` — same reason as the full-content
 * reveal: a dead-session 401 must never trigger the global «redirect to /login»
 * on a PUBLIC library page.
 *
 * ⚠ This used to return `null` on every non-OK status, which is the same bug
 * plan PART 5 called out one layer down: a reader whose reach budget is full was
 * shown «تعذّر تحميل هذه الصفحة» — told the page is broken when in fact the
 * answer was about them. So a 429 is now an ANSWER, carried out of here as a
 * discriminated union, exactly like `fetchFullContent`'s `rate_limited`.
 */
async function fetchAuthedHubPage(
  section: HubSection,
  page: number,
  query?: string,
): Promise<AuthedHubResult> {
  const token = getAccessToken();
  if (!token) return { ok: false, error: "failed" };

  const qs = query ? `page=${page}&${query}` : `page=${page}`;
  try {
    const res = await fetch(
      `${API_BASE}/api/v1/public/library/${section}?${qs}`,
      { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" },
    );
    if (!res.ok) {
      const limited = await isRateLimited(res);
      return { ok: false, error: limited ? "rate_limited" : "failed" };
    }
    const payload = (await res.json()) as AuthedHubPayload;
    return Array.isArray(payload?.items)
      ? { ok: true, payload }
      : { ok: false, error: "failed" };
  } catch {
    return { ok: false, error: "failed" };
  }
}
