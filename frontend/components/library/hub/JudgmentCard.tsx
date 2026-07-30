import { CardShell } from "@/components/library/hub/CardShell";
import { Gavel, MapPin, CalendarDays } from "lucide-react";
import { CourtLevelBadge } from "@/components/library/blocks/CourtLevelBadge";
import type { JudgmentHubItem } from "@/types/library";

/**
 * One card in the /judgments 3×3 hub grid: court + درجة التقاضي badge, title,
 * city + Hijri date, a snippet, and the judgment's domain chips. Server
 * component — links to the judgment document page (Arabic slug interpolated
 * raw; the browser encodes on navigation, same as every sibling card).
 *
 * The domain chips are deliberately plain `<span>`s, NOT filter links: the whole
 * card is already one `<Link>` and nesting anchors is invalid HTML. Domain
 * filtering is reached from the doc page's breadcrumb chips and the hub's own
 * active-filter row instead.
 */
export function JudgmentCard({ item, href }: { item: JudgmentHubItem; href?: string | null }) {
  // `href === undefined` keeps the hub's own link; `null` renders unlinked.
  const target =
    href === undefined ? `/judgments/${item.slug}` : href;
  return (
    <CardShell href={target}>
      <div className="mb-2.5 flex flex-wrap items-center gap-1.5">
        {item.court && (
          <span className="inline-flex items-center gap-1 rounded-full bg-pill px-2 py-0.5 text-[11px] font-medium text-pill-fg">
            <Gavel aria-hidden="true" className="h-3 w-3 shrink-0" />
            {item.court}
          </span>
        )}
        <CourtLevelBadge
          level={item.court_level}
          label={item.court_level_label}
          className="text-[11px]"
        />
      </div>

      <h2 className="line-clamp-2 text-base font-bold leading-snug text-foreground transition-colors group-hover:text-primary">
        {item.title}
      </h2>

      {(item.city || item.date_hijri) && (
        <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-text-muted">
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

      {item.domains.length > 0 && (
        <ul className="mt-auto flex flex-wrap gap-1.5 pt-3">
          {item.domains.slice(0, 3).map((domain) => (
            <li key={domain}>
              <span className="inline-flex items-center rounded-full bg-surface-2 px-2 py-0.5 text-[11px] font-medium text-text-muted">
                {domain}
              </span>
            </li>
          ))}
        </ul>
      )}
    </CardShell>
  );
}
