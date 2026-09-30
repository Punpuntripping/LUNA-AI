import { ClipboardCopy, FileText, Layers, MessageSquare, PenLine } from "lucide-react";
import { DRAFT_SHOWCASE } from "./content";
import { CitationMarker } from "./ShowcaseReportCard";

const ACTION_ICON = [ClipboardCopy, MessageSquare, PenLine];

/**
 * The drafting half of the product — a static rendering of a REAL memo the
 * writer produced (see `DRAFT_SHOWCASE`). Styled as a document page rather
 * than a chat card so it reads as "the thing you file", not "the thing you ask".
 */
export function DraftShowcase() {
  const d = DRAFT_SHOWCASE;
  return (
    <section className="mx-auto max-w-5xl px-4 py-16 sm:py-20">
      <div className="mx-auto max-w-2xl text-center">
        <span className="text-sm font-semibold text-primary">{d.eyebrow}</span>
        <h2 className="mt-2 text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
          {d.title}
        </h2>
        <p className="mt-3 text-base leading-relaxed text-muted-foreground">
          {d.subtitle}
        </p>
      </div>

      <div className="mx-auto mt-10 max-w-3xl overflow-hidden rounded-2xl border border-border bg-card shadow-xl shadow-primary/5 ring-1 ring-black/[0.03]">
        {/* Document toolbar */}
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border bg-muted/40 px-4 py-2.5">
          <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
            <FileText className="h-4 w-4 text-primary" />
            {d.docType}
            <span aria-hidden="true">·</span>
            <span className="tabular-nums">{d.wordCount} كلمة</span>
            <span aria-hidden="true">·</span>
            <span className="tabular-nums">المراجع ({d.totalRefs})</span>
          </div>
          <span className="rounded-full bg-primary/10 px-2.5 py-0.5 text-[11px] font-semibold text-primary">
            {d.exampleTag}
          </span>
        </div>

        <div className="p-5 sm:p-8">
          <div className="mb-5 inline-flex items-center gap-1.5 rounded-full border border-border bg-background px-3 py-1 text-[11px] text-muted-foreground">
            <Layers className="h-3 w-3" />
            {d.builtFrom}
          </div>

          <h3 className="text-balance text-lg font-bold leading-relaxed text-foreground sm:text-xl">
            {d.docTitle}
          </h3>

          <h4 className="mt-6 text-base font-bold text-foreground">
            {d.sectionHeading}
          </h4>
          <div className="relative mt-3 space-y-4">
            {d.paragraphs.map((p) => (
              <p
                key={p.lead ?? p.text.slice(0, 24)}
                className="text-sm leading-loose text-foreground/90"
              >
                {p.lead && <strong className="font-bold text-foreground">{p.lead} </strong>}
                {p.text}
                {p.cites.map((n) => (
                  <CitationMarker key={n} n={n} />
                ))}
              </p>
            ))}
            {/* Fade — the memo continues. */}
            <div
              aria-hidden="true"
              className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-card to-transparent"
            />
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border pt-4">
            {d.actions.map((label, i) => {
              const Icon = ACTION_ICON[i] ?? PenLine;
              return (
                <span
                  key={label}
                  className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-border bg-background px-3 text-xs font-medium text-foreground"
                >
                  <Icon className="h-3.5 w-3.5 text-primary" />
                  {label}
                </span>
              );
            })}
          </div>
        </div>
      </div>
    </section>
  );
}
