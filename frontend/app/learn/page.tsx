import type { Metadata } from "next";
import { SitePageShell } from "@/components/site/SitePageShell";
import { ComingSoonHub } from "@/components/site/ComingSoonHub";

export const metadata: Metadata = {
  title: "اكتشف ريحان",
  description:
    "أدلة ريحان: كيف يعمل ريحان، دليل الاستخدام، أفضل الممارسات لصياغة أسئلتك القانونية، وأمثلة أسئلة حقيقية.",
  // Placeholder hub — keep it out of the index until the lessons land.
  robots: { index: false, follow: true },
};

// اكتشف ريحان hub — the endpoint exists so the header slot resolves today; the
// lesson pages (how-it-works, guide, best-practices, examples) fill it in
// Phase C, co-authored with the edu-popups content. Public (AuthGuard prefix).
// eslint-disable-next-line import/no-default-export
export default function LearnHubPage() {
  return (
    <SitePageShell>
      <ComingSoonHub
        title="اكتشف ريحان"
        description="نُحضّر أدلة تشرح كيف يعمل ريحان خطوة بخطوة، وأفضل الممارسات لصياغة سؤالك القانوني والحصول على أدقّ النتائج، مع أمثلة أسئلة حقيقية وإجاباتها."
        cta={{ href: "/about_us", label: "تعرّف على ريحان" }}
      />
    </SitePageShell>
  );
}
