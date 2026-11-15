"use client";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { CitationMarker, citationMarkerClassName } from "@/components/chat/CitationMarker";
import type { QuoteRef } from "@/lib/contracts/quote-refs";

interface QuoteRefMarkerProps {
  n: number;
  /** The quote behind `[n]`; absent → an inert marker. */
  quote: QuoteRef | undefined;
}

/**
 * `[n]` in a reply built from a prior report: click it and the quote opens in
 * place, word for word, with whose words they are. Same chip as a search
 * reply's citation, so the two read as one thing; a search citation opens the
 * card, this one opens the quote itself.
 */
export function QuoteRefMarker({ n, quote }: QuoteRefMarkerProps) {
  if (!quote) return <CitationMarker n={n} />;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          dir="ltr"
          style={{ unicodeBidi: "isolate" }}
          aria-label={`عرض المرجع رقم ${n}`}
          className={citationMarkerClassName(true)}
        >
          [{n}]
        </button>
      </PopoverTrigger>
      <PopoverContent
        dir="rtl"
        lang="ar"
        side="top"
        collisionPadding={16}
        className="w-[min(26rem,calc(100vw-2rem))] p-0"
      >
        <div className="max-h-[min(22rem,50vh)] overflow-y-auto px-4 py-3 space-y-2">
          {quote.note && (
            <p className="text-xs font-medium text-muted-foreground">{quote.note}</p>
          )}
          <blockquote className="border-s-2 border-primary/50 ps-3 text-sm leading-[1.9] text-foreground">
            «{quote.text}»
          </blockquote>
          {quote.source && (
            <p className="text-xs leading-relaxed text-muted-foreground">— {quote.source}</p>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
