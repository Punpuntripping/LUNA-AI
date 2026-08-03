import type { Metadata } from "next";
import { SitePageShell } from "@/components/site/SitePageShell";
import { DataProtectionView } from "@/components/learn/DataProtectionView";
import { buildArticle } from "@/lib/seo/schema";

const TITLE = "كيف نحمي بياناتك وبيانات عملائك؟ — الأمان في ريحان";
const DESCRIPTION =
  "بياناتك محفوظة في خوادم ريحان: معزولة على حسابك، مشفّرة، لا تُباع ولا تُستخدم للتدريب. تعرّف على شركاء المعالجة العالميين مثل Alibaba Cloud، وكيف تقنّع خدمة تقنيع المعرّفات أرقام هويات موكليك وجوالاتهم قبل أن يغادر النص خوادمنا.";
const URL = "https://rayhanai.com/learn/data-protection";
const OG_IMAGE = `/og?title=${encodeURIComponent("كيف نحمي بياناتك؟")}`;

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  alternates: { canonical: "/learn/data-protection" },
  openGraph: {
    title: TITLE,
    description: DESCRIPTION,
    type: "article",
    url: "/learn/data-protection",
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

// «كيف نحمي بياناتك وبيانات عملائك؟» — third اكتشف ريحان lesson
// (discover_rayhan_data_protection.md). Full content, indexable, listed in the
// sitemap's `static` section. Public via the /learn AuthGuard prefix. Every
// claim restates /privacy or /masking — those pages lead, this lesson follows.
// eslint-disable-next-line import/no-default-export
export default function DataProtectionLessonPage() {
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
      <DataProtectionView />
    </SitePageShell>
  );
}
