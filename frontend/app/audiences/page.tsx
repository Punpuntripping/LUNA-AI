import type { Metadata } from "next";
import { LandingHeader } from "@/components/landing/LandingHeader";
import { LandingFooter } from "@/components/landing/LandingFooter";
import { FinalCtaSection } from "@/components/landing/FinalCtaSection";
import { AudiencesHero } from "@/components/audiences/AudiencesHero";
import { AudienceBlock } from "@/components/audiences/AudienceBlock";
import { SectorBand } from "@/components/audiences/SectorBand";
import { AUDIENCES } from "@/components/audiences/content";

export const metadata: Metadata = {
  title: "ريحان يستهدف مين؟ — مساعد قانوني لكل القطاعات السعودية",
  description:
    "ريحان ليس للمحامين وحدهم. يبحث في أنظمة وأحكام وخدمات كل قطاعات المملكة لكل من يتعامل مع نظام سعودي: المحامون، والمختصون من أطباء ومهندسين ومحاسبين، ورواد الأعمال والمستثمرون، والأفراد.",
  openGraph: {
    title: "ريحان يستهدف مين؟",
    description:
      "قاعدة ريحان تغطّي 38 قطاعاً نظامياً — أمثلة حقيقية لما يسأله المحامي والمختص ورائد الأعمال والفرد، كلٌّ موثّق بمصدره الرسمي.",
    type: "website",
  },
};

// Public page (anonymous-accessible — registered in AuthGuard.PUBLIC_PREFIXES).
// Server component, fully static / prerendered like /pricing and the landing.
// eslint-disable-next-line import/no-default-export
export default function AudiencesPage() {
  return (
    <div className="min-h-screen bg-background">
      <LandingHeader />
      <main>
        <AudiencesHero />

        {/* One block per audience — real example questions + official sources. */}
        <section className="mx-auto max-w-5xl px-4 py-16 sm:py-20">
          <div className="grid gap-5 sm:grid-cols-2">
            {AUDIENCES.map((a) => (
              <AudienceBlock key={a.id} audience={a} />
            ))}
          </div>
        </section>

        <SectorBand />
        <FinalCtaSection />
      </main>
      <LandingFooter />
    </div>
  );
}
