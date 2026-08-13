"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { usePathname } from "next/navigation";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { useDebounce } from "@/hooks/use-debounce";
import { useAuthStore } from "@/stores/auth-store";
import { SearchBar } from "@/components/search/SearchBar";
import { SearchEmptyState } from "@/components/search/SearchEmptyState";
import { SearchFailure } from "@/components/search/SearchFailure";
import { LibrarySearchResultRow } from "@/components/library/search/LibrarySearchResultRow";
import { searchErrorKind, useLibrarySearch } from "@/hooks/use-search";
import {
  SEARCH_COPY,
  SEARCH_DEBOUNCE_MS,
  SEARCH_LIBRARY_COPY,
  SEARCH_MIN_LENGTH,
  searchResultCount,
  searchResultsHeading,
} from "@/lib/search/copy";
import {
  SEARCH_CORPORA,
  corpusLabel,
  parseCorpora,
  toggleCorpus,
  writeCorpora,
  type SearchCorpus,
} from "@/lib/search/corpora";

/**
 * The cross-wing search on `/library` (D5) — one box over أنظمة + أحكام +
 * تعاميم + خدمات, the wing chips that scope it, and the results.
 *
 * ── THE `/library` PLAN CONFLICT, AND HOW IT IS RESOLVED ────────────────────
 * Two plans claim this route. `bm25_navigation_search.md` D5 says «`/library`
 * becomes a real cross-wing search page, replacing the `ComingSoonHub`
 * placeholder». `library_sectors.md` D1/D2 says `/library` is the browsable
 * sector hub — four type chips, the 38-link sector grid, four preview strips —
 * and that plan SHIPPED FIRST: the placeholder D5 was written against no longer
 * exists.
 *
 * They coexist, and the reason they must is D9. Search is registered-only, so a
 * `/library` that were *only* a search box would be a blank page for every
 * anonymous visitor and every crawler — on the one route that carries the sole
 * crawlable path into all 38 sector pages and deliberately dropped its
 * `robots: noindex` to say so. So search becomes the TOP AFFORDANCE ABOVE a page
 * that is still fully worth landing on signed out, rather than a replacement
 * for it.
 *
 * ── WHY IT SWAPS THE BODY RATHER THAN APPENDING TO IT ───────────────────────
 * Identical contract to `HubSearchPanel`: the hub's own body arrives as
 * `children` — a SERVER-rendered subtree handed to a client island, which costs
 * nothing and keeps `LibraryHubView` a server component. While a search is live
 * the children are not rendered; clearing the box brings them straight back,
 * already rendered, with no refetch. The alternative (results below the browse
 * grid) would put a reader's answer under four preview strips and a 38-cell
 * grid, and would leave TWO chip rows on screen naming the same four wings.
 *
 * ── WHY THE SEARCH IS CLIENT-SIDE ──────────────────────────────────────────
 * `/library`'s server render is anonymous and ISR-cached under a shared key, and
 * `/api/v1/search` is `private, no-store` and metered per caller. A result set
 * written into that cache would be replayed to every visitor for the next hour,
 * charged to the wrong budget. Per-user bytes reach the browser through a
 * client-side authed fetch and nowhere else. Nothing in this file may move into
 * `lib/library/api.ts`.
 *
 * ── `?q=` / `?corpus=` ON THE URL ──────────────────────────────────────────
 * Mirrored with `history.replaceState`, NOT a router navigation and NOT
 * `useSearchParams()`. `app/library/page.tsx` must come out of `next build`
 * STATIC (library_sectors §12.8) — reading `searchParams` there would opt the
 * whole anon-serving hub out of static generation, and `useSearchParams()` would
 * drag it into a Suspense boundary for the same gain of nothing. Those URLs are
 * `noindex` via the `X-Robots-Tag` rule in `middleware.ts`, which already covers
 * any `?q=` URL on any route — no page-level `robots` is added here, and none
 * should be.
 */
