"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Compass, LibraryBig } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button, buttonVariants } from "@/components/ui/button";
import { useMyLibrary } from "@/hooks/use-my-library";
import { useSearchQuery } from "@/hooks/use-search";
import type { MyLibraryContentType, MyLibrarySort } from "@/lib/api";
import { MyLibraryAuthGate } from "@/components/library/mine/MyLibraryAuthGate";
import { MyLibraryTabs } from "@/components/library/mine/MyLibraryTabs";
import { MyLibrarySortMenu } from "@/components/library/mine/MyLibrarySortMenu";
import { FrozenUpgradeCta } from "@/components/library/mine/FrozenUpgradeCta";
import { ShelfEntry } from "@/components/library/mine/ShelfEntry";
import { ShelfPagination } from "@/components/library/mine/ShelfPagination";
import { MY_LIBRARY_COPY } from "@/components/library/mine/copy";
import { SearchBar } from "@/components/search/SearchBar";
import { SearchEmptyState } from "@/components/search/SearchEmptyState";
import {
  SEARCH_PRIVATE_COPY,
  SEARCH_RELEVANCE_NOTE,
} from "@/lib/search/copy";

/**
 * «مكتبتي» — the user's library shelf (access_tiers_gating.md PART 5B).
 *
 * A FILTERED HUB, not a new design system (§5B.1): every row renders with the
 * public hub card for its wing. What مكتبتي adds is shelf state — usage,
 * «حفظ», lock badges — around those cards.
 *
 * Client-rendered by construction: the endpoint is authed and `no-store`, and
 * nothing on this page may be produced by a cached server render (§5B.3).
 *
 * ── SEARCH (bm25_navigation_search.md Wave D) ───────────────────────────────
 * The box sits in the filter row beside the existing tabs and sort menu, and it
 * searches the SHELF — «رتّب لي ما فتحته», not the whole library. Server-side
 * that is "rank the public corpora, keep what is on this shelf", which is why a
 * shelf مادة matches through its parent نظام (the same way the shelf displays
 * it) and why نماذج/الحاسبات never match at all (D6/D7 — not indexed).
 *
 * `SearchBar` takes NO `gate`: that prop is D9's anonymous conversion modal and
 * this page is behind `MyLibraryAuthGate` before it ever renders. And no
 * highlighting anywhere (D3) — every row still renders through `ShelfEntry`,
 * i.e. the public hub card for its wing, with the static free snippet it
 * already carried.
 */
export function MyLibraryPage() {
  return (
    <MyLibraryAuthGate>
      <MyLibraryShelf />
    </MyLibraryAuthGate>
  );
}

function ShelfSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="h-44 animate-pulse rounded-xl bg-muted/40" />
      ))}
    </div>
  );
}

