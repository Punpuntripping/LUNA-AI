import type { Metadata } from "next";
import { RegulationsHubView } from "@/components/library/hub/RegulationsHubView";

// Public SEO hub — page 1 of /regulations. Server component, ISR via the fetch
// revalidate window in `lib/library/api.ts` (NO force-dynamic).

const HUB_TITLE = "الأنظمة واللوائح السعودية";
const HUB_DESCRIPTION =
  "دليل الأنظمة واللوائح السعودية — ملخّصات ومواد ومصادر رسمية موثّقة عبر ريحان.";

export function generateMetadata(): Metadata {
  const title = `${HUB_TITLE} | ريحان`;
  const ogImage = `/og?title=${encodeURIComponent(HUB_TITLE)}`;
  return {
    title,
    description: HUB_DESCRIPTION,
    alternates: { canonical: "/regulations" },
    openGraph: {
      title,
      description: HUB_DESCRIPTION,
      siteName: "ريحان",
      type: "website",
      locale: "ar_SA",
      url: "/regulations",
      images: [{ url: ogImage, width: 1200, height: 630, alt: HUB_TITLE }],
    },
    twitter: { card: "summary_large_image", title, description: HUB_DESCRIPTION, images: [ogImage] },
  };
}

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default function RegulationsHubPage() {
  return <RegulationsHubView page={1} />;
}
