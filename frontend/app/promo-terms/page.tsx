import type { Metadata } from "next";
import promoTermsMd from "@/content/legal/promo-terms-ar.md";
import { LegalPageShell } from "@/components/legal/LegalPageShell";

export const metadata: Metadata = {
  title: "أحكام العروض الترويجية — ريحان",
  description: "أحكام العروض الترويجية في ريحان، وعرض «المشتركون الأوائل»",
  alternates: {
    canonical: "/promo-terms",
  },
};

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default function PromoTermsPage() {
  return <LegalPageShell title="أحكام العروض الترويجية" content={promoTermsMd} />;
}
