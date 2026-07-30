import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { RegulationsHubView } from "@/components/library/hub/RegulationsHubView";
import { getRegulationsHub } from "@/lib/library/api";
import { readVerifiedBotSignal } from "@/lib/library/crawler-signal";

// Public SEO hub — deep pages of /regulations at /regulations/page/{n}.
// `n` must be an integer ≥ 2 (page 1 lives at /regulations, its own canonical);
// anything else → 404. Server component, ISR (NO force-dynamic).
//
// ⚠ THIS SEGMENT — AND ONLY THIS SEGMENT — CAN GO DYNAMIC (plan §3.7).
// `readVerifiedBotSignal()` reads the incoming request headers, which opts a
// render out of static generation. That is scoped here on purpose:
//
//   · /regulations (page 1) and every /regulations/{slug} document page import
//     nothing from `crawler-signal.ts` and stay statically prerendered + ISR.
//     Page 1 is the whole anon-serving strategy; making it dynamic would be a
//     far worse regression than the problem §3.7 solves.
//   · Deep pages are the ONLY ones the anon depth cap blocks, so they are the
//     only ones Googlebot cannot otherwise reach — and they are low-traffic,
//     because an anonymous human here gets the CTA wall, not a corpus page.
//   · Dynamic here is also REQUIRED for correctness. If a crawler's uncapped
//     render were written to the Full Route Cache, that HTML would be replayed
//     to every anonymous human for the next hour — the §3.7 edge-cache trap,
//     one layer in. No Full Route Cache, no replay.
//
// The BACKEND load does not change: `next: { revalidate }` still populates the
// Data Cache in a dynamic route (Data Cache ≠ Full Route Cache), and the
// exempted call keys separately because Next hashes `init.headers` into the
// fetch cache key. Only the HTML render is repeated.
//
// ⚠ Do NOT add `export const dynamic = "force-dynamic"` as "documentation" —
// implicit bailout leaves `fetchCache` at `auto`, which is what keeps the Data
// Cache in play. And until `EDGE_SECRET` is set this segment does not go dynamic
// at all: `readVerifiedBotSignal()` returns before touching `headers()`.

const HUB_TITLE = "الأنظمة واللوائح السعودية";
const HUB_DESCRIPTION =
  "دليل الأنظمة واللوائح السعودية — ملخّصات ومواد ومصادر رسمية موثّقة عبر ريحان.";

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
  // ⚠ DELIBERATELY UNEXEMPTED — do not pass the §3.7 signal here. This fetch
  // asks the ANON question ("is this depth capped?"), and the answer drives
  // `noindex, follow`. Exempting it would report `cap_reached: false` to a
  // crawler and hand it an INDEXABLE deep page, turning §3.7 from crawl reach
  // into index bloat. The split is the design: metadata stays capped-and-
  // noindexed, the body below is exempted so the crawler can follow its links
  // down to the document pages. That is the discovery path §3.7 exists to open.
  const data = await getRegulationsHub(pageNum);
  const capped = data?.cap_reached ?? false;

  return {
    title,
    description: HUB_DESCRIPTION,
    alternates: { canonical: `/regulations/page/${n}` },
    ...(capped ? { robots: { index: false, follow: true } } : {}),
  };
}

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default async function RegulationsHubDeepPage({ params }: PageProps) {
  const { n } = await params;
  const pageNum = parsePage(n);
  if (pageNum === null) notFound();
  const verifiedBot = await readVerifiedBotSignal();
  return <RegulationsHubView page={pageNum} verifiedBot={verifiedBot} />;
}
