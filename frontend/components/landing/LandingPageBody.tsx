import { LandingHero } from "@/components/landing/LandingHero";
import { SearchShowcase } from "@/components/landing/SearchShowcase";
import { DraftShowcase } from "@/components/landing/DraftShowcase";
import { SectorsSection } from "@/components/landing/SectorsSection";
import { WorkflowSection } from "@/components/landing/WorkflowSection";
import { ComparisonSection } from "@/components/landing/ComparisonSection";
import { TestimonialsSection } from "@/components/landing/TestimonialsSection";
import { ConfidentialitySection } from "@/components/landing/ConfidentialitySection";
import { StatsBand } from "@/components/landing/StatsBand";
import { AboutSection } from "@/components/landing/AboutSection";
import { WhoBenefitsSection } from "@/components/landing/WhoBenefitsSection";
import { LawyerFaqSection } from "@/components/landing/LawyerFaqSection";
import { PricingSection } from "@/components/landing/PricingSection";
import { FinalCtaSection } from "@/components/landing/FinalCtaSection";

/**
 * The full marketing-page section stack, shared verbatim by the anonymous
 * front door (`/`) and the always-viewable `/about_us` route (the same content
 * signed-in users can reach, since `/` bounces them to /chat). Server
 * component — keep it presentation-only so both routes stay prerenderable.
 *
 * Audience: ~80% Saudi lawyers. The breadth story gets two blocks — the sector
 * examples (SectorsSection) and «من يستفيد من ريحان؟» (lawyer featured, the
 * other personas linking into /audiences).
 */
export function LandingPageBody() {
  return (
    <main>
      <LandingHero />
      <SearchShowcase />
      <DraftShowcase />
      <SectorsSection />
      <WorkflowSection />
      <ComparisonSection />
      <TestimonialsSection />
      <ConfidentialitySection />
      <StatsBand />
      <AboutSection />
      <WhoBenefitsSection />
      <LawyerFaqSection />
      <PricingSection />
      <FinalCtaSection />
    </main>
  );
}
