import Link from "next/link";
import { ChevronLeft } from "lucide-react";
import { SectorTypeCards } from "@/components/library/sectors/SectorTypeCards";
import {
  LIBRARY_TYPE_META,
  formatCount,
  type LibraryType,
} from "@/lib/library/sectors";
import type { SectorHubItem } from "@/lib/library/api";

/**
 * One preview strip: a type heading, ≤3 cards, and a «عرض الكل» link into that
 * type's full paginated list.
 *
 * Used twice with the same shape — unscoped on `/library` (linking into each
 * wing's own hub) and scoped on `/library/{sector}` (linking into
 * `/library/{sector}/{type}`). That symmetry is the reason `/library` shows
 * four strips rather than a merged cross-corpus feed: §9 established that the
 * four corpora share NO sortable column, so a merged feed has no defensible
 * ordering, while four strips need none.
 *
 * Renders NOTHING when the slice is empty — an empty type is not a heading with
 * a hole under it (D9).
 */
export function SectorPreviewStrip({
  type,
  items,
  count,
  href,
  sectorSlugs,
}: {
  type: LibraryType;
  items: SectorHubItem[];
  /** Total in this slice — shown on the «عرض الكل» link. */
  count: number;
  /** Where «عرض الكل» goes: the wing hub, or the sector×type list. */
  href: string;
  sectorSlugs?: Record<string, string>;
}) {
  if (items.length === 0) return null;

  const meta = LIBRARY_TYPE_META[type];

  return (
    <section dir="rtl" className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <div className="space-y-0.5">
          <h2 className="text-lg font-bold tracking-tight text-foreground">
            {meta.longLabel}
          </h2>
          <p className="text-xs leading-relaxed text-muted-foreground">
            {meta.description}
          </p>
        </div>

        <Link
          href={href}
          className="inline-flex shrink-0 items-center gap-1 text-sm font-medium text-primary transition-colors hover:text-primary-hover"
        >
          عرض الكل
          {count > 0 && (
            <span className="tabular-nums text-text-muted">
              ({formatCount(count)})
            </span>
          )}
          <ChevronLeft aria-hidden="true" className="h-4 w-4 shrink-0" />
        </Link>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <SectorTypeCards
          type={type}
          items={items.slice(0, 3)}
          sectorSlugs={sectorSlugs}
        />
      </div>
    </section>
  );
}
