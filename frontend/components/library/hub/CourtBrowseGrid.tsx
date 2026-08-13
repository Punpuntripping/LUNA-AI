import Link from "next/link";
import { cn } from "@/lib/utils";
import { formatCount } from "@/lib/library/sectors";
import { courtPath, type CourtNavItem } from "@/lib/library/courts";

/**
 * «الجهة القضائية» — the 12 court sections as plain `<Link>`s with their counts,
 * plus the «جميع الجهات» tile back to the unfiltered hub.
 *
 * ⚠ A GRID, NOT A CHIP ROW. `JudgmentsFilterBar`'s درجة المحكمة control is four
 * chips; twelve of these — several of them long («ديوان المظالم — الدائرة
 * التجارية») — would wrap into an unreadable slab and push the cards off the
 * screen. This copies `sectors/SectorBrowseGrid` instead, which is the pattern
 * that already carries 38 entries.
 *
 * ⚠ SERVER-RENDERED, ZERO CLIENT STATE. These links in the SSR HTML *are* the
 * second browse axis into the corpus, for readers and for the in-app crawl of a
 * `noindex` wing alike. A `<select>` or a portalled dropdown would render the
 * same thing to a human and nothing to anything else — the trap
 * `global_header.md` records. Verify with `view-source`, never devtools.
 *
 * ⚠ THE ORDER IS CORPUS VOLUME AND IS NEVER RE-SORTED — `courtNavItems()` owns
 * it (see `lib/library/courts.ts`). Alphabetical would bury المحكمة التجارية
 * (20,335 rows) under المحكمة العامة (69).
 *
 * `count === null` means the counts endpoint was unavailable, not zero: the tile
 * then renders without a number rather than asserting «0».
 */
export function CourtBrowseGrid({
  courts,
  activeSlug,
  className,
}: {
  courts: CourtNavItem[];
  /** The court section being viewed. Absent ⇒ the unfiltered /judgments hub. */
  activeSlug?: string;
  className?: string;
}) {
  if (courts.length === 0) return null;

  const tileClass = (isActive: boolean): string =>
    cn(
      "flex items-center justify-between gap-2 rounded-lg border px-3 py-2 transition-colors",
      isActive
        ? "border-primary/60 bg-primary/5"
        : "border-border bg-card hover:border-primary/40 hover:bg-accent/40",
    );

  return (
    <ul
      dir="rtl"
      className={cn(
        "grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3",
        className,
      )}
    >
      {/* The clear action for this axis — the counterpart of «الكل» on the
          court-level chips, and the only way back to the whole corpus from
          inside a section other than the breadcrumb. */}
      <li>
        <Link
          href="/judgments"
          aria-current={activeSlug ? undefined : "page"}
          className={tileClass(!activeSlug)}
        >
          <span className="truncate text-sm font-medium text-foreground">
            جميع الجهات القضائية
          </span>
        </Link>
      </li>

      {courts.map((court) => {
        const isActive = court.slug === activeSlug;
        return (
          <li key={court.slug}>
            <Link
              href={courtPath(court.slug)}
              aria-current={isActive ? "page" : undefined}
              className={tileClass(isActive)}
            >
              <span className="truncate text-sm font-medium text-foreground">
                {court.label}
              </span>
              {court.count !== null && (
                <span className="shrink-0 text-xs tabular-nums text-text-muted">
                  {formatCount(court.count)}
                </span>
              )}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
