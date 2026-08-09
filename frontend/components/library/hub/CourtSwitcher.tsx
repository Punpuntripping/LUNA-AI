import { ChevronDown } from "lucide-react";
import { CourtBrowseGrid } from "@/components/library/hub/CourtBrowseGrid";
import { COURT_FACET_LABEL, type CourtNavItem } from "@/lib/library/courts";

/**
 * «تصفّح حسب الجهة القضائية ▾» — the court switcher that sits under the header of
 * `/judgments` and of every `/judgments/courts/{slug}` page.
 *
 * ⚠ NATIVE `<details>` / `<summary>`, NOT A COMPONENT-LIBRARY DISCLOSURE.
 * Copied from `sectors/SectorSwitcher` and for its four reasons: the 12 links
 * are in the SSR HTML whether the panel is open or closed, keyboard and
 * screen-reader behaviour is the platform's, it works with JS disabled, and it
 * needs zero client state — so the hub stays a server component.
 *
 * Collapsed by default: inside a section the court the reader chose is the
 * subject of the page, and a slab of tiles above the cards would bury it.
 *
 * ⚠ THE LABEL IS «الجهة القضائية», NEVER «نوع المحكمة». `المحكمة العليا` and
 * `محكمة الاستئناف` are court LEVELS that leak into the `court` column on the
 * وزارة العدل feed, so those two buckets sit next to the درجة المحكمة chips in
 * `JudgmentsFilterBar`. The two controls compose — they are different axes over
 * the same corpus — and «نوع المحكمة» would make the pair read as a
 * contradiction.
 */
export function CourtSwitcher({
  courts,
  activeSlug,
}: {
  courts: CourtNavItem[];
  /** The court section being viewed. Absent ⇒ the unfiltered /judgments hub. */
  activeSlug?: string;
}) {
  if (courts.length === 0) return null;

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
        <span>
          {activeSlug
            ? `تغيير ${COURT_FACET_LABEL}`
            : `تصفّح حسب ${COURT_FACET_LABEL}`}
        </span>
        <ChevronDown
          aria-hidden="true"
          className="h-4 w-4 shrink-0 transition-transform group-open:rotate-180"
        />
      </summary>

      <div className="border-t border-border/70 p-4">
        <CourtBrowseGrid courts={courts} activeSlug={activeSlug} />
      </div>
    </details>
  );
}
