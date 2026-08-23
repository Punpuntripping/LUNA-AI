import { ChevronDown } from "lucide-react";
import { EntityBrowseGrid } from "@/components/library/hub/EntityBrowseGrid";
import { ENTITY_FACET_LABEL, type EntityNavItem } from "@/lib/library/entities";

/**
 * «تصفّح حسب الجهة ▾» — the entity switcher that sits under the header of
 * `/compliance`, of every `/compliance/page/{n}`, and of every entity page.
 *
 * ⚠ NATIVE `<details>` / `<summary>`, NOT A COMPONENT-LIBRARY DISCLOSURE.
 * Copied from `CourtSwitcher` / `sectors/SectorSwitcher` and for their four
 * reasons: the 28 links are in the SSR HTML whether the panel is open or closed,
 * keyboard and screen-reader behaviour is the platform's, it works with JS
 * disabled, and it needs zero client state — so `ComplianceHubView` stays a
 * SERVER component, which is what keeps the whole wing statically prerendered
 * and ISR-cached.
 *
 * Collapsed by default: inside a section the entity the reader chose is the
 * subject of the page, and a 29-tile slab above the cards would bury it. On the
 * unfiltered hub it stays collapsed too — the cards are the offer, the axis is
 * the affordance.
 *
 * The label comes from `ENTITY_FACET_LABEL` («الجهة»), the same word the cards
 * print on their entity chip and the breadcrumb uses. The wing's free-text
 * `provider` filter is a different axis and is not this control.
 */
export function EntitySwitcher({
  entities,
  activeSlug,
}: {
  entities: EntityNavItem[];
  /** The entity section being viewed. Absent ⇒ the unfiltered /compliance hub. */
  activeSlug?: string;
}) {
  if (entities.length === 0) return null;

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
            ? `تغيير ${ENTITY_FACET_LABEL}`
            : `تصفّح حسب ${ENTITY_FACET_LABEL}`}
        </span>
        <ChevronDown
          aria-hidden="true"
          className="h-4 w-4 shrink-0 transition-transform group-open:rotate-180"
        />
      </summary>

      <div className="border-t border-border/70 p-4">
        <EntityBrowseGrid entities={entities} activeSlug={activeSlug} />
      </div>
    </details>
  );
}
