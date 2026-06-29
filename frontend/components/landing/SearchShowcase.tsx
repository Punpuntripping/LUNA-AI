import { ExternalLink, Quote } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  SHOWCASE,
  SHOWCASE_CITATIONS,
  SHOWCASE_TOTAL_REFS,
  SOURCE_TYPES,
  SourceType,
} from "./content";
import { ShowcaseReferences } from "./ShowcaseReferences";

/**
 * The landing centerpiece — a faithful, static rendering of a real Rayhan
 * search result (blog share c6f6b05f…). It mirrors the in-app
 * ``ReferencePanel`` card anatomy ([n] badge, domain icon + label, relevance
 * dot, snippet, «عرض المصدر» / «فتح الرابط») so prospects see the actual
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

        {/* The mock report card */}
        <div className="mx-auto mt-10 max-w-3xl overflow-hidden rounded-2xl border border-border bg-card shadow-xl shadow-primary/5 ring-1 ring-black/[0.03]">
          {/* Window chrome + example tag */}
          <div className="flex items-center justify-between border-b border-border bg-muted/40 px-4 py-2.5">
            <div className="flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-full bg-border" />
              <span className="h-2.5 w-2.5 rounded-full bg-border" />
              <span className="h-2.5 w-2.5 rounded-full bg-border" />
            </div>
            <span className="rounded-full bg-primary/10 px-2.5 py-0.5 text-[11px] font-semibold text-primary">
              {SHOWCASE.exampleTag}
            </span>
          </div>

          <div className="space-y-5 p-5 sm:p-7">
            {/* User question */}
            <div className="flex gap-2.5">
              <Quote className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
              <p className="text-sm leading-relaxed text-muted-foreground">
                {SHOWCASE.question}
              </p>
            </div>

            {/* Answer */}
            <div className="rounded-xl border border-border/70 bg-background p-4 sm:p-5">
              <p className="text-[15px] font-semibold leading-relaxed text-foreground">
                {SHOWCASE.answerLead}
              </p>
              <p className="mt-3 text-sm leading-loose text-foreground/90">
                {SHOWCASE.answerBody}
                <CitationMarker n={SHOWCASE.citationN} />
              </p>
            </div>

            {/* References panel — mirrors ReferencePanel; «عرض المصدر» is live */}
            <ShowcaseReferences
              citations={SHOWCASE_CITATIONS}
              totalRefs={SHOWCASE_TOTAL_REFS}
            />
          </div>
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

/** Inline [n] citation marker, styled like the in-app reference badge. */
function CitationMarker({ n }: { n: number }) {
  return (
    <sup className="mx-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded bg-primary/15 px-1 align-super text-[10px] font-semibold tabular-nums text-primary">
      {n}
    </sup>
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
