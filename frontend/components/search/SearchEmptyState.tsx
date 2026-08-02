import { SearchX } from "lucide-react";
import { cn } from "@/lib/utils";
import { SEARCH_COPY } from "@/lib/search/copy";

/**
 * «لا توجد نتائج» / «جرّب كلمات بحث أخرى» — the ONE empty state every search
 * surface renders (plan §6.1). Public hubs, «مكتبتي», المدونة and القوالب all
 * point here, so a reader who searches in two places is told the same thing the
 * same way.
 *
 * Presentational: props in, JSX out, no state and no data. It carries no
 * `"use client"` of its own — it is pulled into the client graph by whichever
 * live-search island renders it, and it works just as well inside a server
 * render (the `/library` cross-wing page, Wave E).
 *
 * ⚠ It says «try other words», NEVER «no such document exists». On a public hub
 * the corpus is only partly slugged (plan §2), so an empty result set is often
 * a reachability fact rather than a corpus fact — and the copy must not claim
 * otherwise.
 */
export function SearchEmptyState({
  title = SEARCH_COPY.emptyTitle,
  hint = SEARCH_COPY.emptyHint,
  className,
}: {
  title?: string;
  hint?: string;
  className?: string;
}) {
  return (
    <div
      dir="rtl"
      className={cn(
        "flex flex-col items-center justify-center gap-3 py-16 text-center",
        className,
      )}
    >
      <SearchX aria-hidden="true" className="h-10 w-10 text-muted-foreground/40" />
      <p className="text-sm font-medium text-muted-foreground">{title}</p>
      <p className="text-xs text-muted-foreground/70">{hint}</p>
    </div>
  );
}
