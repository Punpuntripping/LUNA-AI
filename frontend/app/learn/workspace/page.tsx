import type { Metadata } from "next";
import { SitePageShell } from "@/components/site/SitePageShell";
import { WorkspaceView } from "@/components/learn/WorkspaceView";
import { buildArticle } from "@/lib/seo/schema";

const TITLE = "مساحة العمل في ريحان — ذاكرة موثّقة لمحادثتك القانونية";
const DESCRIPTION =
  "تعرّف على مساحة العمل في ريحان: المسودات ونتائج البحث والملاحظات والمرفقات والمراجع المرقّمة وملخص المحادثة — ولماذا تُحفظ الحقائق المهمة بجانب الحوار حتى تبقى إجابات الوكلاء موثّقة لا مرتجلة.";
const URL = "https://rayhanai.com/learn/workspace";
const OG_IMAGE = `/og?title=${encodeURIComponent("مساحة العمل في ريحان")}`;

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  alternates: { canonical: "/learn/workspace" },
  openGraph: {
    title: TITLE,
    description: DESCRIPTION,
    type: "article",
    url: "/learn/workspace",
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

// «مساحة العمل» — second اكتشف ريحان lesson (discover_rayhan_workspace.md).
// Full content, indexable, listed in the sitemap's `static` section. Public via
// the /learn AuthGuard prefix. Its landing lifted the /learn hub's noindex —
// two live lessons make the hub a real page.
// eslint-disable-next-line import/no-default-export
export default function WorkspaceLessonPage() {
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
      <WorkspaceView />
    </SitePageShell>
  );
}
