import type { Metadata } from "next";
import { FormsHubView } from "@/components/library/hub/FormsHubView";

// Public SEO hub — page 1 of /forms (نماذج). PUBLISHED forms only; empty until a
// reviewer approves + publishes. Server component, ISR (NO force-dynamic).

const HUB_TITLE = "النماذج القانونية الجاهزة";
const HUB_DESCRIPTION =
  "نماذج وصيغ قانونية سعودية جاهزة — متى تُستخدم وأساسها النظامي، وفتحها مباشرة في ريحان.";

export function generateMetadata(): Metadata {
  const title = `${HUB_TITLE} | ريحان`;
  const ogImage = `/og?title=${encodeURIComponent(HUB_TITLE)}`;
  return {
    title,
    description: HUB_DESCRIPTION,
    alternates: { canonical: "/forms" },
    openGraph: {
      title,
      description: HUB_DESCRIPTION,
      siteName: "ريحان",
      type: "website",
      locale: "ar_SA",
      url: "/forms",
      images: [{ url: ogImage, width: 1200, height: 630, alt: HUB_TITLE }],
    },
    twitter: { card: "summary_large_image", title, description: HUB_DESCRIPTION, images: [ogImage] },
  };
}

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default function FormsHubPage() {
  return <FormsHubView page={1} />;
}
