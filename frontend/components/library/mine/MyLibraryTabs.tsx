"use client";

import { cn } from "@/lib/utils";
import type { MyLibraryContentType, MyLibraryResponse } from "@/lib/api";
import { MY_LIBRARY_COPY } from "@/components/library/mine/copy";

/**
 * The four DEFAULT tabs, in the §5B.1 order. They render even when empty —
 * they are the shape of the shelf, not a filtered result set.
 *
 * `article` is deliberately absent: مواد nest under their parent نظام inside
 * the الأنظمة tab and are never a tab of their own ("a مادة without its statute
 * reads as an orphan"). Its rows are therefore counted INTO الأنظمة.
 */
const DEFAULT_TABS = [
  "regulation",
  "judgment",
  "service",
  "circular",
] as const satisfies readonly MyLibraryContentType[];

/** Shown only when non-empty (§5B.1). */
const SECONDARY_TABS = ["form", "calculator"] as const satisfies readonly MyLibraryContentType[];

type ShelfCounts = MyLibraryResponse["counts"];

/** Rows a tab covers: الأنظمة owns the nested مواد too. */
export function tabCount(
  counts: ShelfCounts,
  contentType: MyLibraryContentType,
): number {
  const own = counts[contentType] ?? 0;
  if (contentType === "regulation") return own + (counts.article ?? 0);
  return own;
}

export function MyLibraryTabs({
  counts,
  active,
  onSelect,
}: {
  counts: ShelfCounts;
  active: MyLibraryContentType;
  onSelect: (contentType: MyLibraryContentType) => void;
}) {
  const visible: MyLibraryContentType[] = [
    ...DEFAULT_TABS,
    ...SECONDARY_TABS.filter((tab) => tabCount(counts, tab) > 0),
  ];

  return (
    <div
      dir="rtl"
      role="tablist"
      aria-label={MY_LIBRARY_COPY.pageTitle}
      className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1"
    >
      {visible.map((tab) => {
        const isActive = tab === active;
        const count = tabCount(counts, tab);
        return (
          <button
            key={tab}
            type="button"
            role="tab"
            aria-selected={isActive}
            onClick={() => onSelect(tab)}
            className={cn(
              "inline-flex shrink-0 items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-sm font-medium transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              isActive
                ? "border-primary/60 bg-primary/5 text-foreground"
                : "border-transparent text-muted-foreground hover:bg-accent/50 hover:text-foreground",
            )}
          >
            <span>{MY_LIBRARY_COPY.tabs[tab]}</span>
            {count > 0 && (
              <span
                className={cn(
                  "text-xs font-medium tabular-nums",
                  isActive ? "text-primary" : "text-muted-foreground/80",
                )}
              >
                {count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
