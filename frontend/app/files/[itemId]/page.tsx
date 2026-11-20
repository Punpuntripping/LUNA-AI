import type { Metadata } from "next";
import { LibraryFilePage } from "@/components/files/LibraryFilePage";
import { FilesFeatureGate } from "@/components/files/FilesFeatureGate";

export const metadata: Metadata = {
  title: "الملفات — ريحان",
  robots: { index: false, follow: false },
};

// One file of «الملفات» as a page (.claude/plans/my_files.md §3.4). The build
// flag check and the sidebar shell come from app/files/layout.tsx; the
// per-user check is FilesFeatureGate. Authed and per-user: never cached.
export const dynamic = "force-dynamic";
export const revalidate = 0;

// Next.js App Router requires default export for page files.
// eslint-disable-next-line import/no-default-export
export default async function Page({
  params,
}: {
  params: Promise<{ itemId: string }>;
}) {
  const { itemId } = await params;
  return (
    <FilesFeatureGate>
      <LibraryFilePage itemId={itemId} />
    </FilesFeatureGate>
  );
}
