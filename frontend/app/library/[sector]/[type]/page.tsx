import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { SectorTypeListView } from "@/components/library/sectors/SectorTypeListView";
import { getSectorDetail, getSectors } from "@/lib/library/api";
import {
  LIBRARY_TYPES,
  LIBRARY_TYPE_META,
  isLibraryType,
  isReservedSectorSlug,
  sectorTypeHeading,
  sectorTypeRobots,
} from "@/lib/library/sectors";

// `/library/{sector}/{type}` — page 1 of one sector×type list
// (library_sectors.md §8.3, D7: real paginated paths, not client-side tabs).
//
// ⚠ PAGE 1 STAYS STATICALLY PRERENDERED. It imports NOTHING from
// `lib/library/crawler-signal.ts` and calls no dynamic API — reading request
// headers here would opt the segment out of static generation, and page 1 is
// the whole anon-serving strategy. Only the `page/[n]` sibling may go dynamic;
// the full reasoning is in `app/regulations/page/[n]/page.tsx`.

interface PageProps {
  params: Promise<{ sector: string; type: string }>;
}

/**
 * Prerender every NON-EMPTY sector×type combination — ~149 of the 152.
 *
 * The count filter is D9's other half: three combinations hold zero items
 * (تعاميم/tourism-entertainment, أحكام/research-innovation,
 * أحكام/foreign-affairs), no tab is rendered for them anywhere, and
 * `SectorTypeListView` 404s a direct hit. Prerendering a 404 would be wasted
 * build time and a wasted crawl.
 *
 * Soft-fails to `[]` when `/public/library/sectors` is not up yet — see the
 * sibling `[sector]/page.tsx` note and trap T3.
 */
export async function generateStaticParams(): Promise<
  { sector: string; type: string }[]
> {
  const sectors = await getSectors();
  return sectors.flatMap((sector) =>
    LIBRARY_TYPES.filter((type) => (sector.counts[type] ?? 0) > 0).map(
      (type) => ({ sector: sector.slug, type }),
    ),
  );
}

export async function generateMetadata({
  params,
}: PageProps): Promise<Metadata> {
  const { sector, type } = await params;
  if (!isLibraryType(type) || isReservedSectorSlug(sector)) {
    return { title: "المكتبة القانونية | ريحان" };
  }

  const detail = await getSectorDetail(sector);
  if (!detail) return { title: LIBRARY_TYPE_META[type].longLabel };

  const heading = sectorTypeHeading(type, detail.name_ar);
  const title = `${heading} | ريحان`;
  const description = `${LIBRARY_TYPE_META[type].description} — قطاع ${detail.name_ar} في مكتبة ريحان القانونية.`;
  const ogImage = `/og?title=${encodeURIComponent(heading)}`;

  // Always `noindex` since the wing went paid-only (2026-08-11): this page's
  // anonymous body — which, being statically prerendered, is the body Googlebot
  // gets — is the section wall. `follow` still passes crawlers through to the
  // document pages; الأحكام inherit the wing's PDPL `nofollow`. Both rules live
  // in `sectorTypeRobots`, one place, shared with the sitemap.
  const robots = sectorTypeRobots(type);

  return {
    title,
    description,
    alternates: { canonical: `/library/${detail.slug}/${type}` },
    robots,
    openGraph: {
      title,
      description,
      siteName: "ريحان",
      type: "website",
      locale: "ar_SA",
      url: `/library/${detail.slug}/${type}`,
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
export default async function SectorTypePage({ params }: PageProps) {
  const { sector, type } = await params;
  // `{type}` is a CLOSED four-value vocabulary — anything else is not a page.
  if (!isLibraryType(type)) notFound();
  return <SectorTypeListView slug={sector} type={type} page={1} />;
}
