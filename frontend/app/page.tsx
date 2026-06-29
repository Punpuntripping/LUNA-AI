import type { Metadata } from "next";
import { LandingHeader } from "@/components/landing/LandingHeader";
import { LandingHero } from "@/components/landing/LandingHero";
import { ProblemSection } from "@/components/landing/ProblemSection";
import { AboutSection } from "@/components/landing/AboutSection";
import { SearchShowcase } from "@/components/landing/SearchShowcase";
import { CapabilitiesSection } from "@/components/landing/CapabilitiesSection";
import { StatsBand } from "@/components/landing/StatsBand";
import { TrustSection } from "@/components/landing/TrustSection";
import { PricingSection } from "@/components/landing/PricingSection";
import { FinalCtaSection } from "@/components/landing/FinalCtaSection";
import { LandingFooter } from "@/components/landing/LandingFooter";

export const metadata: Metadata = {
  title: "ريحان — المساعد القانوني الذكي للمحامي السعودي",
  description:
    "ريحان يبحث في الأنظمة السعودية والأحكام القضائية والخدمات الحكومية ويعطيك تقريراً قانونياً كاملاً، كل استشهاد فيه مربوط بمصدره الرسمي ورابطه المباشر.",
  openGraph: {
    title: "ريحان — المساعد القانوني الذكي للمحامي السعودي",
    description:
      "من سؤالك إلى تقرير قانوني كامل، موثّق بمصادره الرسمية. بحث في الأنظمة والأحكام والخدمات الحكومية السعودية.",
    type: "website",
  },
};

// Public landing page. Anonymous visitors see this front door; AuthGuard
// bounces authenticated users to /chat (the app home). Server component — fully
// static, prerendered like /pricing and the legal pages.
// eslint-disable-next-line import/no-default-export
export default function LandingPage() {
  return (
    <div className="min-h-screen bg-background">
      <LandingHeader />
      <main>
        <LandingHero />
        <ProblemSection />
        <AboutSection />
        <SearchShowcase />
        <CapabilitiesSection />
        <StatsBand />
        <TrustSection />
        <PricingSection />
        <FinalCtaSection />
      </main>
      <LandingFooter />
    </div>
  );
}
