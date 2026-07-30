"use client";

import Link from "next/link";
import { Layers } from "lucide-react";
import type { MyLibraryRow } from "@/lib/api";
import { ShelfCard } from "@/components/library/mine/ShelfCard";
import { ShelfMetaBar } from "@/components/library/mine/ShelfMetaBar";
import { NestedArticles } from "@/components/library/mine/NestedArticles";
import {
  MY_LIBRARY_COPY,
  articlesLabel,
} from "@/components/library/mine/copy";

/**
 * One entry in the «مكتبتي» grid: the hub card, its shelf caption, and — for a
 * نظام — the مواد nested under it.
 *
 * `is_shelf_row === false` is the one structurally different case: the نظام was
 * never opened or pinned itself and exists only to hold مواد the user shelved,
 * so it renders as a GROUP HEADER (dashed, no card chrome) rather than as a
 * saved card, which would claim a shelf state it does not have.
 */
export function ShelfEntry({ row }: { row: MyLibraryRow }) {
  const children = row.child_articles ?? [];
  const hasChildren = children.length > 0;

  // A نظام group is ranked by self + مواد (`group_*`), so the visible counter
  // must be the same number the ordering used — otherwise «الأكثر استخداماً»
  // looks wrong on screen.
  const useCount = hasChildren ? row.group_use_count : row.use_count;
  const lastUsedAt = hasChildren ? row.group_last_used_at : row.last_used_at;

  const articlesChip = hasChildren ? (
    <span className="inline-flex items-center gap-1 rounded-full bg-pill px-2 py-0.5 font-medium text-pill-fg">
      <Layers aria-hidden="true" className="h-3 w-3" />
      {articlesLabel(children.length)}
    </span>
  ) : null;

  if (row.content_type === "regulation" && !row.is_shelf_row) {
    return (
      <RegulationGroupHeader
        row={row}
        useCount={useCount}
        lastUsedAt={lastUsedAt}
        articlesChip={articlesChip}
      />
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1">
        <ShelfCard row={row} />
      </div>

      <ShelfMetaBar
        contentType={row.content_type}
        contentId={row.content_id}
        useCount={useCount}
        lastUsedAt={lastUsedAt}
        isSaved={row.source === "manual"}
        isFrozen={row.is_frozen}
        isAvailable={row.is_available}
        leading={articlesChip}
      />

      <NestedArticles articles={children} />
    </div>
  );
}

/**
 * A نظام the user never shelved directly, rendered as a header for the مواد
 * they did shelve. «حفظ» here is an ADD (the نظام itself joins the shelf), not
 * a toggle-off — `source` is null on a synthesized row, so the toggle starts
 * unsaved.
 */
function RegulationGroupHeader({
  row,
  useCount,
  lastUsedAt,
  articlesChip,
}: {
  row: MyLibraryRow;
  useCount: number;
  lastUsedAt: string | null;
  articlesChip: React.ReactNode;
}) {
  const title = row.title?.trim() || MY_LIBRARY_COPY.untitled;
  const linkable = row.is_available && !!row.url;

  return (
    <div
      dir="rtl"
      className="flex h-full flex-col rounded-xl border border-dashed border-border bg-surface-2/40 p-4 sm:p-5"
    >
      <span className="text-[11px] font-medium text-text-muted">
        {MY_LIBRARY_COPY.groupHeaderNote}
      </span>

      {linkable ? (
        <Link
          href={row.url as string}
          className="mt-1 line-clamp-2 text-base font-bold leading-snug text-foreground transition-colors hover:text-primary hover:underline"
        >
          {title}
        </Link>
      ) : (
        <h2 className="mt-1 line-clamp-2 text-base font-bold leading-snug text-muted-foreground">
          {title}
        </h2>
      )}

      <NestedArticles articles={row.child_articles ?? []} />

      <ShelfMetaBar
        className="mt-auto pt-2"
        contentType="regulation"
        contentId={row.content_id}
        useCount={useCount}
        lastUsedAt={lastUsedAt}
        isSaved={row.source === "manual"}
        isFrozen={row.is_frozen}
        isAvailable={row.is_available}
        leading={articlesChip}
      />
    </div>
  );
}
