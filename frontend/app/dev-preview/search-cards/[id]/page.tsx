import { Suspense } from "react";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { SearchCardsPreview } from "../_harness/SearchCardsPreview";

// DEV ONLY — the live cards («تجهيز المستندات», «بحث عميق», «بحث شامل») and the
// full-page item, inside the REAL chat layout (sidebar + chat | pane). 404
// outside `next dev`; linked from nowhere; public only in development
// (AuthGuard's DEV_PUBLIC_PREFIXES). Invented fixture, fake API, no backend.
// Open /dev-preview/search-cards/preview-extended-conv.
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "معاينة بطاقات البحث (داخلي)",
  robots: { index: false, follow: false },
};

// Next.js App Router requires default export for page files.
// eslint-disable-next-line import/no-default-export
export default function Page() {
  if (process.env.NODE_ENV !== "development") notFound();
  return (
    <Suspense fallback={null}>
      <SearchCardsPreview />
    </Suspense>
  );
}
