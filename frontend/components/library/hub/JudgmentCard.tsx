import { CardShell } from "@/components/library/hub/CardShell";
import { Gavel, MapPin, CalendarDays } from "lucide-react";
import { CourtLevelBadge } from "@/components/library/blocks/CourtLevelBadge";
import { SectorPills } from "@/components/library/hub/SectorPills";
import type { JudgmentHubItem } from "@/types/library";

/**
 * One card in the /judgments 3×3 hub grid: court + درجة التقاضي badge, title,
 * city + Hijri date, a snippet, and the judgment's `legal_domains[]` — which
 * ARE the sector vocabulary, so they render through the shared `SectorPills`
 * and link to `/library/{slug}` (D11).
 *
 * They used to be plain `<span>`s because the whole card was one `<Link>` and
 * nesting anchors is invalid HTML. `CardShell`'s `footer` slot lifted that
 * constraint — the pills are now siblings of the anchor, not children of it.
 *
 * Server component — links to the judgment document page (Arabic slug
 * interpolated raw; the browser encodes on navigation, same as every sibling).
 */
export function JudgmentCard({
  item,
  href,
  sectorSlugs,
}: {
  item: JudgmentHubItem;
  href?: string | null;
  sectorSlugs?: Record<string, string>;
}) {
  // `href === undefined` keeps the hub's own link; `null` renders unlinked.
  const target =
    href === undefined ? `/judgments/${item.slug}` : href;
  return (
    <CardShell
      href={target}
      footer={<SectorPills names={item.domains} slugs={sectorSlugs} />}
    >
      <div className="mb-2.5 flex flex-wrap items-center gap-1.5">
        {item.court && (
          <span className="inline-flex items-center gap-1 rounded-full bg-pill px-2 py-0.5 text-xs font-medium text-pill-fg">
            <Gavel aria-hidden="true" className="h-3 w-3 shrink-0" />
            {item.court}
          </span>
        )}
        <CourtLevelBadge
          level={item.court_level}
          label={item.court_level_label}
          className="text-xs"
        />
      </div>

      <h2 className="line-clamp-2 text-base font-bold leading-snug text-foreground transition-colors group-hover:text-primary">
        {item.title}
      </h2>

      {(item.city || item.date_hijri) && (
        <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-muted">
          {item.city && (
            <span className="inline-flex items-center gap-1">
              <MapPin aria-hidden="true" className="h-3 w-3 shrink-0" />
              {item.city}
            </span>
          )}
          {item.date_hijri && (
            <span className="inline-flex items-center gap-1">
              <CalendarDays aria-hidden="true" className="h-3 w-3 shrink-0" />
              {item.date_hijri} هـ
            </span>
          )}
        </p>
      )}

      {item.snippet && (
        <p className="mt-2.5 line-clamp-3 text-sm leading-relaxed text-text-secondary">
          {item.snippet}
        </p>
      )}
    </CardShell>
  );
}
