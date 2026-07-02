import { ExternalLink, Search, PenLine } from "lucide-react";
import type { Audience } from "./content";

/**
 * One audience card on the «ريحان يستهدف مين؟» page: who they are, real example
 * questions they'd ask, and — for each — the official source it traces to. The
 * source links mirror the in-app citation model: every claim is anchored to a
 * real Saudi regulation, judgment, or government service. Server component
 * (no interactivity) so the lucide icons in ``content.ts`` are used directly.
 */
export function AudienceBlock({ audience }: { audience: Audience }) {
  const Icon = audience.icon;
  return (
    <div className="flex flex-col rounded-2xl border border-border bg-card p-6 shadow-sm sm:p-7">
      {/* Header */}
      <div className="flex items-center gap-3">
        <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
          <Icon className="h-5 w-5" />
        </span>
        <h3 className="text-xl font-bold text-foreground">{audience.title}</h3>
      </div>

      <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
        {audience.tagline}
      </p>

      {/* Example questions, each with its official source */}
      <ul className="mt-5 flex flex-col gap-3">
        {audience.examples.map((ex) => {
          const TagIcon = ex.tagIcon;
          const LeadIcon = ex.mode === "write" ? PenLine : Search;
          return (
            <li
              key={ex.q}
              className="rounded-xl border border-border/70 bg-background p-3.5 transition-colors hover:border-primary/40"
            >
              <div className="flex items-start gap-2.5">
                <LeadIcon className="mt-0.5 h-4 w-4 shrink-0 text-primary/70" />
                <div className="min-w-0 flex-1">
                  {ex.tag && (
                    <span className="mb-1.5 inline-flex items-center gap-1 rounded-md bg-primary/10 px-2 py-0.5 text-[11px] font-semibold text-primary">
                      {TagIcon && <TagIcon className="h-3 w-3" />}
                      {ex.tag}
                    </span>
                  )}
                  <p className="text-sm font-medium leading-snug text-foreground">
                    {ex.q}
                  </p>
                  <a
                    href={ex.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-1.5 inline-flex items-center gap-1 text-[11px] font-medium text-muted-foreground transition-colors hover:text-primary"
                  >
                    <ExternalLink className="h-3 w-3 shrink-0" />
                    <span className="line-clamp-1">المصدر: {ex.source}</span>
                  </a>
                </div>
              </div>
            </li>
          );
        })}
      </ul>

      {/* "يستند إلى" footer */}
      <p className="mt-5 border-t border-border pt-4 text-xs leading-relaxed text-muted-foreground">
        {audience.basis}
      </p>
    </div>
  );
}
