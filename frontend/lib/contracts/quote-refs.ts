/**
 * The quotes behind a prior-report reply's `[n]` markers
 * (`message.metadata.quote_refs`, written by `agents/responder/prior_report.py`).
 *
 * A reply answered from an earlier report keeps its verified quotes out of the
 * text: the text carries `[1] [2]` and the user opens each quote from its number.
 * This is the one boundary parser for that list. A malformed item is DROPPED
 * rather than allowed to break the bubble.
 */

import { isZodLikeObject, trimmedNonEmpty } from "./next-steps";

export interface QuoteRef {
  /** The number the reply text shows, `[n]`. */
  n: number;
  /** The quote, word for word. */
  text: string;
  /** Whose words these are, e.g. «نظام التنفيذ — المادة 26». */
  source: string | null;
  /** A line above the quote, e.g. «وجدنا مصدرًا متعلقًا بسؤالك». */
  note: string | null;
}

/** More than a reply ever carries (the backend shows at most 4). */
export const MAX_QUOTE_REFS = 8;

/**
 * Validate a raw `quote_refs` value. `[]` when it is absent or not a list;
 * invalid items are skipped and a repeated number keeps its first occurrence.
 */
export function parseQuoteRefs(raw: unknown): QuoteRef[] {
  if (!Array.isArray(raw)) return [];
  const out: QuoteRef[] = [];
  const seen = new Set<number>();
  for (const item of raw) {
    if (!isZodLikeObject(item)) continue;
    const n = item.n;
    const text = trimmedNonEmpty(item.text);
    if (typeof n !== "number" || !Number.isInteger(n) || n < 1 || seen.has(n)) continue;
    if (text === null) continue;
    seen.add(n);
    out.push({ n, text, source: trimmedNonEmpty(item.source), note: trimmedNonEmpty(item.note) });
    if (out.length === MAX_QUOTE_REFS) break;
  }
  return out.sort((a, b) => a.n - b.n);
}

/** One quote as plain text: `[n] «…»` and its source on the next line. */
export function quoteRefAsText(ref: QuoteRef): string {
  const line = `[${ref.n}] «${ref.text}»`;
  return ref.source ? `${line}\n— ${ref.source}` : line;
}

/**
 * The reply as it should leave the app (copy, share): its text, then the quotes
 * its numbers open, so a pasted `[1]` still points at something.
 */
export function withQuoteRefs(content: string, refs: QuoteRef[]): string {
  if (refs.length === 0) return content;
  return [content.trimEnd(), ...refs.map(quoteRefAsText)].join("\n\n");
}
