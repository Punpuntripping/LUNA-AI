"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  MY_LIBRARY_COPY,
  pageIndicator,
} from "@/components/library/mine/copy";

/**
 * Client pagination for the shelf.
 *
 * The public hubs use `HubPagination`, which builds crawlable
 * `/section/page/{n}` LINKS — deliberately, for SEO. مكتبتي is authed,
 * per-user and `no-store`: there is nothing to crawl and no page to link, so
 * the state stays in the component and this is a pair of buttons.
 *
 * RTL: reading flows right→left, so «السابق» (chevron pointing right, back
 * toward lower pages) sits first — same convention as HubPagination.
 */
export function ShelfPagination({
  page,
  totalPages,
  onChange,
  disabled,
}: {
  page: number;
  totalPages: number;
  onChange: (page: number) => void;
  disabled?: boolean;
}) {
  if (totalPages <= 1) return null;

  const pill =
    "inline-flex h-9 items-center justify-center gap-1 rounded-lg border border-border bg-card px-3 text-sm font-medium text-text-secondary shadow-xs transition-all hover:border-primary/40 hover:text-primary disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:border-border disabled:hover:text-text-secondary";

  return (
    <nav
      dir="rtl"
      aria-label="ترقيم الصفحات"
      className="mt-8 flex flex-wrap items-center justify-center gap-3"
    >
      <button
        type="button"
        className={cn(pill)}
        onClick={() => onChange(page - 1)}
        disabled={disabled || page <= 1}
      >
        <ChevronRight aria-hidden="true" className="h-4 w-4" />
        {MY_LIBRARY_COPY.previousPage}
      </button>

      <span className="text-sm tabular-nums text-muted-foreground">
        {pageIndicator(page, totalPages)}
      </span>

      <button
        type="button"
        className={cn(pill)}
        onClick={() => onChange(page + 1)}
        disabled={disabled || page >= totalPages}
      >
        {MY_LIBRARY_COPY.nextPage}
        <ChevronLeft aria-hidden="true" className="h-4 w-4" />
      </button>
    </nav>
  );
}
