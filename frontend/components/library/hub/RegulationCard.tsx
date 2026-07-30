import { CardShell } from "@/components/library/hub/CardShell";
import { Landmark } from "lucide-react";
import { StatusBadge } from "@/components/library/blocks/StatusBadge";
import { toDocStatus, type RegulationHubItem } from "@/lib/library/api";

/**
 * One card in the /regulations 3×3 hub grid: doc-type + status badges, title,
 * issuing entity, a 2-line summary snippet, and up to three sector pills. Server
 * component — links to the document page (Arabic slug, browser-encoded on nav).
 */
export function RegulationCard({ item, href }: { item: RegulationHubItem; href?: string | null }) {
  // `href === undefined` keeps the hub's own link; `null` renders unlinked.
  const target =
    href === undefined ? `/regulations/${item.slug}` : href;
  const status = toDocStatus(item.status);

  return (
    <CardShell href={target}>
      <div className="mb-2.5 flex flex-wrap items-center gap-1.5">
        {item.doc_type && (
          <span className="inline-flex items-center rounded-full bg-pill px-2 py-0.5 text-[11px] font-medium text-pill-fg">
            {item.doc_type}
          </span>
        )}
        {status && <StatusBadge status={status} />}
      </div>

      <h2 className="line-clamp-2 text-base font-bold leading-snug text-foreground transition-colors group-hover:text-primary">
        {item.title}
      </h2>

      {item.entity_name && (
        <p className="mt-1.5 flex items-center gap-1.5 text-xs text-muted-foreground">
          <Landmark aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
          <span className="truncate">{item.entity_name}</span>
        </p>
      )}

      {item.summary_snippet && (
        <p className="mt-2.5 line-clamp-2 text-sm leading-relaxed text-text-secondary">
          {item.summary_snippet}
        </p>
      )}

      {item.sectors.length > 0 && (
        <ul className="mt-auto flex flex-wrap gap-1.5 pt-4">
          {item.sectors.slice(0, 3).map((sector) => (
            <li
              key={sector}
              className="rounded-full bg-pill px-2 py-0.5 text-[11px] font-medium text-pill-fg"
            >
              {sector}
            </li>
          ))}
        </ul>
      )}
    </CardShell>
  );
}
