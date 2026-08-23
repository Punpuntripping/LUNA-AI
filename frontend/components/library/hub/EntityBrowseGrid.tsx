import Link from "next/link";
import { cn } from "@/lib/utils";
import { formatCount } from "@/lib/library/sectors";
import { entityPath, type EntityNavItem } from "@/lib/library/entities";

/**
 * «الجهة» — the 28 entity sections as plain `<Link>`s with their counts, plus
 * the «جميع الجهات» tile back to the unfiltered hub.
 *
 * ⚠ A GRID, NOT A CHIP ROW. Twenty-eight entries, several of them long («وزارة
 * الموارد البشرية والتنمية الاجتماعية», «المركز الوطني للرقابة على الإلتزام
 * البيئي»), would wrap into an unreadable slab as chips and push the cards off
 * the screen. This is the same three-column tile grid `CourtBrowseGrid` and
 * `sectors/SectorBrowseGrid` use — the pattern that already carries 38 entries.
 *
 * ⚠ SERVER-RENDERED, ZERO CLIENT STATE. These links in the SSR HTML *are* the
 * second browse axis into the corpus, for readers and for crawlers alike — and
 * unlike the judgments switcher this wing is INDEXED (compliance_entity_sections
 * D1), so the crawler half is not theoretical. A `<select>` or a portalled
 * dropdown would render the same thing to a human and nothing to anything else,
 * the trap `global_header.md` records. Verify with `view-source`, never devtools.
 *
 * ⚠ THE ORDER IS CORPUS VOLUME AND IS NEVER RE-SORTED — `entityNavItems()` owns
 * it (see `lib/library/entities.ts`). Alphabetical would bury وزارة العدل (115
 * guides, a third of the wing) among the nine one-guide entities.
 *
 * `count === null` means the counts endpoint was unavailable, not zero: the tile
 * then renders without a number rather than asserting «0». Every one of the 28
 * has at least one guide, so «0» would always be a lie.
 */
export function EntityBrowseGrid({
  entities,
  activeSlug,
  className,
}: {
  entities: EntityNavItem[];
  /** The entity section being viewed. Absent ⇒ the unfiltered /compliance hub. */
  activeSlug?: string;
  className?: string;
}) {
  if (entities.length === 0) return null;

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
      {/* The clear action for this axis, and the only way back to the whole
          corpus from inside a section other than the breadcrumb. First tile,
          before any entity. */}
      <li>
        <Link
          href="/compliance"
          aria-current={activeSlug ? undefined : "page"}
          className={tileClass(!activeSlug)}
        >
          <span className="truncate text-sm font-medium text-foreground">
            جميع الجهات
          </span>
        </Link>
      </li>

      {entities.map((entity) => {
        const isActive = entity.slug === activeSlug;
        return (
          <li key={entity.slug}>
            <Link
              href={entityPath(entity.slug)}
              aria-current={isActive ? "page" : undefined}
              className={tileClass(isActive)}
            >
              <span className="truncate text-sm font-medium text-foreground">
                {entity.label}
              </span>
              {entity.count !== null && (
                <span className="shrink-0 text-xs tabular-nums text-text-muted">
                  {formatCount(entity.count)}
                </span>
              )}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
