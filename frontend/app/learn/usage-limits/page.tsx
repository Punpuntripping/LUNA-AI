import type { Metadata } from "next";
import { SitePageShell } from "@/components/site/SitePageShell";
import { UsageLimitsView } from "@/components/learn/UsageLimitsView";
import { buildArticle } from "@/lib/seo/schema";

const TITLE = "سياسة حد الاستخدام — كم تستهلك كل عملية في ريحان؟";
const DESCRIPTION =
  "ريحان يحاسبك بالنقطة لا بعدد الرسائل: البحث المعمّق 3–5 نقاط، صياغة المستند نقطة واحدة، والسؤال العام جزء من نقطة. أرقام مقاسة على استخدام حقيقي، مع ما تتيحه كل باقة في الجلسة والأسبوع.";
const URL = "https://rayhanai.com/learn/usage-limits";
const OG_IMAGE = `/og?title=${encodeURIComponent("سياسة حد الاستخدام")}`;

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  alternates: { canonical: "/learn/usage-limits" },
  openGraph: {
    title: TITLE,
    description: DESCRIPTION,
    type: "article",
    url: "/learn/usage-limits",
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

// «سياسة حد الاستخدام» — fourth اكتشف ريحان lesson. A LESSON, not a legal
// document: it explains what a نقطة buys and why the caps sit where they do, so
// it lives under /learn beside «كيف يعمل ريحان» rather than under الوثائق
// النظامية with /terms and /privacy. /pricing states the allowances; this page
// explains them, and the two must agree — see the ledger note in UsageLimitsView.
// eslint-disable-next-line import/no-default-export
export default function UsageLimitsLessonPage() {
  const articleLd = buildArticle({
    title: TITLE,
    description: DESCRIPTION,
    url: URL,
    datePublished: "2026-08-03",
  });

  return (
    <SitePageShell>
      <script
        type="application/ld+json"
        // eslint-disable-next-line react/no-danger
        dangerouslySetInnerHTML={{ __html: JSON.stringify(articleLd) }}
      />
      <UsageLimitsView />
    </SitePageShell>
  );
}
