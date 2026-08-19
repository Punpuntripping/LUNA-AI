import type { Metadata } from "next";
import { ComplianceHubView } from "@/components/library/hub/ComplianceHubView";

// Public SEO hub — page 1 of /compliance, backed by `service_guides`: 169
// guides to the most-used Saudi government services, each one OUR OWN authored
// rewrite of the issuing entity's official PDF user-guide, published in full and
// ungated. Server component, ISR via the fetch revalidate window in
// `lib/library/api.ts` (NO force-dynamic).
//
// The hub is the directory; the value — and the indexable surface — is the
// per-guide page at `/compliance/{slug}`, which the backend sitemap section
// carries in full regardless of how deep an anonymous visitor may page here.

const HUB_TITLE = "دليل الخدمات الحكومية";
const HUB_DESCRIPTION =
  "دليل مبسط لأكثر الخدمات الحكومية السعودية استخداماً، وأين تُنجز كل خدمة على موقع الجهة الرسمي.";

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
