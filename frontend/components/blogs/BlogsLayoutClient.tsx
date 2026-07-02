"use client";

import { useEffect } from "react";
import { PanelRightOpen } from "lucide-react";
import { Sidebar } from "@/components/sidebar/Sidebar";
import { Button } from "@/components/ui/button";
import { useSidebarStore } from "@/stores/sidebar-store";

interface BlogsLayoutClientProps {
  children: React.ReactNode;
}

/**
 * Layout shell for the /blogs route group (مدوناتي). Mirrors
 * TemplatesLayoutClient's Sidebar + main split — مدوناتي posts are
 * user-global and don't carry a per-conversation workspace.
 *
 * Forces the sidebar onto the "blogs" tab on mount so the مدوناتي list is
 * visible when the user lands here directly.
 */
export function BlogsLayoutClient({ children }: BlogsLayoutClientProps) {
  const isSidebarOpen = useSidebarStore((s) => s.isOpen);
  const setSidebarOpen = useSidebarStore((s) => s.setOpen);
  const setActiveTab = useSidebarStore((s) => s.setActiveTab);

  useEffect(() => {
    setActiveTab("blogs");
  }, [setActiveTab]);

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      {/* Sidebar — in RTL, this renders on the right side */}
      <Sidebar />

      {/* Main content area */}
      <main className="relative flex-1 flex min-w-0 overflow-hidden">
        {/* Floating sidebar toggle — shown when sidebar is closed on desktop */}
        {!isSidebarOpen && (
          <Button
            variant="ghost"
            size="icon"
            className="absolute top-3 start-3 z-30 h-9 w-9 text-muted-foreground hover:text-foreground"
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
