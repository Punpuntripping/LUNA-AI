import type { Metadata } from "next";
import { SitePageShell } from "@/components/site/SitePageShell";
import { ComingSoonHub } from "@/components/site/ComingSoonHub";

export const metadata: Metadata = {
  title: "المكتبة القانونية — ريحان",
  description:
    "مكتبة ريحان القانونية: الأنظمة واللوائح، الأحكام القضائية، الإجراءات الحكومية، النماذج والصيغ، والحاسبات — مربوطة بمصادرها الرسمية.",
  // Placeholder hub — keep it out of the index until the corpus sections land.
  robots: { index: false, follow: true },
};

// المكتبة القانونية hub — the endpoint exists so the header slot resolves today;
// the real corpus sections (regulations, judgments, compliance, forms,
// calculators, circulars) fill it in later phases. Public (AuthGuard prefix).
// eslint-disable-next-line import/no-default-export
export default function LibraryHubPage() {
  return (
    <SitePageShell>
      <ComingSoonHub
        title="المكتبة القانونية"
        description="نُعِدّ مكتبةً عامة تضم الأنظمة واللوائح، والأحكام القضائية، والإجراءات الحكومية، والنماذج والصيغ، والحاسبات — كلٌّ مربوط بمصدره الرسمي. حتى ذلك الحين، تصفّح مقالاتنا القانونية في المدونة."
        cta={{ href: "/blog", label: "تصفّح المدونة" }}
      />
    </SitePageShell>
  );
}
