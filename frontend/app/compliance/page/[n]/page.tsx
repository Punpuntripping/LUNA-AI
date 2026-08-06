import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ComplianceHubView } from "@/components/library/hub/ComplianceHubView";
import { readVerifiedBotSignal } from "@/lib/library/crawler-signal";

// Public hub — deep pages of /compliance at /compliance/page/{n}.
// `n` must be an integer ≥ 2 (page 1 lives at /compliance); else → 404.
//
// ⚠ Deep pages only: `readVerifiedBotSignal()` (plan §3.7) reads request headers
// and can take THIS segment dynamic once `EDGE_SECRET` is set. /compliance page 1
// stays statically prerendered + ISR. Full reasoning — including why dynamic is
// required for correctness here and why `force-dynamic` must not be added — in
// `app/regulations/page/[n]/page.tsx`.
//
// ⚠ The wing is EMPTY until `compliance_table` exists, so page 2 does not exist
// either and every deep page renders the empty state. The route is kept wired so
// pagination works the day the table lands.

const HUB_TITLE = "دليل الخدمات الحكومية";
const HUB_DESCRIPTION =
  "دليل مبسط لأكثر الخدمات الحكومية السعودية استخداماً، وأين تُنجز كل خدمة على موقع الجهة الرسمي.";

/**
 * Unconditional here, unlike the other wings' deep pages. Those compute
 * `noindex` from `cap_reached`; this wing is `noindex` because it is EMPTY, which
 * no fetch can tell us anything about — and skipping the fetch keeps a crawler
 * hitting `/compliance/page/500` from costing a backend round-trip.
 *
 * TO LIFT: see the note in `app/compliance/page.tsx`. Restore the
 * `cap_reached`-driven robots (copy `app/regulations/page/[n]/page.tsx`) in the
 * same change — a filled wing still needs the anon-wall rule.
 */
const EMPTY_WING_ROBOTS = { index: false, follow: true } as const;

interface PageProps {
  params: Promise<{ n: string }>;
}

function parsePage(raw: string): number | null {
  const num = Number(raw);
  if (!Number.isInteger(num) || num < 2) return null;
  return num;
}

export async function generateMetadata({
  params,
}: PageProps): Promise<Metadata> {
  const { n } = await params;
  return {
    title: `${HUB_TITLE} — صفحة ${n} | ريحان`,
    description: HUB_DESCRIPTION,
    alternates: { canonical: `/compliance/page/${n}` },
    robots: EMPTY_WING_ROBOTS,
  };
}

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default async function ComplianceHubDeepPage({ params }: PageProps) {
  const { n } = await params;
  const pageNum = parsePage(n);
  if (pageNum === null) notFound();
  const verifiedBot = await readVerifiedBotSignal();
  return <ComplianceHubView page={pageNum} verifiedBot={verifiedBot} />;
}
