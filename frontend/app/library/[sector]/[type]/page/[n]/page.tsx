import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { SectorTypeListView } from "@/components/library/sectors/SectorTypeListView";
import { getSectorDetail, getSectorTypeHub } from "@/lib/library/api";
import {
  LIBRARY_TYPE_META,
  isLibraryType,
  isReservedSectorSlug,
  sectorTypeHeading,
  sectorTypeRobots,
} from "@/lib/library/sectors";
import { readVerifiedBotSignal } from "@/lib/library/crawler-signal";

// Deep pages of one sector×type list, at `/library/{sector}/{type}/page/{n}`.
// `n` must be an integer ≥ 2 (page 1 lives at `/library/{sector}/{type}`, its
// own canonical); anything else → 404. Server component, ISR (NO force-dynamic).
//
// ⚠ THIS SEGMENT — AND ONLY THIS SEGMENT — CAN GO DYNAMIC (plan §3.7).
// `readVerifiedBotSignal()` reads the incoming request headers, which opts a
// render out of static generation. Scoped here on purpose, exactly as in
// `app/regulations/page/[n]/page.tsx`, which carries the full reasoning:
// page 1 and the sector overview import nothing from `crawler-signal.ts` and
// stay statically prerendered; deep pages are the only ones the anon depth cap
// blocks, and a crawler's uncapped render must never reach the Full Route Cache
// where it would be replayed to anonymous humans.
//
// ⚠ Do NOT add `export const dynamic = "force-dynamic"` as "documentation" —
// implicit bailout is what keeps `fetchCache` at `auto` and the Data Cache in
// play. Until `EDGE_SECRET` is set this segment does not go dynamic at all:
// `readVerifiedBotSignal()` returns before touching `headers()`.

interface PageProps {
  params: Promise<{ sector: string; type: string; n: string }>;
}

function parsePage(raw: string): number | null {
  const num = Number(raw);
  if (!Number.isInteger(num) || num < 2) return null;
  return num;
}

export async function generateMetadata({
  params,
}: PageProps): Promise<Metadata> {
  const { sector, type, n } = await params;
  if (!isLibraryType(type) || isReservedSectorSlug(sector)) {
    return { title: "المكتبة القانونية | ريحان" };
  }

  const pageNum = parsePage(n);
  if (pageNum === null) {
    return { title: `${LIBRARY_TYPE_META[type].longLabel} — صفحة ${n} | ريحان` };
  }

  // ⚠ THE LIST FETCH HERE IS DELIBERATELY UNEXEMPTED (trap T5) — do NOT pass
  // the §3.7 signal. It asks the ANON question ("is this depth capped?"), and
  // the answer drives `noindex, follow`. Exempting it would report
  // `cap_reached: false` to a crawler and hand it an INDEXABLE deep page,
  // turning §3.7 from crawl reach into index bloat. The split is the design:
  // metadata stays capped-and-noindexed, the BODY below is exempted so the
  // crawler can follow its links down to the document pages. Getting this
  // backwards is the failure mode the trap is named for.
  const [detail, data] = await Promise.all([
    getSectorDetail(sector),
    getSectorTypeHub(type, sector, pageNum),
  ]);
  if (!detail) return { title: LIBRARY_TYPE_META[type].longLabel };

  const heading = sectorTypeHeading(type, detail.name_ar);
  const title = `${heading} — صفحة ${n} | ريحان`;
  const description = `${LIBRARY_TYPE_META[type].description} — قطاع ${detail.name_ar}، صفحة ${n}.`;
  const robots = sectorTypeRobots(
    type,
    detail.counts[type] ?? 0,
    data?.cap_reached ?? false,
  );

  return {
    title,
    description,
    alternates: { canonical: `/library/${detail.slug}/${type}/page/${n}` },
    ...(robots ? { robots } : {}),
  };
}

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default async function SectorTypeDeepPage({ params }: PageProps) {
  const { sector, type, n } = await params;
  if (!isLibraryType(type)) notFound();
  const pageNum = parsePage(n);
  if (pageNum === null) notFound();
  const verifiedBot = await readVerifiedBotSignal();
  return (
    <SectorTypeListView
      slug={sector}
      type={type}
      page={pageNum}
      verifiedBot={verifiedBot}
    />
  );
}
