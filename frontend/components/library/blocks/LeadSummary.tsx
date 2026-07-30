import { cn } from "@/lib/utils";
import {
  toLegalBlocks,
  renderInline,
  dropDuplicateLeadingHeading,
} from "@/lib/library/legal-text";
import type { LeadSummaryProps } from "@/types/library";

/**
 * The summary lead — the AI-written description that ranks pages and dodges
 * duplicate-content penalties vs official gov text. Slightly larger, relaxed
 * type. Markdown heading lines («## النطاق») lift into styled sub-headers with a
 * start-side accent and `**bold**` terms render bold. Server component. Pass
 * EITHER `text` or `children` (children bypass the formatter).
 */
export function LeadSummary({
  text,
  children,
  dedupeHeading,
  className,
}: LeadSummaryProps) {
  const blocks = text
    ? dropDuplicateLeadingHeading(toLegalBlocks(text), dedupeHeading)
    : [];

  return (
    <div
      dir="rtl"
      className={cn(
        "space-y-4 text-base leading-loose text-text-secondary sm:text-[17px]",
        className,
      )}
    >
      {children ??
        blocks.map((block, index) =>
          block.type === "heading" ? (
            // Styled visual sub-header (not a semantic <h#>) so the summary keeps
            // the page's heading outline unchanged — presentation only.
            <p
              key={index}
              className="border-s-[3px] border-primary/50 ps-3 text-base font-bold leading-snug text-foreground sm:text-lg"
            >
              {renderInline(block.text)}
            </p>
          ) : (
            <p key={index} className="whitespace-pre-line">
              {renderInline(block.text)}
            </p>
          ),
        )}
    </div>
  );
}
