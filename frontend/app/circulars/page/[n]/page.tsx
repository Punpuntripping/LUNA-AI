import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { CircularsHubView } from "@/components/library/hub/CircularsHubView";
import { getCircularsHub } from "@/lib/library/api";
import { readVerifiedBotSignal } from "@/lib/library/crawler-signal";

// Public SEO hub — deep pages of /circulars at /circulars/page/{n}. `n` must be
// an integer ≥ 2 (page 1 lives at /circulars, its own canonical); else → 404.
// Server component, ISR (NO force-dynamic).
//
// ⚠ Deep pages only: `readVerifiedBotSignal()` (plan §3.7) reads request headers
// and can take THIS segment dynamic once `EDGE_SECRET` is set. /circulars page 1
// and every /circulars/{slug} document page stay statically prerendered + ISR.
// Full reasoning — including why dynamic is required for correctness here and why
// `force-dynamic` must not be added — in `app/regulations/page/[n]/page.tsx`.

const HUB_TITLE = "التعاميم التنظيمية السعودية";
const HUB_DESCRIPTION =
  "دليل التعاميم التنظيمية السعودية — نصوصها وجهاتها المصدرة موثّقة عبر ريحان.";

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

  // Anon depth-cap pages (page 4+) return `cap_reached` + render a signup wall —
  // don't index the wall, but let Googlebot follow its links.
  //
  // ⚠ DELIBERATELY UNEXEMPTED — this fetch asks the ANON question and its answer
  // drives `noindex, follow`. Exempting it would hand a crawler an INDEXABLE deep
  // page (index bloat); the body below is exempted instead, which is what buys
  // crawl reach. See `app/regulations/page/[n]/page.tsx`.
  const data = await getCircularsHub(pageNum);
  const capped = data?.cap_reached ?? false;

  return {
    title,
    description: HUB_DESCRIPTION,
    alternates: { canonical: `/circulars/page/${n}` },
    ...(capped ? { robots: { index: false, follow: true } } : {}),
  };
}

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default async function CircularsHubDeepPage({ params }: PageProps) {
  const { n } = await params;
  const pageNum = parsePage(n);
  if (pageNum === null) notFound();
  const verifiedBot = await readVerifiedBotSignal();
  return <CircularsHubView page={pageNum} verifiedBot={verifiedBot} />;
}
