import { Check, X } from "lucide-react";
import { COMPARISON, COMPARISON_HEADER } from "./content";

/**
 * Head-to-head comparison: Rayhan vs. general-purpose AI tools.
 *
 * Desktop renders a 3-column table (dimension · ريحان · الأدوات العامة) with the
 * ريحان column tinted so it reads as the highlighted answer. Mobile collapses
 * each row into a stacked card, with inline column labels so the ✓/✗ still make
 * sense without the header row. Shares the same grid template across header and
 * body rows so the columns stay aligned in RTL.
 */
export function ComparisonSection() {
  return (
    <section className="mx-auto max-w-5xl px-4 py-16 sm:py-20">
      <div className="mx-auto max-w-2xl text-center">
        <h2 className="text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
          {COMPARISON_HEADER.title}
        </h2>
        <p className="mt-3 text-base leading-relaxed text-muted-foreground">
          {COMPARISON_HEADER.subtitle}
        </p>
      </div>

      <div className="mt-10 overflow-hidden rounded-2xl border border-border bg-card shadow-sm">
        {/* Product column headers — desktop only. */}
        <div className="hidden grid-cols-[1.3fr_1fr_1fr] border-b border-border md:grid">
          <div className="p-5" />
          <div className="flex flex-col items-center gap-0.5 bg-primary/[0.06] p-5 text-center">
            <span className="text-lg font-bold text-primary">
              {COMPARISON_HEADER.rayhanLabel}
            </span>
            <span className="text-xs text-muted-foreground">
              {COMPARISON_HEADER.rayhanHint}
            </span>
          </div>
          <div className="flex flex-col items-center gap-0.5 p-5 text-center">
            <span className="text-lg font-bold text-foreground">
              {COMPARISON_HEADER.othersLabel}
            </span>
            <span className="text-xs text-muted-foreground">
              {COMPARISON_HEADER.othersHint}
            </span>
          </div>
        </div>

        {/* One row per dimension. */}
        <div className="divide-y divide-border">
          {COMPARISON.map((row) => {
            const Icon = row.icon;
            return (
              <div
                key={row.dimension}
                className="grid grid-cols-1 md:grid-cols-[1.3fr_1fr_1fr]"
              >
                {/* Dimension label. */}
                <div className="flex items-center gap-3 bg-muted/30 p-5 md:bg-transparent">
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                    <Icon className="h-5 w-5" />
                  </div>
                  <span className="text-sm font-bold text-foreground">
                    {row.dimension}
                  </span>
                </div>

                {/* Rayhan — the ✓ column. */}
                <div className="flex items-start gap-2.5 p-5 md:bg-primary/[0.04]">
                  <Check className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                  <div className="flex-1">
                    <span className="mb-1 block text-xs font-semibold text-primary md:hidden">
                      {COMPARISON_HEADER.rayhanLabel}
                    </span>
                    <span className="text-sm leading-relaxed text-foreground">
                      {row.rayhan}
                    </span>
                  </div>
                </div>

                {/* General-purpose tools — the ✗ column. */}
                <div className="flex items-start gap-2.5 border-t border-dashed border-border p-5 md:border-t-0">
                  <X className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground/60" />
                  <div className="flex-1">
                    <span className="mb-1 block text-xs font-semibold text-muted-foreground md:hidden">
                      {COMPARISON_HEADER.othersLabel}
                    </span>
                    <span className="text-sm leading-relaxed text-muted-foreground">
                      {row.others}
                    </span>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
