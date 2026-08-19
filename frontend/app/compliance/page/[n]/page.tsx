import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ComplianceHubView } from "@/components/library/hub/ComplianceHubView";
import { getComplianceHub } from "@/lib/library/api";
import { readVerifiedBotSignal } from "@/lib/library/crawler-signal";

// Public SEO hub — deep pages of /compliance at /compliance/page/{n}.
// `n` must be an integer ≥ 2 (page 1 lives at /compliance); else → 404.
//
// ⚠ Deep pages only: `readVerifiedBotSignal()` (plan §3.7) reads request headers
// and can take THIS segment dynamic once `EDGE_SECRET` is set. /compliance page 1
// and every /compliance/{slug} guide page stay statically prerendered + ISR. Full
// reasoning — including why dynamic is required for correctness here and why
// `force-dynamic` must not be added — in `app/regulations/page/[n]/page.tsx`.

const HUB_TITLE = "دليل الخدمات الحكومية";
const HUB_DESCRIPTION =
  "دليل مبسط لأكثر الخدمات الحكومية السعودية استخداماً، وأين تُنجز كل خدمة على موقع الجهة الرسمي.";

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
  const pageNum = parsePage(n);
  const title = `${HUB_TITLE} — صفحة ${n} | ريحان`;

  if (pageNum === null) {
    return { title, description: HUB_DESCRIPTION };
  }

  // The wing's `noindex` used to be unconditional, because the wing was empty.
  // It carries 169 guides now, so this is back on the sibling wings' rule:
  // anon depth-cap pages return `cap_reached` and render a signup wall — don't
  // index the wall, but let Googlebot follow its links. Crawl reach for the
  // guides themselves does not depend on hub depth at all; the sitemap section
  // lists every guide URL directly.
  //
  // ⚠ DELIBERATELY UNEXEMPTED — this fetch asks the ANON question and its answer
  // drives `noindex, follow`. Exempting it would hand a crawler an INDEXABLE deep
  // page (index bloat); the body below is exempted instead, which is what buys
  // crawl reach. See `app/regulations/page/[n]/page.tsx`.
  const data = await getComplianceHub(pageNum);
  const capped = data?.cap_reached ?? false;

  return {
    title,
    description: HUB_DESCRIPTION,
    alternates: { canonical: `/compliance/page/${n}` },
    ...(capped ? { robots: { index: false, follow: true } } : {}),
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
