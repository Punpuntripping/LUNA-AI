import type { Metadata } from "next";
import { SectorOverviewView } from "@/components/library/sectors/SectorOverviewView";
import { getSectorDetail, getSectors } from "@/lib/library/api";
import { isReservedSectorSlug, sectorHeading } from "@/lib/library/sectors";

// `/library/{sector}` — one القطاع's overview (library_sectors.md §8.3).
// Server component, ISR via the fetch revalidate window (NO force-dynamic, NO
// headers()): §12.8 requires this page STATIC, and `generateStaticParams` below
// is what makes the 38 of them prerender at build.
//
// ⚠ `/library/mine` IS NOT SWALLOWED BY THIS SEGMENT (trap T2). Next resolves a
// STATIC segment before a dynamic sibling, so `app/library/mine/page.tsx` wins
// the match outright — and `AuthGuard.PRIVATE_EXCEPTIONS` re-checks the same
// path ahead of its public-prefix list, so the authed shelf keeps its auth gate
// even though `/library` is a public prefix. `isReservedSectorSlug` is the
// third lock, and the backend 404s `mine`/`page` as the fourth. A regression
// here renders a per-user shelf to anonymous visitors, so it gets four.

const FALLBACK_TITLE = "القطاع | ريحان";

interface PageProps {
  params: Promise<{ sector: string }>;
}

/**
 * Prerender all 38 sector pages at build.
 *
 * `getSectors()` soft-fails to `[]` (see its note in `lib/library/api.ts`) —
 * a backend that has not yet shipped `/public/library/sectors` yields an empty
 * list, the build succeeds with nothing prerendered, and `dynamicParams`
 * (default `true`) still serves every sector on demand + ISR. That is trap T3
 * in its benign form: deploy the BACKEND FIRST, then the frontend, or the bake
 * produces empty hubs and a same-commit rebuild is a Docker-layer no-op.
 */
export async function generateStaticParams(): Promise<{ sector: string }[]> {
  const sectors = await getSectors();
  return sectors.map((sector) => ({ sector: sector.slug }));
}

export async function generateMetadata({
  params,
}: PageProps): Promise<Metadata> {
  const { sector } = await params;
  if (isReservedSectorSlug(sector)) return { title: FALLBACK_TITLE };

  const detail = await getSectorDetail(sector);
  // Unknown slug — the page itself 404s; metadata just must not throw.
  if (!detail) return { title: FALLBACK_TITLE };

  // D6 — Arabic display name, always. The Latin slug is a URL segment and never
  // appears as text.
  const heading = sectorHeading(detail.name_ar);
  const title = `${heading} — المكتبة القانونية | ريحان`;
  const description = `كل ما يخص قطاع ${detail.name_ar} في مكتبة ريحان القانونية: الأنظمة واللوائح، والأحكام القضائية، والخدمات الحكومية، والتعاميم التنظيمية.`;
  const ogImage = `/og?title=${encodeURIComponent(heading)}`;

  return {
    title,
    description,
    alternates: { canonical: `/library/${detail.slug}` },
    openGraph: {
      title,
      description,
      siteName: "ريحان",
      type: "website",
      locale: "ar_SA",
      url: `/library/${detail.slug}`,
      images: [{ url: ogImage, width: 1200, height: 630, alt: heading }],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: [ogImage],
    },
  };
}

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default async function SectorPage({ params }: PageProps) {
  const { sector } = await params;
  return <SectorOverviewView slug={sector} />;
}
