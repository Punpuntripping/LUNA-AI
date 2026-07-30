import { CardShell } from "@/components/library/hub/CardShell";
import { FileText } from "lucide-react";
import { type FormHubItem } from "@/lib/library/api";

/**
 * One card in the /forms 3×3 hub grid: title, category chip, and a 2-line
 * «متى تستخدمه» snippet (the free SEO layer — the template body never appears on
 * a hub card). Server component — links to the form detail page.
 */
export function FormCard({ item, href }: { item: FormHubItem; href?: string | null }) {
  // `href === undefined` keeps the hub's own link; `null` renders unlinked.
  const target =
    href === undefined ? `/forms/${item.slug}` : href;
  return (
    <CardShell href={target}>
      <div className="mb-2.5 flex flex-wrap items-center gap-1.5">
        <span className="inline-flex items-center gap-1 rounded-full bg-accent-soft px-2 py-0.5 text-[11px] font-semibold text-primary">
          <FileText aria-hidden="true" className="h-3 w-3" />
          نموذج
        </span>
        {item.category && (
          <span className="inline-flex items-center rounded-full bg-pill px-2 py-0.5 text-[11px] font-medium text-pill-fg">
            {item.category}
          </span>
        )}
      </div>

      <h2 className="line-clamp-2 text-base font-bold leading-snug text-foreground transition-colors group-hover:text-primary">
        {item.title}
      </h2>

      {item.use_case_snippet && (
        <p className="mt-2.5 line-clamp-3 text-sm leading-relaxed text-text-secondary">
          {item.use_case_snippet}
        </p>
      )}
    </CardShell>
  );
}
