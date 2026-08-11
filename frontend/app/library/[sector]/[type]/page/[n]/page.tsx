import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { SectorTypeListView } from "@/components/library/sectors/SectorTypeListView";
import { getSectorDetail } from "@/lib/library/api";
import {
  LIBRARY_TYPE_META,
  isLibraryType,
  isReservedSectorSlug,
  sectorTypeHeading,
  sectorTypeRobots,
} from "@/lib/library/sectors";
// Deep pages of one sector×type list, at `/library/{sector}/{type}/page/{n}`.
// `n` must be an integer ≥ 2 (page 1 lives at `/library/{sector}/{type}`, its
// own canonical); anything else → 404. Server component, ISR (NO force-dynamic).
//
// ⚠ THE §3.7 CRAWLER SIGNAL WAS REMOVED FROM THIS SEGMENT (2026-08-11), AND
// PUTTING IT BACK WOULD BE A REGRESSION, NOT A RESTORATION. This used to be the
// one sector-wing route that could go dynamic: it called `readVerifiedBotSignal()`
// and forwarded the result on the hub fetch, so a verified crawler was served
// past the anon DEPTH cap. The section gate ended that — `section_scope_allowed`
// in `backend/app/api/public_library.py` refuses a sector-scoped request below
// `paid` and deliberately does NOT honour the crawler waiver, because no
// signed-out human reaches this list any more and serving one to Googlebot would
// be cloaking. So the signal could no longer change the answer, while still
// costing this segment its static render (reading request headers is what opts a
// route out). Dead plumbing that also reads as a promise the backend does not
// keep — hence gone, along with the hub fetch in `generateMetadata`.
//
// ⚠ Do NOT add `export const dynamic = "force-dynamic"` as "documentation" —
// implicit bailout is what keeps `fetchCache` at `auto` and the Data Cache in
// play. `app/regulations/page/[n]/page.tsx` still carries the full §3.7
// reasoning and still uses the signal: THAT wing is ungated, so the exemption is
// still live and still correct there.

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

  // ⚠ TRAP T5 IS NOW MOOT HERE, AND THE FETCH IS GONE WITH IT. The robots call
  // used to need this page's own `cap_reached` from a DELIBERATELY UNEXEMPTED
  // fetch, because indexability turned on the anon depth cap. Since the wing
  // went paid-only (2026-08-11) `sectorTypeRobots` answers `noindex` for every
  // page unconditionally, so there is nothing left for a hub fetch to decide —
  // and issuing one anyway would be a per-render round-trip bought for a
  // constant. The BODY below is still §3.7-exempted, which is unchanged: the
  // crawler follows links down to the document pages.
  const detail = await getSectorDetail(sector);
  if (!detail) return { title: LIBRARY_TYPE_META[type].longLabel };

  const heading = sectorTypeHeading(type, detail.name_ar);
  const title = `${heading} — صفحة ${n} | ريحان`;
  const description = `${LIBRARY_TYPE_META[type].description} — قطاع ${detail.name_ar}، صفحة ${n}.`;

  return {
    title,
    description,
    alternates: { canonical: `/library/${detail.slug}/${type}/page/${n}` },
    robots: sectorTypeRobots(type),
  };
}

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default async function SectorTypeDeepPage({ params }: PageProps) {
  const { sector, type, n } = await params;
  if (!isLibraryType(type)) notFound();
  const pageNum = parsePage(n);
  if (pageNum === null) notFound();
  // No crawler signal — see the header note. `SectorTypeListView` no longer
  // takes one either; this was its only caller that ever passed it.
  return <SectorTypeListView slug={sector} type={type} page={pageNum} />;
}
