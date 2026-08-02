"use client";

import { SidebarPageShell } from "@/components/shell/SidebarPageShell";
import { ImportBlogDialog } from "@/components/blogs/ImportBlogDialog";
import { useSidebarStore } from "@/stores/sidebar-store";

interface BlogsLayoutClientProps {
  children: React.ReactNode;
}

/**
 * Layout shell for the /blogs route group (مدوناتي) — the shared
 * SidebarPageShell plus ImportBlogDialog, mounted once so any /blogs surface
 * (the مدوناتي grid) can open it via the sidebar store flag.
 */
export function BlogsLayoutClient({ children }: BlogsLayoutClientProps) {
  const isImportOpen = useSidebarStore((s) => s.isImportBlogDialogOpen);
  const setImportOpen = useSidebarStore((s) => s.setImportBlogDialogOpen);

  return (
    <>
      <SidebarPageShell>{children}</SidebarPageShell>
      <ImportBlogDialog open={isImportOpen} onOpenChange={setImportOpen} />
    </>
  );
}
