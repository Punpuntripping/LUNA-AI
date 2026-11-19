import type { Metadata } from "next";
import { FilesPage } from "@/components/files/FilesPage";

export const metadata: Metadata = {
  title: "الملفات — ريحان",
  description: "الملفات التي أرسلتها في محادثاتك، بنصها المستخرج وملخصها.",
  // Per-user surface: nothing here belongs in an index.
  robots: { index: false, follow: false },
};

/**
 * `/files` — «الملفات», the user's file library (.claude/plans/my_files.md §3.4).
 *
 * `force-dynamic`: authed and per-user, so never statically rendered or cached.
 * The list itself is fetched client-side with the bearer token; nothing
 * user-specific is produced by this server render.
 */
export const dynamic = "force-dynamic";
export const revalidate = 0;

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default function Page() {
  return <FilesPage />;
}
