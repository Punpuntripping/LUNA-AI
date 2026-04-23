"use client";

import { useEffect } from "react";
import { Menu } from "lucide-react";
import { cn } from "@/lib/utils";
import { useSidebarStore } from "@/stores/sidebar-store";
import { SidebarHeader } from "@/components/sidebar/SidebarHeader";
import { SidebarFooter } from "@/components/sidebar/SidebarFooter";
import { ConversationList } from "@/components/sidebar/ConversationList";
import { CaseList } from "@/components/sidebar/CaseList";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { TooltipProvider } from "@/components/ui/tooltip";

export function Sidebar() {
  const { isOpen, activeTab, setActiveTab, setOpen } = useSidebarStore();

  // Close sidebar on mobile when navigating
  useEffect(() => {
    const handleResize = () => {
      if (window.innerWidth < 768) {
        setOpen(false);
      }
    };

    // Set initial state
    handleResize();

    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [setOpen]);

  return (
    <TooltipProvider delayDuration={300}>
      {/* Mobile menu button — shown when sidebar is closed on mobile */}
      {!isOpen && (
        <div className="fixed top-3 start-3 z-50 md:hidden">
          <Button
            variant="outline"
            size="icon"
            className="h-9 w-9"
            onClick={() => setOpen(true)}
          >
            <Menu className="h-4 w-4" />
          </Button>
        </div>
      )}

      {/* Mobile overlay backdrop */}
      {isOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 md:hidden"
          onClick={() => setOpen(false)}
        />
      )}

      {/* Sidebar panel */}
      <aside
        className={cn(
          "flex flex-col bg-sidebar border-e border-sidebar-border transition-all duration-200 ease-in-out z-50",
          // Desktop
          "relative",
          isOpen ? "w-72" : "w-0 overflow-hidden",
          // Mobile: fixed overlay
          "max-md:fixed max-md:inset-y-0 max-md:start-0",
          isOpen ? "max-md:w-72" : "max-md:w-0"
        )}
      >
        {/* Header */}
        <SidebarHeader />

        {/* Tab navigation — flex-1 + min-h-0 so the list scrolls instead of overflowing */}
        <Tabs
          value={activeTab}
          onValueChange={(v) => setActiveTab(v as "conversations" | "cases")}
          className="flex-1 flex flex-col min-h-0 px-2 pt-2"
        >
          <TabsList className="w-full grid grid-cols-2 shrink-0">
            <TabsTrigger value="conversations" className="text-xs">
              المحادثات
            </TabsTrigger>
            <TabsTrigger value="cases" className="text-xs">
              القضايا
            </TabsTrigger>
          </TabsList>

          <TabsContent value="conversations" className="flex-1 flex flex-col min-h-0 mt-0 data-[state=inactive]:hidden">
            <ConversationList />
          </TabsContent>

          <TabsContent value="cases" className="flex-1 flex flex-col min-h-0 mt-0 data-[state=inactive]:hidden">
            <CaseList />
          </TabsContent>
        </Tabs>

        {/* Footer */}
        <SidebarFooter />
      </aside>
    </TooltipProvider>
  );
}
