import type { Metadata } from "next";
import { SitePageShell } from "@/components/site/SitePageShell";
import { HowItWorksView } from "@/components/learn/HowItWorksView";
import { buildArticle } from "@/lib/seo/schema";

const TITLE = "كيف يعمل ريحان — من السؤال إلى التقرير الموثّق";
const DESCRIPTION =
  "تعرّف على وكلاء ريحان الثلاثة: الموجّه الذي يفهم طلبك، والباحث الذي يغوص في المكتبة القانونية بوضعَي بحث الأنظمة والامتثال والأحكام القضائية، والكاتب الذي يصوغ مستنداتك من بحث موثّق وقوالبك الخاصة.";
const URL = "https://rayhanai.com/learn/how-it-works";
const OG_IMAGE = `/og?title=${encodeURIComponent("كيف يعمل ريحان؟")}`;

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  alternates: { canonical: "/learn/how-it-works" },
  openGraph: {
    title: TITLE,
    description: DESCRIPTION,
    type: "article",
    url: "/learn/how-it-works",
    siteName: "ريحان",
    locale: "ar_SA",
    images: [{ url: OG_IMAGE, width: 1200, height: 630, alt: TITLE }],
  },
  twitter: {
    card: "summary_large_image",
    title: TITLE,
    images: [OG_IMAGE],
  },
};

// «كيف يعمل ريحان» — first اكتشف ريحان lesson (discover_rayhan_agents.md).
// Full content, indexable, listed in the sitemap's `static` section. Public via
// the /learn AuthGuard prefix. The /learn hub itself stays noindex until a
// second lesson lands.
// eslint-disable-next-line import/no-default-export
export default function HowItWorksPage() {
  const articleLd = buildArticle({
    title: TITLE,
    description: DESCRIPTION,
    url: URL,
    datePublished: "2026-08-02",
  });

  return (
    <SitePageShell>
      <script
        type="application/ld+json"
        // eslint-disable-next-line react/no-danger
        dangerouslySetInnerHTML={{ __html: JSON.stringify(articleLd) }}
      />
      <HowItWorksView />
    </SitePageShell>
  );
}
