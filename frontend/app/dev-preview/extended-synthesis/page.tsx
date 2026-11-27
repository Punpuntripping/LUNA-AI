import { Suspense } from "react";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ExtendedSynthesisPreview } from "./_harness/ExtendedSynthesisPreview";

// DEV ONLY — the «بحث موسّع» merged-answer item in the real workspace pane
// (components/workspace/ExtendedSynthesisSections.tsx). 404 outside `next dev`;
// linked from nowhere; public only in development (AuthGuard's
// DEV_PUBLIC_PREFIXES). The fixture is INVENTED text, bundled with the harness
// (./_harness/fixture.ts) — no real user's data, no backend.
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "معاينة البحث الموسّع (داخلي)",
  robots: { index: false, follow: false },
};

// Next.js App Router requires default export for page files.
// eslint-disable-next-line import/no-default-export
export default function Page() {
  if (process.env.NODE_ENV !== "development") notFound();
  return (
    <Suspense fallback={null}>
      <ExtendedSynthesisPreview />
    </Suspense>
  );
}
