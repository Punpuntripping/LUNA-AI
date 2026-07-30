"use client";

import { useRouter } from "next/navigation";
import { BookMarked, Lock } from "lucide-react";
import { cn } from "@/lib/utils";
import { useMyLibrary } from "@/hooks/use-my-library";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { MY_LIBRARY_COPY } from "@/components/library/mine/copy";
import type { MyLibraryRow } from "@/lib/api";

/**
 * «مكتبتي» in the sidebar — the shelf, one tab under «مدوناتي».
 *
 * Deliberately a THIN list, not a second implementation of the shelf: the full
 * surface (four tabs, sorting, nesting, the frozen upgrade CTA) lives at
 * `/library/mine`, and this panel is a recency peek into it plus a way in.
 * Mirrors `BlogList` — same SectionHeader, same skeleton, same empty state
 * shape — so the four sidebar tabs read as one component family.
 *
 * Sorted by recency and capped: a lawyer's shelf grows without bound, and a
 * sidebar that renders 400 rows to show the top 8 is a scroll trap.
 */

const SIDEBAR_SHELF_LIMIT = 8;

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-4 pt-3 pb-2 shrink-0">
      <p className="text-[11px] font-medium uppercase tracking-[0.2em] text-muted-foreground/60">
        {children}
      </p>
    </div>
  );
}

function ShelfSkeleton() {
  return (
    <div className="space-y-1.5 px-3 py-2">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="h-7 rounded-md bg-muted/40 animate-pulse" />
      ))}
    </div>
  );
}

function ShelfRow({ row }: { row: MyLibraryRow }) {
  const router = useRouter();
  const title = (row.title ?? "").trim() || MY_LIBRARY_COPY.untitled;

  // An unavailable item has no public page to open — render it as plain text
  // rather than a dead click target.
  const openable = row.is_available && !!row.url;

  return (
    <div
      role={openable ? "button" : undefined}
      tabIndex={openable ? 0 : undefined}
      className={cn(
        "group flex items-center gap-2 rounded-md px-3 py-2 transition-colors",
        openable
          ? "cursor-pointer hover:bg-accent/60 text-foreground"
          : "text-muted-foreground",
      )}
      onClick={() => openable && router.push(row.url!)}
      onKeyDown={(e) => {
        if (!openable) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          router.push(row.url!);
        }
      }}
      title={title}
    >
      {row.is_frozen ? (
        <Lock className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      ) : (
        <BookMarked className="h-3.5 w-3.5 shrink-0 text-muted-foreground/70" />
      )}
      <span className="flex-1 truncate text-sm">{title}</span>
    </div>
  );
}

export function LibraryShelfList() {
  const router = useRouter();
  const { data, isLoading } = useMyLibrary({
    contentType: null,
    sort: "recent",
    page: 1,
    pageSize: SIDEBAR_SHELF_LIMIT,
  });

  const rows = data?.items ?? [];

  let body: React.ReactNode;
  if (isLoading && rows.length === 0) {
    body = <ShelfSkeleton />;
  } else if (rows.length === 0) {
    body = (
      <div className="px-4 py-6 text-center">
        <p className="text-xs leading-relaxed text-muted-foreground">
          {MY_LIBRARY_COPY.emptyShelf}
        </p>
      </div>
    );
  } else {
    body = (
      <ScrollArea className="flex-1 min-h-0">
        <div className="px-2 pb-2 space-y-0.5">
          {rows.map((row) => (
            <ShelfRow key={`${row.content_type}:${row.content_id}`} row={row} />
          ))}
        </div>
      </ScrollArea>
    );
  }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <SectionHeader>{MY_LIBRARY_COPY.pageTitle}</SectionHeader>
      {body}
      <div className="px-2 pb-2 pt-1 shrink-0">
        <Button
          variant="ghost"
          className="w-full justify-center text-xs font-medium text-muted-foreground hover:text-foreground"
          onClick={() => router.push("/library/mine")}
          data-testid="sidebar-open-my-library"
        >
          {MY_LIBRARY_COPY.openFullShelf}
        </Button>
      </div>
    </div>
  );
}
