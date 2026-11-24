import type { Metadata } from "next";
import { AngleMapPage } from "@/components/files/angles/AngleMapPage";
import { FilesFeatureGate } from "@/components/files/FilesFeatureGate";

export const metadata: Metadata = {
  title: "الخيوط — ريحان",
  robots: { index: false, follow: false },
};

// One angle map of «الأوجه» as a page (.claude/plans/angle_map_ui.md rev 3). The
// build flag check and the sidebar shell come from app/files/layout.tsx; the
// per-user check is FilesFeatureGate. Authed and per-user: never cached.
export const dynamic = "force-dynamic";
export const revalidate = 0;

// Next.js App Router requires default export for page files.
// eslint-disable-next-line import/no-default-export
export default async function Page({
  params,
}: {
  params: Promise<{ mapId: string }>;
}) {
  const { mapId } = await params;
  return (
    <FilesFeatureGate>
      <AngleMapPage mapId={mapId} />
    </FilesFeatureGate>
  );
}
