import type { Metadata } from "next";
import { SitePageShell } from "@/components/site/SitePageShell";
import { ForLawyersView } from "@/components/marketing/ForLawyersView";
import { buildArticle } from "@/lib/seo/schema";

const TITLE = "ريحان للقانونيين — هل يأخذ الذكاء الاصطناعي مكان المحامي؟";
const DESCRIPTION =
  "إجابات صريحة على مخاوف المحامي السعودي من الذكاء الاصطناعي: وظيفتك، ومعرفتك، وبيانات عملائك. وكيف يقلّص ريحان وقت الصياغة، ويعطيك إلماماً شاملاً بالقضية، ويفتح لك قطاعات خارج تخصّصك.";
const URL = "https://rayhanai.com/for-lawyers";
const OG_IMAGE = `/og?title=${encodeURIComponent("ريحان للقانونيين")}`;

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  alternates: { canonical: "/for-lawyers" },
  openGraph: {
    title: TITLE,
    description: DESCRIPTION,
    type: "article",
    url: "/for-lawyers",
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

// «ريحان للقانونيين» — the objection-handling page in the «عن ريحان» menu,
// beside /audiences and /vs-chatgpt. Deliberately NOT a /learn lesson: it sells
// past a fear rather than teaching a feature. Full content, indexable, in the
// sitemap's `static` section — and a free SEO asset for «هل يغني الذكاء
// الاصطناعي عن المحامي» queries. Registered in AuthGuard.PUBLIC_PREFIXES.
// eslint-disable-next-line import/no-default-export
export default function ForLawyersPage() {
  const articleLd = buildArticle({
    title: TITLE,
    description: DESCRIPTION,
    url: URL,
    datePublished: "2026-08-30",
  });

  return (
    <SitePageShell>
      <script
        type="application/ld+json"
        // eslint-disable-next-line react/no-danger
        dangerouslySetInnerHTML={{ __html: JSON.stringify(articleLd) }}
      />
      <ForLawyersView />
    </SitePageShell>
  );
}
