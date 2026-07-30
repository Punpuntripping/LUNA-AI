import type { Metadata } from "next";
import { CircularsHubView } from "@/components/library/hub/CircularsHubView";

// Public SEO hub — page 1 of /circulars. Server component, ISR via the fetch
// revalidate window in `lib/library/api.ts` (NO force-dynamic).

const HUB_TITLE = "التعاميم التنظيمية السعودية";
const HUB_DESCRIPTION =
  "دليل التعاميم التنظيمية السعودية — نصوصها وجهاتها المصدرة موثّقة عبر ريحان.";

export function generateMetadata(): Metadata {
  const title = `${HUB_TITLE} | ريحان`;
  const ogImage = `/og?title=${encodeURIComponent(HUB_TITLE)}`;
  return {
    title,
    description: HUB_DESCRIPTION,
    alternates: { canonical: "/circulars" },
    openGraph: {
      title,
      description: HUB_DESCRIPTION,
      siteName: "ريحان",
      type: "website",
      locale: "ar_SA",
      url: "/circulars",
      images: [{ url: ogImage, width: 1200, height: 630, alt: HUB_TITLE }],
    },
    twitter: { card: "summary_large_image", title, description: HUB_DESCRIPTION, images: [ogImage] },
  };
}

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default function CircularsHubPage() {
  return <CircularsHubView page={1} />;
}