export function LibrarySearchPanel({
  sectorSlugs,
  children,
}: {
  /** `name_ar → slug` for the sector pills on result rows (D11). */
  sectorSlugs?: Record<string, string>;
  /** The browsable hub: type chips, sector grid, preview strips. */
  children: ReactNode;
}) {
  const pathname = usePathname();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  const [rawQuery, setRawQuery] = useState("");
  /** Selected wings. EMPTY = «الكل» = the endpoint's own all-four default. */
  const [corpora, setCorpora] = useState<SearchCorpus[]>([]);
  /** The live query string on the address bar, kept for the CTA's `?next=`. */
  const [urlSearch, setUrlSearch] = useState("");
  /**
   * Has the reader touched anything? Until they have, the URL is the source of
   * truth and must not be rewritten — a blind sync on mount would strip the very
   * `?q=` we are about to seed FROM it, and a shared search link would erase
   * itself in front of the person who opened it.
   */
  const userTouchedRef = useRef(false);

  // Seed from a shared link. Read in an effect, not during render: `window` does
  // not exist server-side, and `useSearchParams()` is off the table for this
  // route (see the header note).
  useEffect(() => {
    const search = window.location.search;
    setUrlSearch(search);

    const params = new URLSearchParams(search);
    const seededQuery = params.get("q") ?? "";
    if (seededQuery.trim().length > 0) setRawQuery(seededQuery);

    const seededCorpora = parseCorpora(params);
    // A link naming ALL four wings means the same thing as naming none, and the
    // empty state is the one this UI renders as «الكل». Normalising on the way
    // in stops a shared link from painting four "active" chips and no «الكل».
    if (
      seededCorpora.length > 0 &&
      seededCorpora.length < SEARCH_CORPORA.length
    ) {
      setCorpora(seededCorpora);
    }
  }, []);

  const query = useDebounce(rawQuery, SEARCH_DEBOUNCE_MS).trim();
  // A stable dependency for the mirror effect: the state is an array, so its
  // identity changes on every toggle even when the selection does not.
  const corporaKey = corpora.join(",");

  // Mirror the debounced query + the wing selection onto the URL. `replaceState`,
  // not `pushState`: one history entry per keystroke would make the back button
  // unusable, and the reader's way "back" from a search is the box's own ×.
  useEffect(() => {
    if (!userTouchedRef.current) return;
    const params = new URLSearchParams(window.location.search);
    if (query.length > 0) params.set("q", query);
    else params.delete("q");
    writeCorpora(params, corporaKey ? corporaKey.split(",") : []);

    const serialized = params.toString();
    const nextSearch = serialized ? `?${serialized}` : "";
    if (nextSearch === window.location.search) return;
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}${nextSearch}`,
    );
    setUrlSearch(nextSearch);
  }, [query, corporaKey]);

  const handleQueryChange = useCallback((value: string) => {
    userTouchedRef.current = true;
    setRawQuery(value);
  }, []);

  const handleToggleCorpus = useCallback((corpus: SearchCorpus) => {
    userTouchedRef.current = true;
    setCorpora((current) => toggleCorpus(current, corpus));
  }, []);

  const handleSelectAllCorpora = useCallback(() => {
    userTouchedRef.current = true;
    setCorpora([]);
  }, []);

  const isSearching = isAuthenticated && query.length >= SEARCH_MIN_LENGTH;
  const { data, isFetching, isError, error, refetch } = useLibrarySearch({
    query,
    corpora,
    enabled: isAuthenticated,
  });

  const items = data?.items ?? [];
  // Nothing has come back for THIS query yet — the first load, or a wing change
  // that invalidated the key. `keepPreviousData` means `data` may still hold the
  // previous set, which is what keeps the list on screen instead of flashing a
  // spinner between keystrokes.
  const isFirstLoad = isSearching && isFetching && !data;

  return (
    <div dir="rtl" className="space-y-6">
      <div className="space-y-2">
        {/* Wider than the hub boxes and deliberately so: on the four wings the
            box is one control in a filter row, here it is the page's primary
            affordance. `sm:w-full` overrides `SearchBar`'s own `sm:w-72`
            (tailwind-merge resolves the same modifier+property), and the cap
            keeps the line length readable on a desktop. */}
        <SearchBar
          value={rawQuery}
          onChange={handleQueryChange}
          placeholder={SEARCH_LIBRARY_COPY.placeholder}
          ariaLabel={SEARCH_LIBRARY_COPY.ariaLabel}
          isPending={isSearching && isFetching}
          // The gate is what makes an anonymous click a conversion instead of a
          // dead box (D9). `returnTo` carries the CURRENT query string — query
          // and wing selection both — so a shared search link survives the round
          // trip through signup and lands back on the results it promised.
          gate={{ returnTo: `${pathname}${urlSearch}` }}
          // No `minLength`: this IS a BM25 surface and takes the default 3, the
          // same number `search_service.normalize_query` enforces with a 400.
          className="sm:w-full max-w-2xl"
        />

        {!isSearching && (
          <p className="max-w-2xl text-xs leading-relaxed text-muted-foreground">
            {SEARCH_LIBRARY_COPY.lead}
          </p>
        )}
      </div>

      {isSearching ? (
        <div className="space-y-5">
          <CorpusChips
            selected={corpora}
            onToggle={handleToggleCorpus}
            onSelectAll={handleSelectAllCorpora}
          />

          <div aria-live="polite" aria-busy={isFetching}>
            {isFirstLoad ? (
              <p className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
                <Loader2
                  aria-hidden="true"
                  className="h-4 w-4 shrink-0 animate-spin"
                />
                {SEARCH_COPY.searching}
              </p>
            ) : isError ? (
              <SearchFailure
                kind={searchErrorKind(error)}
                onRetry={() => void refetch()}
              />
            ) : items.length === 0 ? (
              <SearchEmptyState />
            ) : (
              <div className="space-y-4">
                <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                  <h2 className="text-sm font-medium text-muted-foreground">
                    {searchResultsHeading(query)}
                  </h2>
                  {/* Exact when the candidate cut did not bind, «أفضل N نتيجة»
                      when it did — the ceiling told honestly rather than dressed
                      up as a total. See `searchResultCount`. */}
                  <span className="text-xs tabular-nums text-text-muted">
                    {searchResultCount(
                      data?.total ?? items.length,
                      data?.total_is_exact ?? true,
                    )}
                  </span>
                </div>

                {/* A LIST, not a card grid. The hits are ranked across wings, so
                    reading order is the ranking — a 3-column grid would scatter
                    rank 1..9 across two axes and lose the one thing BM25 gives
                    this page. */}
                <ul className="space-y-3">
                  {items.map((hit) => (
                    <LibrarySearchResultRow
                      key={`${hit.corpus}:${hit.content_id}`}
                      hit={hit}
                      sectorSlugs={sectorSlugs}
                    />
                  ))}
                </ul>

                {/* There is no paginator on purpose (see
                    `LIBRARY_SEARCH_PAGE_SIZE`): «add another word» is the right
                    answer to a 200-hit navigation search, and the endpoint
                    returns nothing past offset 200 anyway. */}
                {(data?.total ?? 0) > items.length && (
                  <p className="pt-1 text-center text-xs text-muted-foreground">
                    {SEARCH_COPY.moreResults}
                  </p>
                )}
              </div>
            )}
          </div>
        </div>
      ) : (
        children
      )}
    </div>
  );
}

/**
 * «نطاق البحث» — the wing chips that drive the repeatable `corpus` param.
 *
 * ⚠ BUTTONS, NOT LINKS, and this is the one place in the library where that is
 * the right call. Every other filter row on a public hub is built from `<Link>`s
 * precisely so a crawler can walk it (`SectorBrowseGrid`, `CourtLevelChips`).
 * Here the thing being filtered is registered-only client state: a crawlable
 * `/library?q=…&corpus=judgment` URL would render an anonymous visitor the
 * unfiltered sector hub — a link that promises a filtered result set and
 * delivers a different page. The selection still reaches the URL through
 * `replaceState`, so a result set stays shareable; it just is not a crawl edge.
 *
 * Multi-select, because the param is repeatable: a reader may want أنظمة +
 * أحكام without تعاميم. «الكل» is the empty selection, not a fifth value.
 */
function CorpusChips({
  selected,
  onToggle,
  onSelectAll,
}: {
  selected: readonly SearchCorpus[];
  onToggle: (corpus: SearchCorpus) => void;
  onSelectAll: () => void;
}) {
  const allActive = selected.length === 0;

  return (
    <div
      role="group"
      aria-label={SEARCH_LIBRARY_COPY.scopeLabel}
      className="flex flex-wrap items-center gap-1.5"
    >
      <Chip active={allActive} onClick={onSelectAll}>
        {SEARCH_LIBRARY_COPY.scopeAll}
      </Chip>
      {SEARCH_CORPORA.map((corpus) => (
        <Chip
          key={corpus}
          active={selected.includes(corpus)}
          onClick={() => onToggle(corpus)}
        >
          {corpusLabel(corpus)}
        </Chip>
      ))}
    </div>
  );
}

/**
 * One chip. Same chrome as `JudgmentsFilterBar`'s court-level row so the two
 * filter vocabularies look like one system — `aria-pressed` rather than
 * `aria-current` because these toggle rather than navigate.
 */
function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "inline-flex h-8 items-center rounded-full border px-3 text-sm font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        active
          ? "border-primary bg-primary text-primary-foreground shadow-xs"
          : "border-border bg-card text-text-secondary hover:border-primary/40 hover:text-primary",
      )}
    >
      {children}
    </button>
  );
}
