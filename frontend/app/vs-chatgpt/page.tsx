import type { Metadata } from "next";
import { SitePageShell } from "@/components/site/SitePageShell";
import { VsChatGptView } from "@/components/marketing/VsChatGptView";
import { buildArticle } from "@/lib/seo/schema";

const TITLE = "ريحان مقابل ChatGPT — لماذا لا تكفي الأدوات العامة للقانون السعودي";
const DESCRIPTION =
  "مقارنة مباشرة بين ريحان والأدوات العامة مثل ChatGPT في العمل القانوني السعودي: دقّة المصادر، تغطية الأنظمة والأحكام، التخصّص للمحامي، واستخراج بيانات المستندات.";
const URL = "https://rayhanai.com/vs-chatgpt";
const OG_IMAGE = `/og?title=${encodeURIComponent("ريحان مقابل الأدوات العامة")}`;

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  alternates: { canonical: "/vs-chatgpt" },
  openGraph: {
    title: TITLE,
    description: DESCRIPTION,
    type: "article",
    url: "/vs-chatgpt",
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

// «ريحان مقابل ChatGPT» — a standalone comparison page reachable from the
// «عن ريحان» header menu and the footer. Full content (indexable, in the
// sitemap's `static` section) — the highest-value item in the pitch menu and a
// free SEO asset for «ChatGPT للمحامين» / «هل أستخدم ChatGPT في القانون» queries.
// Registered in AuthGuard.PUBLIC_PREFIXES.
// eslint-disable-next-line import/no-default-export
export default function VsChatGptPage() {
  const articleLd = buildArticle({
    title: TITLE,
    description: DESCRIPTION,
    url: URL,
    datePublished: "2026-07-23",
  });

  return (
    <SitePageShell>
      <script
        type="application/ld+json"
        // eslint-disable-next-line react/no-danger
        dangerouslySetInnerHTML={{ __html: JSON.stringify(articleLd) }}
      />
      <VsChatGptView />
    </SitePageShell>
  );
}
