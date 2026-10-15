import { useMemo } from "react";

import { linkifyCitations, stripMarkdownImages } from "@contracts";

import { EnrichedMarkdown } from "./adapters/EnrichedMarkdown";
import { MarkedMarkdown } from "./adapters/MarkedMarkdown";
import type { MarkdownAdapterProps } from "./types";

export type { MarkdownVariant } from "./types";

/** K0 spike: which renderer draws. Removed once the spike picks a winner. */
export type MarkdownEngine = "A" | "B";

export interface MarkdownProps extends MarkdownAdapterProps {
  /** Spike only — (A) react-native-enriched-markdown (native, needs a new dev
   *  build) or (B) marked + own renderer (pure JS, default). */
  engine?: MarkdownEngine;
}

/** K1 — the shared preprocessing (same as the web): drop markdown images
 *  (cards live in the reader, never inline), then `[4،17]` →
 *  `[[4]](cite:4)[[17]](cite:17)` so the adapters route `cite:` links. */
export function prepareMarkdown(content: string): string {
  return linkifyCitations(stripMarkdownImages(content));
}

/** Assistant/reader markdown: Arabic RTL, GFM tables, `[n]` citations as
 *  pills calling `onCitationPress`, http(s) links in the in-app browser. */
export function Markdown({ content, engine = "B", ...rest }: MarkdownProps) {
  const prepared = useMemo(() => prepareMarkdown(content), [content]);
  return engine === "A" ? <EnrichedMarkdown content={prepared} {...rest} /> : <MarkedMarkdown content={prepared} {...rest} />;
}
