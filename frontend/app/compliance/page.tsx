import type { Metadata } from "next";
import { ComplianceHubView } from "@/components/library/hub/ComplianceHubView";

// Public SEO hub — page 1 of /compliance (reads the `services` table). Server
// component, ISR via the fetch revalidate window (NO force-dynamic).

const HUB_TITLE = "خدمات الامتثال الحكومية";
const HUB_DESCRIPTION =
  "أدلّة الخدمات الحكومية السعودية — الشروط والمستندات المطلوبة وخطوات التنفيذ عبر ريحان.";

export function generateMetadata(): Metadata {
  const title = `${HUB_TITLE} | ريحان`;
  const ogImage = `/og?title=${encodeURIComponent(HUB_TITLE)}`;
  return {
    title,
    description: HUB_DESCRIPTION,
    alternates: { canonical: "/compliance" },
    openGraph: {
      title,
      description: HUB_DESCRIPTION,
      siteName: "ريحان",
      type: "website",
      locale: "ar_SA",
      url: "/compliance",
      images: [{ url: ogImage, width: 1200, height: 630, alt: HUB_TITLE }],
    },
    twitter: { card: "summary_large_image", title, description: HUB_DESCRIPTION, images: [ogImage] },
  };
}

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default function ComplianceHubPage() {
  return <ComplianceHubView page={1} />;
}
