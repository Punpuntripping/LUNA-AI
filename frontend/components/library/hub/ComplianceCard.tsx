import { CardShell } from "@/components/library/hub/CardShell";
import { Star } from "lucide-react";
import { type ComplianceHubItem } from "@/lib/library/api";

/**
 * One card in the /compliance 3×3 hub grid: provider badge, an «الأكثر
 * استخدامًا» chip for popular services, title, a 2-line intro snippet, and up to
 * three sector pills. Server component — links to the service HowTo page.
 */
export function ComplianceCard({ item, href }: { item: ComplianceHubItem; href?: string | null }) {
  // `href === undefined` keeps the hub's own link; `null` renders unlinked.
  const target =
    href === undefined ? `/compliance/${item.slug}` : href;
  return (
    <CardShell href={target}>
      <div className="mb-2.5 flex flex-wrap items-center gap-1.5">
        {item.provider_name && (
          <span className="inline-flex items-center rounded-full bg-pill px-2 py-0.5 text-[11px] font-medium text-pill-fg">
            {item.provider_name}
          </span>
        )}
        {item.is_most_used && (
          <span className="inline-flex items-center gap-1 rounded-full bg-accent-soft px-2 py-0.5 text-[11px] font-semibold text-primary">
            <Star aria-hidden="true" className="h-3 w-3" />
            الأكثر استخدامًا
          </span>
        )}
      </div>

      <h2 className="line-clamp-2 text-base font-bold leading-snug text-foreground transition-colors group-hover:text-primary">
        {item.title}
      </h2>

      {item.intro_snippet && (
        <p className="mt-2.5 line-clamp-2 text-sm leading-relaxed text-text-secondary">
          {item.intro_snippet}
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
