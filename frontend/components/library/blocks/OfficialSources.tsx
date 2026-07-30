import { ExternalLink, Landmark } from "lucide-react";
import { cn } from "@/lib/utils";
import type { OfficialSourcesProps } from "@/types/library";

/**
 * «المصادر الرسمية» — outbound links to the authoritative government sources
 * (BOE / هيئة الخبراء / ناجز، official PDF / landing pages). All links open in a
 * new tab with `rel="noopener noreferrer"` and an external-link icon. Server
 * component.
 */
export function OfficialSources({
  sources,
  title = "المصادر الرسمية",
  className,
}: OfficialSourcesProps) {
  if (sources.length === 0) return null;

  return (
    <section dir="rtl" className={cn("w-full", className)}>
      <h2 className="mb-3 flex items-center gap-2 text-sm font-bold text-foreground">
        <Landmark aria-hidden="true" className="h-4 w-4 shrink-0 text-primary" />
        {title}
      </h2>

      <ul className="space-y-2">
        {sources.map((source, index) => (
          <li key={`${source.href}-${index}`}>
            <a
              href={source.href}
              target="_blank"
              rel="noopener noreferrer"
              className="group flex items-center gap-2.5 rounded-lg border border-border bg-card px-3 py-2.5 transition-colors hover:border-primary/40 hover:bg-muted/40"
            >
              <ExternalLink
                aria-hidden="true"
                className="h-4 w-4 shrink-0 text-muted-foreground transition-colors group-hover:text-primary"
              />
              <span className="flex-1 text-sm font-medium text-foreground group-hover:text-primary">
                {source.label}
              </span>
            </a>
          </li>
        ))}
      </ul>
    </section>
  );
}
