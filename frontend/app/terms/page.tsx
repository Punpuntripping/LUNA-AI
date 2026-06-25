import type { Metadata } from "next";
import termsMd from "@/content/legal/terms-ar.md";
import { LegalPageShell } from "@/components/legal/LegalPageShell";

export const metadata: Metadata = {
  title: "الشروط والأحكام — ريحان",
  description: "الشروط والأحكام لمنصة ريحان",
};

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default function TermsPage() {
  return <LegalPageShell title="الشروط والأحكام" content={termsMd} />;
}