function MyLibraryShelf() {
  const [tab, setTab] = useState<MyLibraryContentType>("regulation");
  const [sort, setSort] = useState<MyLibrarySort>("recent");
  const [page, setPage] = useState(1);
  const { value, setValue, query, isSearching } = useSearchQuery();

  // A new query re-ranks the whole shelf, so page 4 of the previous list is
  // meaningless against it — and worse, it is usually past the end, which shows
  // an empty grid and reads as "no matches". Same reset the tab and sort
  // handlers already do, but driven by an effect because the query changes on a
  // debounce timer rather than on a click.
  useEffect(() => {
    setPage(1);
  }, [query]);

  const { data, isLoading, isError, isFetching, refetch } = useMyLibrary({
    contentType: tab,
    sort,
    q: query,
    page,
  });

  const counts = data?.counts ?? {};
  // The WHOLE shelf, across every type — «لم تفتح أي مصدر بعد» is only true
  // when nothing at all has been shelved, never when one tab happens to be
  // empty.
  const shelfSize = Object.values(counts).reduce(
    (total, n) => total + (n ?? 0),
    0,
  );
  const items = data?.items ?? [];

  const handleTab = (next: MyLibraryContentType) => {
    setTab(next);
    setPage(1);
  };

  const handleSort = (next: MyLibrarySort) => {
    setSort(next);
    setPage(1);
  };

  return (
    <main
      dir="rtl"
      className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 sm:py-10"
    >
      <header className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
            <LibraryBig aria-hidden="true" className="h-6 w-6 text-primary" />
            {MY_LIBRARY_COPY.pageTitle}
          </h1>

          {/* مكتبتي ⇄ /library. The shelf is closed by construction — it holds
              only what this user already used — so without this the only way
              back to the corpus is the nav dropdown. Its twin lives on the
              public hub (`ShelfLink`). */}
          <Link
            href="/library"
            className={cn(
              buttonVariants({ variant: "outline", size: "sm" }),
              "gap-1.5",
            )}
          >
            <Compass aria-hidden="true" className="h-4 w-4 shrink-0" />
            {MY_LIBRARY_COPY.browsePublicLibrary}
          </Link>
        </div>
        <p className="text-sm leading-relaxed text-muted-foreground">
          {MY_LIBRARY_COPY.pageSubtitle}
        </p>
      </header>

      {/* §5B.4 — the frozen shelf is the conversion surface, never a hidden one. */}
      <div className="mt-5 space-y-4">
        <FrozenUpgradeCta frozenCount={data?.frozen_count ?? 0} />

        <div className="flex flex-wrap items-center justify-between gap-3">
          <MyLibraryTabs counts={counts} active={tab} onSelect={handleTab} />

          {/* `w-full sm:w-auto` is load-bearing: `SearchBar`'s wrapper is
              `w-full` on mobile, and a percentage width inside a shrink-to-fit
              flex item resolves against a container that is itself sized by its
              content. Pinning the row to the full width on mobile makes the
              result deterministic — box on one line, «الترتيب» wrapped under
              it — instead of browser-dependent. */}
          <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
            {/* The box renders whenever the shelf holds anything at all.
                `shelfSize` is derived from `counts`, which the backend keeps
                WHOLE-SHELF during a search precisely so the chrome does not
                empty out with the results — `items.length` is the filtered
                count and would make the one control that undoes a zero-result
                search vanish along with the results it produced. */}
            {(shelfSize > 0 || isSearching) && (
              <SearchBar
                value={value}
                onChange={setValue}
                placeholder={SEARCH_PRIVATE_COPY.myLibrary.placeholder}
                ariaLabel={SEARCH_PRIVATE_COPY.myLibrary.ariaLabel}
                isPending={isSearching && isFetching}
                className="sm:w-56"
              />
            )}

            {/* «الترتيب» is hidden, not disabled, while a search is live: the
                backend REPLACES `sort` with the BM25 ranking, so a menu still
                reading «الأحدث» would be describing an order that is not in
                effect. The note takes its place so the control does not simply
                vanish unexplained. */}
            {isSearching ? (
              <span className="text-xs text-text-muted">
                {SEARCH_RELEVANCE_NOTE}
              </span>
            ) : (
              <MyLibrarySortMenu value={sort} onChange={handleSort} />
            )}
          </div>
        </div>
      </div>

      <div className="mt-5" aria-busy={isFetching}>
        {isLoading ? (
          <ShelfSkeleton />
        ) : isError ? (
          <div className="flex flex-col items-center gap-3 py-16 text-center">
            <p className="text-sm text-muted-foreground">
              {MY_LIBRARY_COPY.loadError}
            </p>
            <Button variant="outline" size="sm" onClick={() => void refetch()}>
              {MY_LIBRARY_COPY.retry}
            </Button>
          </div>
        ) : items.length === 0 && isSearching ? (
          // A search that matched nothing is «جرّب كلمات بحث أخرى» — NEVER
          // «لم تفتح أي مصدر بعد», which would tell someone with a full shelf
          // that they have none, nor «لا توجد أنظمة في مكتبتك بعد», which would
          // be a claim about the tab rather than about the query.
          <SearchEmptyState />
        ) : items.length === 0 ? (
          <div className="space-y-1 py-16 text-center">
            <p className="text-sm text-muted-foreground">
              {shelfSize === 0
                ? MY_LIBRARY_COPY.emptyShelf
                : MY_LIBRARY_COPY.emptyTab[tab]}
            </p>
            {shelfSize > 0 && (
              <p className="text-xs text-text-muted">
                {MY_LIBRARY_COPY.emptyTabHint}
              </p>
            )}
          </div>
        ) : (
          <div
            className={cn(
              "grid grid-cols-1 gap-x-4 gap-y-6 transition-opacity sm:grid-cols-2 lg:grid-cols-3",
              isFetching && "opacity-60",
            )}
          >
            {items.map((row) => (
              <ShelfEntry
                key={`${row.content_type}:${row.content_id}`}
                row={row}
              />
            ))}
          </div>
        )}
      </div>

      <ShelfPagination
        page={data?.page ?? page}
        totalPages={data?.total_pages ?? 1}
        onChange={setPage}
        disabled={isFetching}
      />
    </main>
  );
}
