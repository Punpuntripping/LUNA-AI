import { ChevronLeft, HelpCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { JsonLd } from "@/components/seo/JsonLd";
import { buildFaqPage } from "@/lib/seo/schema";
import type { FaqBlockProps } from "@/types/library";

/**
 * «الأسئلة الشائعة» — a native `<details>` accordion (no client JS) plus a
 * `FAQPage` JSON-LD node for rich results. Server component. Answers render as
 * blank-line paragraphs with preserved single line breaks.
 */
export function FaqBlock({
  items,
  title = "الأسئلة الشائعة",
  withJsonLd = true,
  className,
}: FaqBlockProps) {
  if (items.length === 0) return null;

  return (
    <section dir="rtl" className={cn("w-full", className)}>
      {withJsonLd && (
        <JsonLd
          data={buildFaqPage(
            items.map((item) => ({ question: item.q, answer: item.a })),
          )}
        />
      )}

      <h2 className="mb-3 flex items-center gap-2 text-lg font-bold text-foreground">
        <HelpCircle aria-hidden="true" className="h-5 w-5 shrink-0 text-primary" />
        {title}
      </h2>

      <div className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card">
        {items.map((item, index) => (
          <details key={index} className="group">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3.5 text-sm font-semibold text-foreground transition-colors hover:bg-muted/40">
              {item.q}
              <ChevronLeft
                aria-hidden="true"
                className="h-4 w-4 shrink-0 text-muted-foreground transition-transform group-open:-rotate-90"
              />
            </summary>
            <div className="px-4 pb-4 text-sm leading-relaxed text-text-secondary">
              {item.a
                .split(/\n{2,}/)
                .map((p) => p.trim())
                .filter(Boolean)
                .map((paragraph, pIndex) => (
                  <p key={pIndex} className="whitespace-pre-line [&:not(:first-child)]:mt-2">
                    {paragraph}
                  </p>
                ))}
            </div>
          </details>
        ))}
      </div>
    </section>
  );
}
