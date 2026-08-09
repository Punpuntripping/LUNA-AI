import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { JudgmentsHubView } from "@/components/library/hub/JudgmentsHubView";
import { readParam, type RawSearchParams } from "@/lib/library/hub-query";
import {
  COURT_ORDER,
  courtHeading,
  courtLabel,
  isCourtSlug,
  normalizeCourtSlug,
} from "@/lib/library/courts";

// Deep pages of ONE court section: `/judgments/courts/{court}/page/{n}`.
// `n` must be an integer >= 2 — page 1 lives at `/judgments/courts/{court}`,
// its own canonical; anything else → 404, same rule as
// `app/judgments/page/[n]/page.tsx`. Server component; data ISR via
// `lib/library/api.ts` (NO force-dynamic).
//
// `page` is a RESERVED segment in the court vocabulary on BOTH sides
// (`RESERVED_COURT_SLUGS` here and in `shared/library/courts.py`), so
// `/judgments/courts/page/2` can never resolve as a court in either direction.
//
// ⚠ NOINDEX — PDPL GATE, inherited from the wing. See the note at the top of
// the page-1 sibling. NOTE the `cap_reached` noindex rule the other hubs use
// (`{ index: false, follow: true }` on an anon depth-cap wall) is deliberately
// NOT applied — it would be strictly weaker than the PDPL gate. Restore it as
// the `capped` branch when the gate is lifted.
const NOINDEX_PDPL = { index: false, follow: false } as const;

const WING_TITLE = "الأحكام القضائية السعودية";

interface PageProps {
  params: Promise<{ court: string; n: string }>;
  searchParams: Promise<RawSearchParams>;
}

function parsePage(raw: string): number | null {
  const num = Number(raw);
  if (!Number.isInteger(num) || num < 2) return null;
  return num;
}

/**
 * Page 2 of each of the 12 sections — the entry point into every section's
 * pagination. Deeper pages resolve on demand (`dynamicParams` default), which
 * is right for a `noindex` wing whose deepest section runs to ~2,260 pages: the
 * page count is a backend fact this file must not duplicate.
 *
 * Like the page-1 sibling, this segment reads `searchParams` for the chips and
 * therefore renders at request time today; the list stays because it is the
 * explicit statement of which sections exist.
 */
export function generateStaticParams(): { court: string; n: string }[] {
  return COURT_ORDER.map((court) => ({ court, n: "2" }));
}

export async function generateMetadata({
  params,
}: PageProps): Promise<Metadata> {
  const { court, n } = await params;
  const slug = normalizeCourtSlug(court);
  const label = courtLabel(slug);
  if (!label) {
    return { title: `${WING_TITLE} | ريحان`, robots: NOINDEX_PDPL };
  }

  const heading = courtHeading(label);
  return {
    title: `${heading} — صفحة ${n} | ريحان`,
    description: `${label} — أحكامها ووقائعها وأسبابها ومنطوقها، صفحة ${n} عبر ريحان.`,
    alternates: {
      canonical: `/judgments/courts/${encodeURIComponent(slug)}/page/${n}`,
    },
    robots: NOINDEX_PDPL,
  };
}

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default async function JudgmentsCourtDeepPage({
  params,
  searchParams,
}: PageProps) {
  const { court, n } = await params;
  const slug = normalizeCourtSlug(court);
  if (!isCourtSlug(slug)) notFound();

  const pageNum = parsePage(n);
  if (pageNum === null) notFound();

  const query = await searchParams;
  return (
    <JudgmentsHubView
      page={pageNum}
      filters={{
        court: slug,
        court_level: readParam(query, "court_level"),
        domain: readParam(query, "domain"),
        q: readParam(query, "q"),
      }}
    />
  );
}
