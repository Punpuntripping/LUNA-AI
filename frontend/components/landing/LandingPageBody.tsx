import { LandingHero } from "@/components/landing/LandingHero";
import { ProblemSection } from "@/components/landing/ProblemSection";
import { AboutSection } from "@/components/landing/AboutSection";
import { AudiencesTeaser } from "@/components/audiences/AudiencesTeaser";
import { SearchShowcase } from "@/components/landing/SearchShowcase";
import { CapabilitiesSection } from "@/components/landing/CapabilitiesSection";
import { ComparisonSection } from "@/components/landing/ComparisonSection";
import { StatsBand } from "@/components/landing/StatsBand";
import { TrustSection } from "@/components/landing/TrustSection";
import { PricingSection } from "@/components/landing/PricingSection";
import { FinalCtaSection } from "@/components/landing/FinalCtaSection";

/**
 * The full marketing-page section stack, shared verbatim by the anonymous
 * front door (`/`) and the always-viewable `/about_us` route (the same content
 * signed-in users can reach, since `/` bounces them to /chat). Server
 * component — keep it presentation-only so both routes stay prerenderable.
 */
export function LandingPageBody() {
  return (
    <main>
      <LandingHero />
      <ProblemSection />
      <AboutSection />
      <AudiencesTeaser />
      <SearchShowcase />
      <CapabilitiesSection />
      <ComparisonSection />
      <StatsBand />
      <TrustSection />
      <PricingSection />
      <FinalCtaSection />
    </main>
  );
}
