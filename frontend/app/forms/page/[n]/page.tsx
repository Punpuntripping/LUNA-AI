import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { FormsHubView } from "@/components/library/hub/FormsHubView";
import { getFormsHub } from "@/lib/library/api";
import { readVerifiedBotSignal } from "@/lib/library/crawler-signal";

// Public SEO hub — deep pages of /forms at /forms/page/{n}. `n` must be an
// integer ≥ 2 (page 1 lives at /forms, its own canonical); else → 404. Server
// component, ISR (NO force-dynamic).
//
// ⚠ Deep pages only: `readVerifiedBotSignal()` (plan §3.7) reads request headers
// and can take THIS segment dynamic once `EDGE_SECRET` is set. /forms page 1 and
// every /forms/{slug} document page stay statically prerendered + ISR. Full
// reasoning — including why dynamic is required for correctness here and why
// `force-dynamic` must not be added — in `app/regulations/page/[n]/page.tsx`.

const HUB_TITLE = "النماذج القانونية الجاهزة";
const HUB_DESCRIPTION =
  "نماذج وصيغ قانونية سعودية جاهزة — متى تُستخدم وأساسها النظامي، وفتحها مباشرة في ريحان.";

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

  // ⚠ DELIBERATELY UNEXEMPTED — this fetch asks the ANON question and its answer
  // drives `noindex, follow`. Exempting it would hand a crawler an INDEXABLE deep
  // page (index bloat); the body below is exempted instead, which is what buys
  // crawl reach. See `app/regulations/page/[n]/page.tsx`.
  const data = await getFormsHub(pageNum);
  const capped = data?.cap_reached ?? false;

  return {
    title,
    description: HUB_DESCRIPTION,
    alternates: { canonical: `/forms/page/${n}` },
    ...(capped ? { robots: { index: false, follow: true } } : {}),
  };
}

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default async function FormsHubDeepPage({ params }: PageProps) {
  const { n } = await params;
  const pageNum = parsePage(n);
  if (pageNum === null) notFound();
  const verifiedBot = await readVerifiedBotSignal();
  return <FormsHubView page={pageNum} verifiedBot={verifiedBot} />;
}
