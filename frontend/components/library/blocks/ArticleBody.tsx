import { cn } from "@/lib/utils";
import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";
import { GateBanner } from "@/components/library/blocks/GateBanner";
import {
  toLegalBlocks,
  renderInline,
  dropDuplicateLeadingHeading,
} from "@/lib/library/legal-text";
import type { ArticleBodyProps } from "@/types/library";

/**
 * Renders the VISIBLE document body (نص المادة، ملخص الوقائع، متن التعميم…) and,
 * when `gate.isTruncated`, drops a GateBanner immediately after it.
 *
 * Pass `plain` to render pre-formatted legal text as blocks: مادة headers gain a
 * start-side accent, `**bold**` term definitions render bold, and everything
 * else stays verbatim (no list/table/citation parsing — that's what the chat
 * `MarkdownRenderer` fallback is for). This keeps legal text predictable.
 *
 * `dedupeHeading` (plain only): when the FIRST rendered block is a heading that
 * duplicates this value (colon/whitespace-insensitive), it is dropped — used
 * where a styled section `<h2>` already renders the same title the body repeats.
 *
 * `gateBarsOnly`: render the trailing GateBanner as decorative skeleton bars
 * WITHOUT its CTA card — for per-section gates when a single document-level CTA
 * card is the one conversion surface (avoids stacked back-to-back cards).
 *
 * IMPORTANT: `visibleText` is ONLY the visible portion — the server already
 * truncated the gated remainder, so no hidden text reaches the DOM. When gated,
 * the body carries `.gated-body` so the page's paywall JSON-LD fragment
 * (`buildPaywallFragment(".gated-body")`) can target it.
 */
export function ArticleBody({
  visibleText,
  gate,
  plain,
  dedupeHeading,
  gateBarsOnly,
  className,
}: ArticleBodyProps) {
  const truncated = Boolean(gate?.isTruncated);
  const blocks = plain
    ? dropDuplicateLeadingHeading(toLegalBlocks(visibleText), dedupeHeading)
    : [];

  return (
    <div dir="rtl" className={cn(truncated && "gated-body", className)}>
      {plain ? (
        <div className="space-y-4 text-[15px] leading-[1.95] text-foreground sm:text-base sm:leading-[2]">
          {blocks.map((block, index) =>
            block.type === "heading" ? (
              <p
                key={index}
                className="border-s-[3px] border-primary/40 ps-3 pt-1 text-[15px] font-bold leading-snug text-foreground sm:text-base"
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
      ) : (
        <MarkdownRenderer content={visibleText} />
      )}

      {truncated && gate && (
        <GateBanner
          hiddenPlaceholderLines={gate.hiddenPlaceholderLines}
          ctaHref={gate.ctaHref}
          barsOnly={gateBarsOnly}
        />
      )}
    </div>
  );
}
