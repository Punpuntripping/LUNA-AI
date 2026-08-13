"use client";

import { PanelRightOpen } from "lucide-react";
import { Sidebar } from "@/components/sidebar/Sidebar";
import { Button } from "@/components/ui/button";
import { useSidebarStore } from "@/stores/sidebar-store";
import { useIsMobile } from "@/hooks/use-media-query";

interface SidebarPageShellProps {
  children: React.ReactNode;
}

/**
 * App shell for full-page surfaces that live beside the chat: Sidebar + main
 * pane + the floating reopen toggle. Shared by /templates, /blogs and
 * /library/mine so the three collections carry identical chrome (previously
 * TemplatesLayoutClient and BlogsLayoutClient were copies of this markup).
 */
export function SidebarPageShell({ children }: SidebarPageShellProps) {
  const isSidebarOpen = useSidebarStore((s) => s.isOpen);
  const setSidebarOpen = useSidebarStore((s) => s.setOpen);
  const isMobile = useIsMobile();

  return (
    <div className="flex h-dvh overflow-hidden bg-background">
      {/* Sidebar — in RTL, this renders on the right side */}
      <Sidebar />

      {/* Main content area */}
      <main className="relative flex-1 flex min-w-0 overflow-hidden">
        {/* Floating sidebar toggle — DESKTOP ONLY; below `md` the sidebar's own
            fixed hamburger owns this corner (see ChatLayoutClient). */}
        {!isSidebarOpen && !isMobile && (
          <Button
            variant="ghost"
            size="icon"
            className="absolute top-3 start-3 z-30 h-9 w-9 text-muted-foreground hover:text-foreground max-md:hidden"
            onClick={() => setSidebarOpen(true)}
            aria-label="فتح الشريط الجانبي"
          >
            <PanelRightOpen className="h-5 w-5" />
          </Button>
        )}

        <div className="flex flex-1 flex-col min-w-0 overflow-hidden">
          {children}
        </div>
      </main>
    </div>
  );
}
