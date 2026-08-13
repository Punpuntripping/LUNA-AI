import Link from "next/link";
import { FileText, Gavel, Landmark } from "lucide-react";
import { cn } from "@/lib/utils";
import { SectorPills } from "@/components/library/hub/SectorPills";
import {
  corpusLabel,
  hitMeta,
  hitSectors,
  isSearchCorpus,
  type SearchCorpus,
} from "@/lib/search/corpora";
import type { LibrarySearchHit } from "@/hooks/use-search";

/**
 * One row in the `/library` cross-wing result list.
 *
 * ── WHY THIS EXISTS INSTEAD OF REUSING A HUB CARD ───────────────────────────
 * `GET /api/v1/search` answers with a card-agnostic `SearchHit`: corpus + slug +
 * title + facets + url, and per D3 **no snippet**. Every hub card is built the
 * other way round — `RegulationCard` wants `summary_snippet`, `JudgmentCard`
 * wants `snippet`, `CircularCard` wants `body_snippet` — and those excerpts live
 * only on the wing's own hub envelope. That asymmetry is not an oversight in the
 * API: it is the reason the four wings search through their OWN endpoints (§6.2)
 * and only this page calls the cross-wing one. Reusing a hub card here would
 * render a permanent hole where the excerpt goes.
 *
 * So a result is presented as what it actually is — an ADDRESS: which wing, what
 * it is called, who issued it, which sectors it belongs to.
 *
 * ── WHY THE ANCHOR DOES NOT COVER THE WHOLE ROW ─────────────────────────────
 * Same constraint `CardShell` documents: the sector pills are `<Link>`s (D11)
 * and nesting `<a>` inside `<a>` is invalid HTML that the parser silently
 * un-nests, breaking hydration. So the anchor covers the title + meta block and
 * the pills are its SIBLING inside the same frame.
 *
 * Server-renderable (no state, no handlers) — it is pulled into the client graph
 * by the panel that lists it, which is the same arrangement `SectorPills` and
 * `SearchEmptyState` already rely on.
 */

/**
 * A glyph per wing, so a mixed-corpus list is scannable without reading every
 * badge. Deliberately the ONLY per-corpus visual difference: three accent colours
 * would have to come out of the semantic palette (`success` / `warning`), where
 * green already means «ساري» and amber «معدَّل» — a تعميم tinted like a repealed
 * نظام is worse than no colour at all. Icons carry no such meaning in either
 * theme.
 */
const CORPUS_ICON: Record<SearchCorpus, typeof Landmark> = {
  regulation: Landmark,
  judgment: Gavel,
  circular: FileText,
};

export function LibrarySearchResultRow({
  hit,
  sectorSlugs,
}: {
  hit: LibrarySearchHit;
  /** `name_ar → slug` for the sector pills (D11). Omit ⇒ plain-text pills. */
  sectorSlugs?: Record<string, string>;
}) {
  // The wire type is `string`: `/api/v1/search` only ever answers with the three
  // public corpora, but a row that cannot name its wing still renders — titled,
  // linked and unlabelled — rather than crashing the whole result list.
  const corpus = isSearchCorpus(hit.corpus) ? hit.corpus : null;
  const Icon = corpus ? CORPUS_ICON[corpus] : null;
  const meta = corpus ? hitMeta(corpus, hit.facets) : [];
  const sectors = corpus ? hitSectors(corpus, hit.facets) : [];

  const body = (
    <>
      <div className="flex items-start gap-2.5">
        {corpus && (
          <span className="mt-0.5 inline-flex shrink-0 items-center gap-1 rounded-full bg-pill px-2 py-0.5 text-xs font-medium text-pill-fg">
            {Icon && <Icon aria-hidden="true" className="h-3 w-3 shrink-0" />}
            {corpusLabel(corpus)}
          </span>
        )}
        <h3 className="line-clamp-2 flex-1 text-base font-semibold leading-snug text-foreground transition-colors group-hover:text-primary">
          {hit.title}
        </h3>
      </div>

      {meta.length > 0 && (
        // «·» rather than a comma: these are independent labels (الجهة، المحكمة،
        // المدينة), not a list in a sentence, and the separator must not read as
        // Arabic punctuation inside one of them.
        <p className="mt-1.5 truncate text-xs text-muted-foreground">
          {meta.join(" · ")}
        </p>
      )}
    </>
  );

  return (
    <li
      dir="rtl"
      className={cn(
        "group rounded-xl border border-border bg-card p-4 shadow-xs transition-colors",
        hit.url && "hover:border-primary/40",
      )}
    >
      {hit.url ? (
        <Link
          href={hit.url}
          className="block rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {body}
        </Link>
      ) : (
        // No slug ⇒ no published address. Render the row unlinked rather than
        // guessing an href — the rule `search_service.public_url` states from
        // the other side by returning `None`, and the one `CardShell` follows
        // for unlocked-but-unpublished shelf items.
        <div>{body}</div>
      )}

      <SectorPills
        names={sectors}
        slugs={sectorSlugs}
        max={2}
        className="pt-2.5"
      />
    </li>
  );
}
