import { RegulationCard } from "@/components/library/hub/RegulationCard";
import { JudgmentCard } from "@/components/library/hub/JudgmentCard";
import { ComplianceCard } from "@/components/library/hub/ComplianceCard";
import { CircularCard } from "@/components/library/hub/CircularCard";
import type {
  CircularHubItem,
  ComplianceHubItem,
  RegulationHubItem,
  SectorHubItem,
} from "@/lib/library/api";
import type { LibraryType } from "@/lib/library/sectors";
import type { JudgmentHubItem } from "@/types/library";

/**
 * The 3×3 grid for one sector×type slice.
 *
 * A FILTERED HUB IS NOT A NEW DESIGN SYSTEM (§8.1 / §5B.1). Every card here is
 * the EXISTING wing card, unchanged — a سـجل on `/library/labor-employment/
 * regulations` is pixel-identical to the same سـجل on `/regulations`. If a new
 * card ever seems necessary here, the answer is to fix the wing's card.
 *
 * The wire is JSON, so the item shape is narrowed by `type` rather than by the
 * type system — the same boundary assertion `HubCtaWall` and `lib/library/api`
 * already make on their own payloads.
 */
export function SectorTypeCards({
  type,
  items,
  sectorSlugs,
}: {
  type: LibraryType;
  items: SectorHubItem[];
  sectorSlugs?: Record<string, string>;
}) {
  switch (type) {
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
  }
}
