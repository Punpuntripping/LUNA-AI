import { Suspense } from "react";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { AngleMapPreview } from "./_harness/AngleMapPreview";

// DEV ONLY — the angle-map («الأوجه») preview harness (.claude/plans/angle_map_ui.md).
// 404 outside `next dev`; linked from nowhere; public only in development
// (AuthGuard's DEV_PUBLIC_PREFIXES). Fixtures come from ANGLE_MAP_FIXTURE_DIR
// through ./fixture/[name]/route.ts at request time — real users' documents:
// run the server with `next dev -H 127.0.0.1`.
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "معاينة خريطة الأوجه (داخلي)",
  robots: { index: false, follow: false },
};

// Next.js App Router requires default export for page files.
// eslint-disable-next-line import/no-default-export
export default function Page() {
  if (process.env.NODE_ENV !== "development") notFound();
  return (
    <Suspense fallback={null}>
      <AngleMapPreview />
    </Suspense>
  );
}
