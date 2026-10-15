import { useMemo } from "react";

import { EnrichedMarkdown } from "./adapters/EnrichedMarkdown";
import { MarkedMarkdown } from "./adapters/MarkedMarkdown";
import { linkifyCitations } from "./linkify-citations";
import type { MarkdownAdapterProps } from "./types";

export type { MarkdownVariant } from "./types";

/** K0 spike: which renderer draws. Removed once the spike picks a winner. */
export type MarkdownEngine = "A" | "B";

export interface MarkdownProps extends MarkdownAdapterProps {
  /** Spike only — (A) react-native-enriched-markdown (native, needs a new dev
   *  build) or (B) marked + own renderer (pure JS, default). */
  engine?: MarkdownEngine;
}

/** Assistant/reader markdown: Arabic RTL, GFM tables, `[n]` citations as
 *  pills calling `onCitationPress`, http(s) links in the in-app browser. */
export function Markdown({ content, engine = "B", ...rest }: MarkdownProps) {
  // TODO(K1): stripMarkdownImages → linkifyCitations from @contracts.
  const prepared = useMemo(() => linkifyCitations(content), [content]);
  return engine === "A" ? <EnrichedMarkdown content={prepared} {...rest} /> : <MarkedMarkdown content={prepared} {...rest} />;
}
