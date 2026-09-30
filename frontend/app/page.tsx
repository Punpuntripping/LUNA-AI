import type { Metadata } from "next";
import { LandingPageBody } from "@/components/landing/LandingPageBody";
import { SitePageShell } from "@/components/site/SitePageShell";
import { ogImageUrl } from "@/lib/seo/og";

const OG_TITLE = "ريحان — مساعد المحامي السعودي في البحث والصياغة";
const OG_IMAGE = ogImageUrl("مساعد المحامي السعودي في البحث والصياغة");

export const metadata: Metadata = {
  title: OG_TITLE,
  description:
    "ريحان يبحث للمحامي في الأنظمة السعودية وأكثر من 30,000 حكم قضائي، ويصوغ المذكرات واللوائح بمراجع مرقّمة — كل استشهاد مربوط بمصدره الرسمي ورابطه المباشر.",
  alternates: {
    canonical: "/",
  },
  openGraph: {
    title: OG_TITLE,
    description:
      "من البحث القانوني إلى المذكرة الجاهزة، موثّقة بالأنظمة والأحكام السعودية ومصادرها الرسمية.",
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
