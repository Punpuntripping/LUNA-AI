import { cn } from "@/lib/utils";
import { LegalBlocks } from "@/components/library/blocks/LegalBlocks";
import {
  toLegalBlocks,
  dropDuplicateLeadingHeading,
} from "@/lib/library/legal-text";
import type { LeadSummaryProps } from "@/types/library";

/**
 * The summary lead — the AI-written description that ranks pages and dodges
 * duplicate-content penalties vs official gov text. Same reading scale as the
 * body, set apart by its secondary text colour rather than by size. Markdown
 * heading lines («## النطاق») lift into styled sub-headers with a start-side
 * accent and `**bold**` terms render bold. Server component. Pass EITHER `text`
 * or `children` (children bypass the formatter).
 */
export function LeadSummary({
  text,
  children,
  dedupeHeading,
  className,
}: LeadSummaryProps) {
  if (children) {
    return (
      <div
        dir="rtl"
        className={cn("text-read text-text-secondary", className)}
      >
        {children}
      </div>
    );
  }

  const blocks = text
    ? dropDuplicateLeadingHeading(toLegalBlocks(text), dedupeHeading)
    : [];

  return (
    <div dir="rtl" className={className}>
      <LegalBlocks blocks={blocks} tone="lead" />
    </div>
  );
}
