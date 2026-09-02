import Link from "next/link";
import { cn } from "@/lib/utils";
import { formatCount } from "@/lib/library/sectors";
import { subjectPath } from "@/lib/blog/slug";
import type { BlogSubject } from "@/types";

/**
 * «المواضيع» — the browse axis of the public blog wing as a tile grid.
 *
 * Shared by the `/blog` hub (CAPPED to 12 by the caller, plan D13 + §12.1) and
 * the full `/blog/subjects` index (uncapped). Presentational and pure: the
 * caller owns the `>= 1` filter, the ordering and the cap, because those are
 * the same three decisions the sitemap makes and they must not be restated in
 * two places.
 *
 * ⚠ A GRID, NOT A CHIP ROW — the shape `EntityBrowseGrid` / `SectorBrowseGrid`
 * already carry. Subject labels are full Arabic phrases («نظام العمل»,
 * «سند الأمر»), and ~100 of them as chips wrap into an unreadable slab.
 *
 * ⚠ SERVER-RENDERED, ZERO CLIENT STATE. These links in the SSR HTML *are* the
 * browse tree, for readers and for crawlers alike. A portalled dropdown would
 * render the same thing to a human and nothing to anything else. Verify with
 * `view-source`, never devtools.
 *
 * Counts are Latin digits via `formatCount` — subject counts are app chrome,
 * not corpus text, so the numerals policy applies with no carve-out.
 */
export function SubjectGrid({
  subjects,
  activeSlug,
  moreHref,
  className,
}: {
  subjects: BlogSubject[];
  /** The subject being viewed, when the grid renders as a switcher. */
  activeSlug?: string;
  /** When set, a trailing «كل المواضيع» tile links to the full index. */
  moreHref?: string;
  className?: string;
}) {
  if (subjects.length === 0 && !moreHref) return null;

  const tileClass = (isActive: boolean): string =>
    cn(
      "flex h-full items-center justify-between gap-2 rounded-lg border px-3 py-2.5 transition-colors",
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
      {subjects.map((subject) => {
        const isActive = subject.slug === activeSlug;
        return (
          <li key={subject.slug}>
            <Link
              href={subjectPath(subject.slug)}
              aria-current={isActive ? "page" : undefined}
              className={tileClass(isActive)}
            >
              <span className="truncate text-sm font-medium text-foreground">
                {subject.label_ar}
              </span>
              <span className="shrink-0 text-xs tabular-nums text-text-muted">
                {formatCount(subject.blog_count)}
              </span>
            </Link>
          </li>
        );
      })}

      {moreHref && (
        <li>
          <Link href={moreHref} className={tileClass(false)}>
            <span className="truncate text-sm font-medium text-primary">
              كل المواضيع
            </span>
          </Link>
        </li>
      )}
    </ul>
  );
}
