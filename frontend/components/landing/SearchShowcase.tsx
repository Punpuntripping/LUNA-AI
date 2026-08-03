import { ExternalLink } from "lucide-react";
import { cn } from "@/lib/utils";
import { SHOWCASE, SOURCE_TYPES, SourceType } from "./content";
import { ShowcaseReportCard } from "./ShowcaseReportCard";

/**
 * The landing centerpiece — a faithful, static rendering of a real Rayhan
 * search result (blog share c6f6b05f…). It mirrors the in-app
 * ``ReferencePanel`` card anatomy ([n] badge, domain icon + label, relevance
 * dot, snippet, «عرض المصدر» / «فتح المصدر الرسمي») so prospects see the actual
 * product surface: a complete answer where every citation links back to its
 * official source — here across both regulations and government services.
 */
export function SearchShowcase() {
  return (
    <section id="showcase" className="scroll-mt-20 bg-muted/30 py-16 sm:py-20">
      <div className="mx-auto max-w-5xl px-4">
        {/* Section header */}
        <div className="mx-auto max-w-2xl text-center">
          <span className="text-sm font-semibold text-primary">
            {SHOWCASE.eyebrow}
          </span>
          <h2 className="mt-2 text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
            {SHOWCASE.title}
          </h2>
          <p className="mt-3 text-base leading-relaxed text-muted-foreground">
            {SHOWCASE.subtitle}
          </p>
        </div>

        {/* The mock report card (shared with the اكتشف ريحان lessons) */}
        <div className="mx-auto mt-10 max-w-3xl">
          <ShowcaseReportCard />
        </div>

        {/* Three source types every report can cite */}
        <div className="mx-auto mt-8 max-w-3xl">
          <p className="mb-3 text-center text-sm text-muted-foreground">
            كل استشهاد مربوط بمصدره الرسمي — عبر ثلاثة مصادر معتمدة:
          </p>
          <div className="grid gap-3 sm:grid-cols-3">
            {SOURCE_TYPES.map((s) => (
              <SourceTypeCard key={s.label} source={s} />
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

function SourceTypeCard({ source }: { source: SourceType }) {
  const Icon = source.icon;
  return (
    <div className="rounded-xl border border-border bg-card p-4 shadow-sm transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md">
      <div className="flex items-center gap-2">
        <Icon className={cn("h-4 w-4", source.tint)} />
        <span className="text-sm font-semibold text-foreground">
          {source.label}
        </span>
      </div>
      <div className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
        <ExternalLink className="h-3 w-3 shrink-0" />
        {source.linkLabel}
      </div>
    </div>
  );
}
