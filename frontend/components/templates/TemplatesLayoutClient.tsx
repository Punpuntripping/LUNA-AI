"use client";

import { SidebarPageShell } from "@/components/shell/SidebarPageShell";
import { CreateTemplateDialog } from "@/components/templates/CreateTemplateDialog";

interface TemplatesLayoutClientProps {
  children: React.ReactNode;
}

/**
 * Layout shell for the /templates route group — the shared SidebarPageShell
 * plus CreateTemplateDialog, mounted once so any /templates surface
 * (empty-state landing, قوالبي grid) can open it via the sidebar store flag.
 */
export function TemplatesLayoutClient({ children }: TemplatesLayoutClientProps) {
  return (
    <>
      <SidebarPageShell>{children}</SidebarPageShell>
      <CreateTemplateDialog />
    </>
  );
}
