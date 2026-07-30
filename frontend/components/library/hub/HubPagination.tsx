import Link from "next/link";
import { ChevronRight, ChevronLeft } from "lucide-react";
import { cn } from "@/lib/utils";

interface HubPaginationProps {
  /** Section base path, e.g. "/regulations". Page 1 lives at the base. */
  basePath: string;
  currentPage: number;
  totalPages: number;
  /**
   * Already-encoded query string (no leading "?") to carry across page links —
   * a hub's active filters, e.g. `court_level=appeal&q=إيجار`. Optional and
   * backward-compatible: unfiltered hubs omit it and links stay bare paths.
   */
  query?: string;
}

const WINDOW = 2;

/**
 * Path-based hub pagination («السابق / التالي» + a numbered window with first/
 * last jumps). Page 1 → `basePath`; deeper pages → `${basePath}/page/{n}`
 * (crawlable, ISR-friendly, self-canonical). Server component — links only.
 *
 * RTL note: reading flows right→left, so «السابق» (chevron pointing right, back
 * toward lower pages) sits first and «التالي» (chevron left) sits last.
 */
export function HubPagination({
  basePath,
  currentPage,
  totalPages,
  query,
}: HubPaginationProps) {
  if (totalPages <= 1) return null;

  const suffix = query ? `?${query}` : "";
  const hrefFor = (page: number): string =>
    page <= 1 ? `${basePath}${suffix}` : `${basePath}/page/${page}${suffix}`;

  const start = Math.max(1, currentPage - WINDOW);
  const end = Math.min(totalPages, currentPage + WINDOW);
  const windowPages: number[] = [];
  for (let page = start; page <= end; page += 1) windowPages.push(page);

  const pillBase =
    "inline-flex h-10 min-w-10 items-center justify-center gap-1 rounded-lg border px-3 text-sm font-medium transition-all";
  const pillIdle =
    "border-border bg-card text-text-secondary shadow-xs hover:-translate-y-0.5 hover:border-primary/40 hover:text-primary hover:shadow-sm";

  return (
    <nav
      dir="rtl"
      aria-label="ترقيم الصفحات"
      className="mt-8 flex flex-wrap items-center justify-center gap-1.5"
    >
      {currentPage > 1 && (
        <Link
          href={hrefFor(currentPage - 1)}
          rel="prev"
          className={cn(pillBase, pillIdle)}
        >
          <ChevronRight aria-hidden="true" className="h-4 w-4" />
          السابق
        </Link>
      )}

      {start > 1 && (
        <>
          <Link
            href={hrefFor(1)}
            className={cn(pillBase, pillIdle)}
          >
            1
          </Link>
          {start > 2 && (
            <span className="px-1 text-sm text-muted-foreground">…</span>
          )}
        </>
      )}

      {windowPages.map((page) =>
        page === currentPage ? (
          <span
            key={page}
            aria-current="page"
            className={cn(
              pillBase,
              "border-primary bg-primary font-bold text-primary-foreground shadow-sm ring-2 ring-primary/20",
            )}
          >
            {page}
          </span>
        ) : (
          <Link
            key={page}
            href={hrefFor(page)}
            className={cn(pillBase, pillIdle)}
          >
            {page}
          </Link>
        ),
      )}

      {end < totalPages && (
        <>
          {end < totalPages - 1 && (
            <span className="px-1 text-sm text-muted-foreground">…</span>
          )}
          <Link
            href={hrefFor(totalPages)}
            className={cn(pillBase, pillIdle)}
          >
            {totalPages}
          </Link>
        </>
      )}

      {currentPage < totalPages && (
        <Link
          href={hrefFor(currentPage + 1)}
          rel="next"
          className={cn(pillBase, pillIdle)}
        >
          التالي
          <ChevronLeft aria-hidden="true" className="h-4 w-4" />
        </Link>
      )}
    </nav>
  );
}
