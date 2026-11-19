import { notFound } from "next/navigation";
import { MY_FILES_ENABLED } from "@/lib/features";
import { SidebarPageShell } from "@/components/shell/SidebarPageShell";

/**
 * «الملفات» (`/files`, `/files/[itemId]`) in the app shell — Sidebar, no public
 * site header — like the other per-user collections (/templates, /blogs,
 * /library/mine), all reached from the sidebar nav.
 *
 * Private by default: `/files` is not in AuthGuard's PUBLIC_PREFIXES.
 *
 * Flag off → every /files URL is not-found. The check lives in this layout,
 * above `[itemId]/loading.tsx`, so it runs before that Suspense boundary starts
 * streaming.
 */
// Next.js App Router requires a default export for layout files
// eslint-disable-next-line import/no-default-export
export default function FilesLayout({ children }: { children: React.ReactNode }) {
  if (!MY_FILES_ENABLED) notFound();
  return <SidebarPageShell>{children}</SidebarPageShell>;
}
