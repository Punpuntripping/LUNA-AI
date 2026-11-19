import { Suspense } from "react";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { MyFilesPreview } from "./_harness/MyFilesPreview";

// DEV ONLY — the «الملفات» preview harness (.claude/plans/my_files.md, Phase 1).
// 404 outside `next dev`; linked from nowhere; public only in development
// (AuthGuard's DEV_PUBLIC_PREFIXES). Fixtures come from MY_FILES_FIXTURE_DIR
// through ./fixture/[name]/route.ts at request time.
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "معاينة الملفات (تطوير)",
  robots: { index: false, follow: false },
};

// Next.js App Router requires default export for page files.
// eslint-disable-next-line import/no-default-export
export default function Page() {
  if (process.env.NODE_ENV !== "development") notFound();
  return (
    <Suspense fallback={null}>
      <MyFilesPreview />
    </Suspense>
  );
}
