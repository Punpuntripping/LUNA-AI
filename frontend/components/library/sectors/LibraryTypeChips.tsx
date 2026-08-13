import Link from "next/link";
import { cn } from "@/lib/utils";
import {
  LIBRARY_TYPES,
  LIBRARY_TYPE_META,
  formatCount,
  sectorTypePath,
  type LibraryType,
} from "@/lib/library/sectors";

/**
 * The four tab chips with their counts — «مكتبتي»'s `MyLibraryTabs`, rebuilt as
 * a SERVER component (§8.1).
 *
 * The shelf's version is a `useState` tablist because its data is authed and
 * client-fetched. The public one cannot be: tab content behind JS is indexed
 * unreliably (D7), so every tab here is a real `<Link>` to a real URL and the
 * "active" tab is just the page you are on.
 *
 * Two modes, one component:
 *   · `sectorSlug` set   → chips link to `/library/{sector}/{type}`, and a tab
 *                          with ZERO items is not rendered at all (D9 — an
 *                          empty combination is not a page).
 *   · `sectorSlug` unset → chips link to each wing's own hub (`/regulations`,
 *                          …), which already owns unfiltered deep pagination
 *                          and is already indexed. All four always render.
 */
export function LibraryTypeChips({
  counts,
  active,
  sectorSlug,
  className,
}: {
  counts: Partial<Record<LibraryType, number>>;
  active?: LibraryType;
  sectorSlug?: string;
  className?: string;
}) {
  const visible = LIBRARY_TYPES.filter(
    (type) => !sectorSlug || (counts[type] ?? 0) > 0,
  );

  if (visible.length === 0) return null;

  return (
    <ul
      dir="rtl"
      className={cn("-mx-1 flex flex-wrap gap-1.5 px-1", className)}
    >
      {visible.map((type) => {
        const meta = LIBRARY_TYPE_META[type];
        const count = counts[type] ?? 0;
        const isActive = type === active;
        const href = sectorSlug
          ? sectorTypePath(sectorSlug, type)
          : meta.wingPath;

        return (
          <li key={type}>
            <Link
              href={href}
              aria-current={isActive ? "page" : undefined}
              className={cn(
                "inline-flex shrink-0 items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-sm font-medium transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                isActive
                  ? "border-primary/60 bg-primary/5 text-foreground"
                  : "border-border bg-card text-text-secondary hover:border-primary/40 hover:text-primary",
              )}
            >
              <span>{meta.label}</span>
              {count > 0 && (
                <span
                  className={cn(
                    "text-xs font-medium tabular-nums",
                    isActive ? "text-primary" : "text-text-muted",
                  )}
                >
                  {formatCount(count)}
                </span>
              )}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
