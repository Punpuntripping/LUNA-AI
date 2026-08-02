"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { Loader2 } from "lucide-react";
import { useDebounce } from "@/hooks/use-debounce";
import { useAuthStore } from "@/stores/auth-store";
import { SearchBar } from "@/components/search/SearchBar";
import { SearchEmptyState } from "@/components/search/SearchEmptyState";
import { SearchFailure } from "@/components/search/SearchFailure";
import { HubCards } from "@/components/library/hub/HubCards";
import { searchErrorKind, useHubSearch } from "@/hooks/use-search";
import {
  SEARCH_COPY,
  SEARCH_DEBOUNCE_MS,
  SEARCH_MIN_LENGTH,
  SEARCH_SURFACE_COPY,
  searchResultCount,
  searchResultsHeading,
  type SearchSurface,
} from "@/lib/search/copy";

/**
 * The filter bar for a public library hub — and the live search results that
 * replace its grid.
 *
 * ── WHY IT WRAPS THE GRID ───────────────────────────────────────────────────
 * The box and the results are one piece of state, so one component has to own
 * both. The hub's normal body (cards + pagination, or the CTA wall, or the
 * empty state) is passed in as `children` — a SERVER-rendered subtree handed to
 * a client island, which costs nothing and keeps every hub view a server
 * component. While a search is live the children are simply not rendered; the
 * moment the box is cleared they come back, already rendered, with no refetch.
 *
 * ── WHY THE SEARCH IS CLIENT-SIDE (D9 + the ISR constraint) ─────────────────
 * Search is registered-only and the server-side hub fetch is anonymous and
 * ISR-cached under a SHARED key, so a search cannot run there: `q` is dropped
 * for an anonymous caller, and a per-user result set written into that cache
 * would be replayed to every visitor for the next hour. So the query runs from
 * the browser with the caller's bearer (`useHubSearch`), exactly like
 * `HubCtaWall`'s authed reveal. Nothing in this file may ever move into
 * `lib/library/api.ts` or a server component.
 *
 * ── THE ANONYMOUS PATH ──────────────────────────────────────────────────────
 * The box renders for everyone. For an anonymous visitor `SearchBar` locks it
 * and a click opens the conversion modal instead of searching (D9), carrying
 * `?next=` back to this exact URL — query string included — so signing up
 * returns them to the search they were reaching for. A visitor who arrives on a
 * SHARED `?q=` link gets the unfiltered hub page 1 with the query seeded into
 * the box: the page they asked for, plus the reason they cannot filter it yet.
 * No modal opens on arrival; that would be an interstitial, and this surface
 * fires only on a gesture Googlebot never performs.
 *
 * ── `?q=` ON THE URL ────────────────────────────────────────────────────────
 * The debounced query is mirrored onto the address bar with
 * `history.replaceState`, NOT a router navigation: a search stays shareable and
 * survives a reload, while page 1 of every wing stays statically prerendered
 * (`app/regulations/page.tsx` — reading `searchParams` there would make the
 * whole anon-serving hub dynamic, which the route's own header calls a far
 * worse regression than anything it would buy). Those URLs are `noindex` via
 * the `X-Robots-Tag` rule in `middleware.ts` — an internal-search page is a
 * thin near-duplicate and indexing it burns crawl budget (§0.1).
 */
