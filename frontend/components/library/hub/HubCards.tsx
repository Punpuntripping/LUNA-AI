import { RegulationCard } from "@/components/library/hub/RegulationCard";
import { ComplianceCard } from "@/components/library/hub/ComplianceCard";
import { CircularCard } from "@/components/library/hub/CircularCard";
import { JudgmentCard } from "@/components/library/hub/JudgmentCard";
import { FormCard } from "@/components/library/hub/FormCard";
import type {
  RegulationHubItem,
  ComplianceHubItem,
  CircularHubItem,
  FormHubItem,
} from "@/lib/library/api";
import type { JudgmentHubItem } from "@/types/library";

/** The five public wings, named exactly as their backend path segment. */
export type HubSection =
  | "regulations"
  | "compliance"
  | "circulars"
  | "judgments"
  | "forms";

/** One card's worth of data, whichever wing it came from. */
export type HubItem =
  | RegulationHubItem
  | ComplianceHubItem
  | CircularHubItem
  | JudgmentHubItem
  | FormHubItem;

/**
 * Render a list of hub items as the wing's own cards.
 *
 * Extracted from `HubCtaWall` so the two client-side paths that produce cards
 * without a server render — the authed depth-cap REVEAL and the live SEARCH
 * results (`HubSearchPanel`) — share one switch instead of two copies that
 * drift. Both feed it the same envelope shape off the same endpoint.
 *
 * ⚠ THE SEARCH RESULT CARD IS THE BROWSE CARD, DELIBERATELY (D3). Search
 * returns no snippet field at all: `search_doc` indexes only text the anon card
 * and doc page already publish, so a result keeps the static free excerpt it
 * renders while browsing — `summary_snippet`, `body_snippet`, `snippet`,
 * `intro_snippet`. There is no `ts_headline`, no `<mark>`, no per-hit
 * access-tier resolution and no `dangerouslySetInnerHTML` anywhere on this
 * path. A leak stops being a code path to keep correct and becomes
 * structurally impossible. Do NOT reintroduce a highlighter here; §6.1 dropped
 * `SearchHighlight` on purpose.
 *
 * The wire is JSON, so the item shape is narrowed by `section` rather than by
 * the type system — the same boundary assertion `lib/library/api.ts` makes on
 * its own fetchers.
 */
export function HubCards({
  section,
  items,
  sectorSlugs,
}: {
  section: HubSection;
  items: HubItem[];
  sectorSlugs?: Record<string, string>;
}) {
  switch (section) {
    case "regulations":
      return (
        <>
          {(items as RegulationHubItem[]).map((item) => (
            <RegulationCard
              key={item.slug}
              item={item}
              sectorSlugs={sectorSlugs}
            />
          ))}
        </>
      );
    case "compliance":
      return (
        <>
          {(items as ComplianceHubItem[]).map((item) => (
            <ComplianceCard
              key={item.slug}
              item={item}
              sectorSlugs={sectorSlugs}
            />
          ))}
        </>
      );
    case "circulars":
      return (
        <>
          {(items as CircularHubItem[]).map((item) => (
            <CircularCard
              key={item.slug}
              item={item}
              sectorSlugs={sectorSlugs}
            />
          ))}
        </>
      );
    case "judgments":
      return (
        <>
          {(items as JudgmentHubItem[]).map((item) => (
            <JudgmentCard
              key={item.slug}
              item={item}
              sectorSlugs={sectorSlugs}
            />
          ))}
        </>
      );
    case "forms":
      return (
        <>
          {(items as FormHubItem[]).map((item) => (
            <FormCard key={item.slug} item={item} />
          ))}
        </>
      );
  }
}
