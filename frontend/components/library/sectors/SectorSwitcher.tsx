import { ChevronDown } from "lucide-react";
import { SectorBrowseGrid } from "@/components/library/sectors/SectorBrowseGrid";
import type { SectorSummary } from "@/lib/library/api";

/**
 * «تغيير القطاع ▾» — the sector switcher that sits under the header of every
 * `/library/{sector}` page (§8.3).
 *
 * ⚠ NATIVE `<details>` / `<summary>`, NOT A COMPONENT LIBRARY DISCLOSURE.
 * Deliberate, and it buys four things at once: the 38 links are in the SSR HTML
 * whether the panel is open or closed (crawlable — a JS popover renders nothing
 * for a crawler), keyboard and screen-reader behaviour is the platform's, it
 * works with JS disabled, and it needs zero client state so the whole page
 * stays a server component and stays statically prerenderable.
 *
 * Collapsed by default: the sector the reader chose is the subject of the page,
 * and 38 tiles above the content would bury it.
 */
export function SectorSwitcher({
  sectors,
  activeSlug,
}: {
  sectors: SectorSummary[];
  activeSlug: string;
}) {
  if (sectors.length === 0) return null;

  return (
    <details
      dir="rtl"
      className="group overflow-hidden rounded-xl border border-border bg-surface-2/40"
    >
      <summary
        className={
          "flex cursor-pointer list-none items-center justify-between gap-2 px-4 py-3 " +
          "text-sm font-medium text-text-secondary transition-colors hover:text-foreground " +
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring " +
          "[&::-webkit-details-marker]:hidden"
        }
      >
        <span>تغيير القطاع</span>
        <ChevronDown
          aria-hidden="true"
          className="h-4 w-4 shrink-0 transition-transform group-open:rotate-180"
        />
      </summary>

      <div className="border-t border-border/70 p-4">
        <SectorBrowseGrid sectors={sectors} activeSlug={activeSlug} />
      </div>
    </details>
  );
}
