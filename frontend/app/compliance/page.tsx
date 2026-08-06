import type { Metadata } from "next";
import { ComplianceHubView } from "@/components/library/hub/ComplianceHubView";

// Public hub — page 1 of /compliance, backed by `compliance_table` («دليل مبسط
// لأكثر الخدمات استخداماً»). Server component, ISR via the fetch revalidate
// window (NO force-dynamic).
//
// ⚠ LIVE AND EMPTY. `compliance_table` does not exist yet, so this renders the
// empty state. It is `noindex, follow` for exactly that reason — see ROBOTS.

const HUB_TITLE = "دليل الخدمات الحكومية";
const HUB_DESCRIPTION =
  "دليل مبسط لأكثر الخدمات الحكومية السعودية استخداماً، وأين تُنجز كل خدمة على موقع الجهة الرسمي.";

/**
 * ⚠ NOINDEX WHILE THE WING IS EMPTY, and this is the whole gate.
 *
 * An indexed page whose body is «لا توجد خدمات لعرضها حالياً» is a thin-content
 * hit against the domain, and a crawled empty page is one we then have to wait to
 * get re-crawled once it fills. `follow` stays on so the shell's internal links
 * are still worth something.
 *
 * TO LIFT: delete this key in the SAME change that flips
 * `library_service.COMPLIANCE_TABLE_READY` and re-adds the wing to
 * `SITEMAP_SECTIONS` (`lib/seo/sitemap.ts`) + `_LIBRARY_SITEMAP_SECTIONS`
 * (`public_library.py`). All four move together or the wing is either invisible
 * or advertised empty.
 */
const EMPTY_WING_ROBOTS = { index: false, follow: true } as const;

export function generateMetadata(): Metadata {
  const title = `${HUB_TITLE} | ريحان`;
  const ogImage = `/og?title=${encodeURIComponent(HUB_TITLE)}`;
  return {
    title,
    description: HUB_DESCRIPTION,
    alternates: { canonical: "/compliance" },
    robots: EMPTY_WING_ROBOTS,
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
