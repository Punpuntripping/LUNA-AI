import { CardShell } from "@/components/library/hub/CardShell";
import { ScrollText } from "lucide-react";
import { type CircularHubItem } from "@/lib/library/api";

/**
 * One card in the /circulars 3×3 hub grid: title, issuing-entity badge, and a
 * 2-line body snippet. Server component — links to the circular document page
 * (Arabic slug, browser-encoded on nav).
 *
 * `source_label` is an INTERNAL provenance token ('entity' / 'scraped') and is
 * deliberately NOT rendered anywhere on the card.
 */
export function CircularCard({ item, href }: { item: CircularHubItem; href?: string | null }) {
  // `href === undefined` keeps the hub's own link; `null` renders unlinked.
  const target =
    href === undefined ? `/circulars/${item.slug}` : href;
  return (
    <CardShell href={target}>
      {item.entity_name && (
        <div className="mb-2.5 flex flex-wrap items-center gap-1.5">
          <span className="inline-flex items-center gap-1 rounded-full bg-pill px-2 py-0.5 text-[11px] font-medium text-pill-fg">
            <ScrollText aria-hidden="true" className="h-3 w-3 shrink-0" />
            {item.entity_name}
          </span>
        </div>
      )}

      <h2 className="line-clamp-2 text-base font-bold leading-snug text-foreground transition-colors group-hover:text-primary">
        {item.title}
      </h2>

      {item.body_snippet && (
        <p className="mt-2.5 line-clamp-3 text-sm leading-relaxed text-text-secondary">
          {item.body_snippet}
        </p>
      )}
    </CardShell>
  );
}
