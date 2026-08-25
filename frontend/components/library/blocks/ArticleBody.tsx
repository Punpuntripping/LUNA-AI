import { cn } from "@/lib/utils";
import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";
import { GateBanner } from "@/components/library/blocks/GateBanner";
import { LegalBlocks } from "@/components/library/blocks/LegalBlocks";
import {
  toLegalBlocks,
  dropDuplicateLeadingHeading,
} from "@/lib/library/legal-text";
import type { ArticleBodyProps } from "@/types/library";

/**
 * Renders the VISIBLE document body (نص المادة، ملخص الوقائع، متن التعميم…) and,
 * when `gate.isTruncated`, drops a GateBanner immediately after it.
 *
 * Pass `plain` to render pre-formatted legal text as typed blocks (see
 * `toLegalBlocks` + `LegalBlocks`): chapter lines and مادة headers gain their
 * own rhythm, clause numbers attach to their clause, sub-clauses («أ- …»)
 * group into a tight list, `**bold**` term definitions render bold, and
 * everything else stays verbatim (no table/citation parsing — that's what the
 * chat `MarkdownRenderer` fallback is for). This keeps legal text predictable.
 *
 * `tables` (plain only): sanitized table markup keyed by the `TBL_…` token that
 * stands in for it inside `visibleText`, so a statute's grids render as grids
 * instead of the flattened prose the corpus indexes. A token that does not
 * resolve renders NOTHING — never a raw `TBL_…` on a statute page. Absent —
 * which is what every non-regulation caller passes, and what every ISR payload
 * baked before the backend shipped this forces — the placeholder branch does
 * not run at all and the body renders exactly as it did before.
 *
 * `dedupeHeading` (plain only): when the FIRST rendered block is a heading or
 * clause label that duplicates this value (colon/whitespace-insensitive), it is
 * dropped — used where a styled section `<h2>` already renders the same title
 * the body repeats.
 *
 * `headingAnchors` (markdown path only): give `h1..h6` deterministic
 * `slugifyHeading` ids so a table of contents can link INTO the body. Opt-in and
 * default-off — the ids are only meaningful to a surface that also builds hrefs
 * from the same slugger (`/compliance/{slug}`, and the مدونة before it). Either
 * way the markdown path renders on the reading scale (`prose`).
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
  tables,
  dedupeHeading,
  gateBarsOnly,
  headingAnchors,
  className,
}: ArticleBodyProps) {
  const truncated = Boolean(gate?.isTruncated);
  const blocks = plain
    ? dropDuplicateLeadingHeading(
        toLegalBlocks(visibleText, tables),
        dedupeHeading,
      )
    : [];

  return (
    <div dir="rtl" className={cn(truncated && "gated-body", className)}>
      {plain ? (
        <LegalBlocks blocks={blocks} />
      ) : (
        <MarkdownRenderer
          content={visibleText}
          headingAnchors={headingAnchors}
          prose
        />
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
