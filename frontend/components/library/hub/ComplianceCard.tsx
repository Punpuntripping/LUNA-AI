import { CardShell } from "@/components/library/hub/CardShell";
import { type ComplianceHubItem } from "@/lib/library/api";

/**
 * One card in the /compliance guide grid: issuing entity, title, and one line of
 * our own orientation text.
 *
 * ⚠ NOTHING RENDERS A PROCEDURE HERE. The card the retired wing used carried an
 * `intro_snippet` lifted out of the `services` corpus, and the page behind it
 * restated الشروط / المستندات المطلوبة / الخطوات — which is what got that wing
 * deleted on 2026-08-03. `summary` is written by us to answer "is this the
 * service I need?", and the answer to "how do I do it?" is the issuing entity's
 * page. If a field ever appears here that a government body owns and edits, it
 * is in the wrong place.
 *
 * Unlinked today: `compliance_table` does not exist, so the grid is never
 * rendered with items. `href` is threaded through anyway so «مكتبتي» and the
 * search results can reuse the card the day it does.
 */
export function ComplianceCard({
  item,
  href,
}: {
  item: ComplianceHubItem;
  href?: string | null;
}) {
  // `href === undefined` keeps the hub's own link; `null` renders unlinked.
  const target = href === undefined ? `/compliance/${item.slug}` : href;
  return (
    <CardShell href={target}>
      {item.provider_name && (
        <div className="mb-2.5 flex flex-wrap items-center gap-1.5">
          <span className="inline-flex items-center rounded-full bg-pill px-2 py-0.5 text-[11px] font-medium text-pill-fg">
            {item.provider_name}
          </span>
        </div>
      )}

      <h2 className="line-clamp-2 text-base font-bold leading-snug text-foreground transition-colors group-hover:text-primary">
        {item.title}
      </h2>

      {item.summary && (
        <p className="mt-2.5 line-clamp-2 text-sm leading-relaxed text-text-secondary">
          {item.summary}
        </p>
      )}
    </CardShell>
  );
}
