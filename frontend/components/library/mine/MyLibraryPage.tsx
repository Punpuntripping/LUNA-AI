"use client";

import { useState } from "react";
import { LibraryBig } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { useMyLibrary } from "@/hooks/use-my-library";
import type { MyLibraryContentType, MyLibrarySort } from "@/lib/api";
import { MyLibraryAuthGate } from "@/components/library/mine/MyLibraryAuthGate";
import { MyLibraryTabs } from "@/components/library/mine/MyLibraryTabs";
import { MyLibrarySortMenu } from "@/components/library/mine/MyLibrarySortMenu";
import { FrozenUpgradeCta } from "@/components/library/mine/FrozenUpgradeCta";
import { ShelfEntry } from "@/components/library/mine/ShelfEntry";
import { ShelfPagination } from "@/components/library/mine/ShelfPagination";
import { MY_LIBRARY_COPY } from "@/components/library/mine/copy";

/**
 * «مكتبتي» — the user's library shelf (access_tiers_gating.md PART 5B).
 *
 * A FILTERED HUB, not a new design system (§5B.1): every row renders with the
 * public hub card for its wing. What مكتبتي adds is shelf state — usage,
 * «حفظ», lock badges — around those cards.
 *
 * Client-rendered by construction: the endpoint is authed and `no-store`, and
 * nothing on this page may be produced by a cached server render (§5B.3).
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

  const { data, isLoading, isError, isFetching, refetch } = useMyLibrary({
    contentType: tab,
    sort,
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
        <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
          <LibraryBig aria-hidden="true" className="h-6 w-6 text-primary" />
          {MY_LIBRARY_COPY.pageTitle}
        </h1>
        <p className="text-sm leading-relaxed text-muted-foreground">
          {MY_LIBRARY_COPY.pageSubtitle}
        </p>
      </header>

      {/* §5B.4 — the frozen shelf is the conversion surface, never a hidden one. */}
      <div className="mt-5 space-y-4">
        <FrozenUpgradeCta frozenCount={data?.frozen_count ?? 0} />

        <div className="flex flex-wrap items-center justify-between gap-3">
          <MyLibraryTabs counts={counts} active={tab} onSelect={handleTab} />
          <MyLibrarySortMenu value={sort} onChange={handleSort} />
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
