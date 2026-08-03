import type { Metadata } from "next";
import { LandingPageBody } from "@/components/landing/LandingPageBody";
import { SitePageShell } from "@/components/site/SitePageShell";

export const metadata: Metadata = {
  title: "عن ريحان — المساعد القانوني الذكي في الأنظمة السعودية",
  description:
    "تعرّف على ريحان: بحث موثّق في الأنظمة السعودية والأحكام القضائية، وصياغة قانونية كل استشهاد فيها مربوط بمصدره الرسمي.",
  openGraph: {
    title: "عن ريحان — المساعد القانوني الذكي في الأنظمة السعودية",
    description:
      "من سؤالك إلى تقرير قانوني كامل، موثّق بمصادره الرسمية. بحث في الأنظمة والأحكام القضائية السعودية.",
    type: "website",
  },
};

// «عن ريحان» — the exact marketing-landing content, addressable for EVERYONE.
// The bare "/" bounces authenticated users to /chat, so this route is their
// way back to the public front door (header shows the signed-in variant with
// «العودة إلى ريحان» instead of login CTAs). Anonymous visitors see the same
// page they'd get on rayhanai.com. Registered in AuthGuard.PUBLIC_PREFIXES.
// eslint-disable-next-line import/no-default-export
export default function AboutUsPage() {
  return (
    <SitePageShell>
      <LandingPageBody />
    </SitePageShell>
  );
}