export function HubSearchPanel({
  section,
  sectorSlugs,
  filters,
  leading,
  below,
  children,
}: {
  section: SearchSurface;
  /** `name_ar → slug` for the sector pills on result cards (D11). */
  sectorSlugs?: Record<string, string>;
  /**
   * Hub filters that must survive a search — today only `/judgments`' court
   * level and domain. Searching inside an active filter is the behaviour a
   * reader expects from a filter ROW; a search that silently widened the set
   * back out would be a different feature.
   */
  filters?: Record<string, string | undefined>;
  /** Start of the filter row (the judgments court-level chips). */
  leading?: ReactNode;
  /** Under the filter row (the judgments active-filter dismiss row). */
  below?: ReactNode;
  /** The hub's normal body: card grid + pagination, CTA wall, or empty state. */
  children: ReactNode;
}) {
  const pathname = usePathname();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  const [rawQuery, setRawQuery] = useState("");
  /** The live query string on the address bar, kept for the CTA's `?next=`. */
  const [urlSearch, setUrlSearch] = useState("");
  /**
   * Has the reader touched the box? Until they have, the URL is the source of
   * truth and must not be rewritten — a blind sync on mount would strip the
   * very `?q=` we are about to seed FROM it, and a shared search link would
   * erase itself in front of the person who opened it.
   */
  const userTypedRef = useRef(false);

  // Seed from a shared `?q=` link. Read in an effect rather than during render:
  // `window` does not exist server-side, and `useSearchParams()` would drag
  // every statically prerendered hub into a Suspense boundary and fail the
  // build (anon_conversion_popup T2).
  useEffect(() => {
    const search = window.location.search;
    setUrlSearch(search);
    const seeded = new URLSearchParams(search).get("q") ?? "";
    if (seeded.trim().length > 0) setRawQuery(seeded);
  }, []);

  const query = useDebounce(rawQuery, SEARCH_DEBOUNCE_MS).trim();

  // Mirror the debounced query onto the URL. `replaceState`, not `pushState`:
  // one history entry per keystroke would make the back button unusable.
  useEffect(() => {
    if (!userTypedRef.current) return;
    const params = new URLSearchParams(window.location.search);
    if ((params.get("q") ?? "") === query) return;
    if (query.length > 0) params.set("q", query);
    else params.delete("q");
    const next = params.toString();
    const search = next ? `?${next}` : "";
    window.history.replaceState(null, "", `${window.location.pathname}${search}`);
    setUrlSearch(search);
  }, [query]);

  const handleChange = useCallback((value: string) => {
    userTypedRef.current = true;
    setRawQuery(value);
  }, []);

  const isSearching = isAuthenticated && query.length >= SEARCH_MIN_LENGTH;
  const { data, isFetching, isError, error, refetch } = useHubSearch({
    section,
    query,
    filters,
    enabled: isAuthenticated,
  });

  const surface = SEARCH_SURFACE_COPY[section];
  // Nothing has come back for THIS query yet — the first load, or a filter
  // change. `keepPreviousData` means `data` may still hold the previous set,
  // which is what keeps the grid on screen instead of flashing a skeleton.
  const isFirstLoad = isSearching && isFetching && !data;

  return (
    <div dir="rtl" className="space-y-6">
      <div className="space-y-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          {leading}
          <SearchBar
            value={rawQuery}
            onChange={handleChange}
            placeholder={surface.placeholder}
            ariaLabel={surface.ariaLabel}
            isPending={isSearching && isFetching}
            // The gate is what makes an anonymous click a conversion instead of
            // a dead box. `returnTo` carries the CURRENT query string so a
            // shared `?q=` link survives the round trip through signup.
            gate={{ returnTo: `${pathname}${urlSearch}` }}
            className="sm:w-64"
          />
        </div>
        {below}
      </div>

      {isSearching ? (
        <div aria-live="polite" aria-busy={isFetching}>
          {isFirstLoad ? (
            <p className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
              <Loader2 aria-hidden="true" className="h-4 w-4 shrink-0 animate-spin" />
              {SEARCH_COPY.searching}
            </p>
          ) : isError ? (
            <SearchFailure kind={searchErrorKind(error)} onRetry={() => void refetch()} />
          ) : (data?.items.length ?? 0) === 0 ? (
            <SearchEmptyState />
          ) : (
            <div className="space-y-4">
              <h2 className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm font-medium text-muted-foreground">
                <span>{searchResultsHeading(query)}</span>
                {/* Wave B added `total_count` to the hub envelope specifically so
                    this could name a number. It is null on browse and whenever
                    the backend skipped the count, so render only when present —
                    and let `searchResultCount` own both the pluralisation and
                    the «أفضل N» ceiling wording. */}
                {typeof data?.total_count === "number" && (
                  <span className="font-normal text-muted-foreground/70">
                    {searchResultCount(
                      data.total_count,
                      data.total_count_is_exact ?? true,
                    )}
                  </span>
                )}
              </h2>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                <HubCards
                  section={section}
                  items={data?.items ?? []}
                  sectorSlugs={sectorSlugs}
                />
              </div>
              {(data?.total_pages ?? 1) > 1 && (
                <p className="pt-1 text-center text-xs text-muted-foreground">
                  {SEARCH_COPY.moreResults}
                </p>
              )}
            </div>
          )}
        </div>
      ) : (
        children
      )}
    </div>
  );
}
