import type { Metadata } from "next";
import { LibraryHubView } from "@/components/library/sectors/LibraryHubView";

// `/library` — «المكتبة القانونية», the unified public hub (library_sectors.md
// §8.1). Server component, ISR via the fetch revalidate window in
// `lib/library/api.ts` (NO force-dynamic, NO searchParams — either would opt
// this route out of static generation, and §12.8 requires it STATIC).
//
// ⚠ THE `robots: noindex` THAT USED TO LIVE HERE IS GONE ON PURPOSE (D1). It
// was correct while this was a `ComingSoonHub` placeholder; it is wrong now.
// This is a nav hub like every other one, it carries the only crawlable path
// into the 38 sector pages, and the header + footer both link to it. Do not
// restore it.

const HUB_TITLE = "المكتبة القانونية السعودية";
const HUB_DESCRIPTION =
  "مكتبة ريحان القانونية: الأنظمة واللوائح، والأحكام القضائية، والتعاميم التنظيمية — مرتّبة حسب القطاع ومربوطة بمصادرها الرسمية.";

export function generateMetadata(): Metadata {
  const title = `${HUB_TITLE} | ريحان`;
  const ogImage = `/og?title=${encodeURIComponent(HUB_TITLE)}`;
  return {
    title,
    description: HUB_DESCRIPTION,
    alternates: { canonical: "/library" },
    openGraph: {
      title,
      description: HUB_DESCRIPTION,
      siteName: "ريحان",
      type: "website",
      locale: "ar_SA",
      url: "/library",
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
export default function LibraryHubPage() {
  return <LibraryHubView />;
}
