import type { Metadata } from "next";
import { JudgmentsHubView } from "@/components/library/hub/JudgmentsHubView";
import { readParam, type RawSearchParams } from "@/lib/library/hub-query";

// Public library hub — page 1 of /judgments. Server component; the DATA is ISR
// via the fetch revalidate window in `lib/library/api.ts` (NO force-dynamic).
// Reading `searchParams` for the filter row makes the RENDER request-time, but
// the upstream fetch is still cached per filter combination.
//
// ⚠ NOINDEX — PDPL GATE. The whole /judgments wing is `noindex, nofollow` until
// the PDPL anonymization audit passes: judgment text may still carry
// party-identifying details, and a crawled page is a page we cannot un-publish.
// TO FLIP AFTER THE AUDIT PASSES:
//   1. delete the `robots` key in `generateMetadata` HERE, in
//      `app/judgments/page/[n]/page.tsx` (keep its cap_reached noindex) and in
//      `app/judgments/[slug]/page.tsx`;
//   2. add "judgments" to SITEMAP_SECTIONS in `lib/seo/sitemap.ts` and give it a
//      case in the `app/sitemaps/[section]` route.
// Nothing else about the wing is provisional — this is the only gate.
const NOINDEX_PDPL = { index: false, follow: false } as const;

const HUB_TITLE = "الأحكام القضائية السعودية";
const HUB_DESCRIPTION =
  "أحكام المحاكم السعودية — وقائعها وأسبابها ومنطوقها والأنظمة المستند إليها، موثّقة عبر ريحان.";

interface PageProps {
  searchParams: Promise<RawSearchParams>;
}

export function generateMetadata(): Metadata {
  const title = `${HUB_TITLE} | ريحان`;
  const ogImage = `/og?title=${encodeURIComponent(HUB_TITLE)}`;
  return {
    title,
    description: HUB_DESCRIPTION,
    // Canonical stays the bare hub: the filtered views are the SAME collection
    // sliced differently, never separate documents.
    alternates: { canonical: "/judgments" },
    robots: NOINDEX_PDPL,
    openGraph: {
      title,
      description: HUB_DESCRIPTION,
      siteName: "ريحان",
      type: "website",
      locale: "ar_SA",
      url: "/judgments",
      images: [{ url: ogImage, width: 1200, height: 630, alt: HUB_TITLE }],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description: HUB_DESCRIPTION,
      images: [ogImage],
    },
  };
}

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default async function JudgmentsHubPage({ searchParams }: PageProps) {
  const params = await searchParams;
  return (
    <JudgmentsHubView
      page={1}
      filters={{
        court_level: readParam(params, "court_level"),
        domain: readParam(params, "domain"),
        q: readParam(params, "q"),
      }}
    />
  );
}
