import { LandingHero } from "@/components/landing/LandingHero";
import { SearchShowcase } from "@/components/landing/SearchShowcase";
import { DraftShowcase } from "@/components/landing/DraftShowcase";
import { WorkflowSection } from "@/components/landing/WorkflowSection";
import { ComparisonSection } from "@/components/landing/ComparisonSection";
import { ConfidentialitySection } from "@/components/landing/ConfidentialitySection";
import { StatsBand } from "@/components/landing/StatsBand";
import { AboutSection } from "@/components/landing/AboutSection";
import { LawyerFaqSection } from "@/components/landing/LawyerFaqSection";
import { PricingSection } from "@/components/landing/PricingSection";
import { FinalCtaSection } from "@/components/landing/FinalCtaSection";

/**
 * The full marketing-page section stack, shared verbatim by the anonymous
 * front door (`/`) and the always-viewable `/about_us` route (the same content
 * signed-in users can reach, since `/` bounces them to /chat). Server
 * component — keep it presentation-only so both routes stay prerenderable.
 *
 * Audience: Saudi lawyers only. The breadth story (المختصون / رواد الأعمال /
 * الأفراد) lives on /audiences and is deliberately NOT teased here.
 */
export function LandingPageBody() {
  return (
    <main>
      <LandingHero />
      <SearchShowcase />
      <DraftShowcase />
      <WorkflowSection />
      <ComparisonSection />
      <ConfidentialitySection />
      <StatsBand />
      <AboutSection />
      <LawyerFaqSection />
      <PricingSection />
      <FinalCtaSection />
    </main>
  );
}
