import { Quote } from "lucide-react";
import {
  SHOWCASE,
  SHOWCASE_CITATIONS,
  SHOWCASE_TOTAL_REFS,
} from "./content";
import { ShowcaseReferences } from "./ShowcaseReferences";

/**
 * The mock report card — a faithful, static rendering of a real Rayhan search
 * result (window chrome → question → answer with an inline [n] marker → the
 * «المراجع» panel with a live «عرض المصدر» dialog). Extracted from
 * `SearchShowcase` so the landing/about_us centerpiece and the اكتشف ريحان
 * lessons all render the SAME illustration from the same `content.ts` data —
 * one showcase, no forks.
 */
export function ShowcaseReportCard() {
  return (
    <div className="overflow-hidden rounded-2xl border border-border bg-card shadow-xl shadow-primary/5 ring-1 ring-black/[0.03]">
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
  );
}

/** Inline [n] citation marker, styled like the in-app reference badge. */
export function CitationMarker({ n }: { n: number }) {
  return (
    <sup className="mx-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded bg-primary/15 px-1 align-super text-[10px] font-semibold tabular-nums text-primary">
      {n}
    </sup>
  );
}
