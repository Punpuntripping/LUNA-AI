import Link from "next/link";
import { cn } from "@/lib/utils";
import { formatCount, sectorPath } from "@/lib/library/sectors";
import type { SectorSummary } from "@/lib/library/api";

/**
 * «تصفّح حسب القطاع» — all 38 sectors as plain `<Link>`s with their totals
 * (§8.2).
 *
 * ⚠ SERVER-RENDERED, ZERO CLIENT STATE, AND THAT IS THE ENTIRE POINT. These 38
 * links in the SSR HTML *are* the crawl skeleton for the second axis into the
 * corpus. A `<select>`, a JS popover or a Radix dropdown would render the same
 * thing to a human and NOTHING to a crawler — the trap `global_header.md` hit
 * with portalled menus, and the reason `JudgmentsFilterBar` is built out of
 * links and a plain GET form. Verify with `view-source`, never devtools:
 * devtools shows the hydrated DOM, which is precisely the thing that lies here.
 *
 * ⚠ ORDER COMES FROM THE SERVER AND IS NEVER RE-SORTED. `/public/library/
 * sectors` already returns volume order, which is the browse order (§3):
 * alphabetical would bury المعاملات التجارية (20,182 items) under الأمن الغذائي.
 */
export function SectorBrowseGrid({
  sectors,
  activeSlug,
  className,
}: {
  sectors: SectorSummary[];
  /** The sector currently being viewed — marked, and not a link to itself. */
  activeSlug?: string;
  className?: string;
}) {
  if (sectors.length === 0) return null;

  return (
    <ul
      dir="rtl"
      className={cn(
        "grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4",
        className,
      )}
    >
      {sectors.map((sector) => {
        const isActive = sector.slug === activeSlug;
        return (
          <li key={sector.slug}>
            <Link
              href={sectorPath(sector.slug)}
              aria-current={isActive ? "page" : undefined}
              className={cn(
                "flex items-center justify-between gap-2 rounded-lg border px-3 py-2 transition-colors",
                isActive
                  ? "border-primary/60 bg-primary/5"
                  : "border-border bg-card hover:border-primary/40 hover:bg-accent/40",
              )}
            >
              <span className="truncate text-[13px] font-medium text-foreground">
                {sector.name_ar}
              </span>
              <span className="shrink-0 text-[11px] tabular-nums text-text-muted">
                {formatCount(sector.counts.total)}
              </span>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
