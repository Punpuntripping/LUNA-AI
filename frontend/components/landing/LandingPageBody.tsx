import { LandingHero } from "@/components/landing/LandingHero";
import { SearchShowcase } from "@/components/landing/SearchShowcase";
import { DraftShowcase } from "@/components/landing/DraftShowcase";
import { ComparisonSection } from "@/components/landing/ComparisonSection";
import { SectorEvidence } from "@/components/landing/SectorEvidence";
import { WorkflowSection } from "@/components/landing/WorkflowSection";
import { TestimonialsSection } from "@/components/landing/TestimonialsSection";
import { ConfidentialitySection } from "@/components/landing/ConfidentialitySection";
import { WhoBenefitsSection } from "@/components/landing/WhoBenefitsSection";
import { LawyerFaqSection } from "@/components/landing/LawyerFaqSection";
import { PricingSection } from "@/components/landing/PricingSection";
import { FinalCtaSection } from "@/components/landing/FinalCtaSection";
import { DIFFERENTIATOR_HEADER } from "@/components/landing/content";

/**
 * The full marketing-page section stack, shared verbatim by the anonymous
 * front door (`/`) and the always-viewable `/about_us` route (the same content
 * signed-in users can reach, since `/` bounces them to /chat). Server
 * component — keep it presentation-only so both routes stay prerenderable.
 *
 * Audience: ~80% Saudi lawyers. The breadth story lives in the differentiator
 * block (comparison table + sector evidence) and «من يستفيد من ريحان؟» (lawyer
 * featured, the other personas linking into /audiences). Trimmed 2026-10-11:
 * StatsBand (repeated the hero numbers) and AboutSection were dropped.
 */
export function LandingPageBody() {
  return (
    <main>
      <LandingHero />
      <SearchShowcase />
      <DraftShowcase />
      <ComparisonSection header={DIFFERENTIATOR_HEADER}>
        <SectorEvidence />
      </ComparisonSection>
      <WorkflowSection />
      <TestimonialsSection />
      <ConfidentialitySection />
      <WhoBenefitsSection />
      <LawyerFaqSection />
      <PricingSection />
      <FinalCtaSection />
    </main>
  );
}
