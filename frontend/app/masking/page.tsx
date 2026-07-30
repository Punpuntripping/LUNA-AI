import type { Metadata } from "next";
import maskingMd from "@/content/legal/masking-ar.md";
import { LegalPageShell } from "@/components/legal/LegalPageShell";

export const metadata: Metadata = {
  title: "تقنيع المعرّفات — ريحان",
  description: "شرح تقنية تقنيع المعرّفات (وضع السرية) في ريحان",
  alternates: {
    canonical: "/masking",
  },
};

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default function MaskingPage() {
  return <LegalPageShell title="تقنيع المعرّفات" content={maskingMd} />;
}
