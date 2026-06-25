import type { Metadata } from "next";
import privacyMd from "@/content/legal/privacy-ar.md";
import { LegalPageShell } from "@/components/legal/LegalPageShell";

export const metadata: Metadata = {
  title: "سياسة الخصوصية — ريحان",
  description: "سياسة الخصوصية لمنصة ريحان",
};

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default function PrivacyPage() {
  return <LegalPageShell title="سياسة الخصوصية" content={privacyMd} />;
}
