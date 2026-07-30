import type { Metadata } from "next";
import { LandingPageBody } from "@/components/landing/LandingPageBody";
import { SitePageShell } from "@/components/site/SitePageShell";

const OG_TITLE = "ريحان — المساعد القانوني الذكي في الأنظمة السعودية";
const OG_IMAGE = `/og?title=${encodeURIComponent("المساعد القانوني الذكي في الأنظمة السعودية")}`;

export const metadata: Metadata = {
  title: OG_TITLE,
  description:
    "ريحان يبحث في الأنظمة السعودية والأحكام القضائية والخدمات الحكومية ويعطيك تقريراً قانونياً كاملاً، كل استشهاد فيه مربوط بمصدره الرسمي ورابطه المباشر.",
  alternates: {
    canonical: "/",
  },
  openGraph: {
    title: OG_TITLE,
    description:
      "من سؤالك إلى تقرير قانوني كامل، موثّق بمصادره الرسمية. بحث في الأنظمة والأحكام والخدمات الحكومية السعودية.",
    type: "website",
    url: "/",
    siteName: "ريحان",
    locale: "ar_SA",
    images: [{ url: OG_IMAGE, width: 1200, height: 630, alt: OG_TITLE }],
  },
  twitter: {
    card: "summary_large_image",
    title: OG_TITLE,
    images: [OG_IMAGE],
  },
};

// Public landing page. Anonymous visitors see this front door; AuthGuard
// bounces authenticated users to /chat (the app home) — they can read the same
// content on /about_us. Server component — fully static, prerendered like
// /pricing and the legal pages.
// eslint-disable-next-line import/no-default-export
export default function LandingPage() {
  return (
    <SitePageShell>
      <LandingPageBody />
    </SitePageShell>
  );
}
