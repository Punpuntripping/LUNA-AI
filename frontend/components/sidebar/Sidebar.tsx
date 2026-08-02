"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  BookMarked,
  LayoutTemplate,
  Menu,
  Newspaper,
  Scale,
  SquarePen,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { useSidebarStore } from "@/stores/sidebar-store";
import { SidebarHeader } from "@/components/sidebar/SidebarHeader";
import { SidebarFooter } from "@/components/sidebar/SidebarFooter";
import { ConversationList } from "@/components/sidebar/ConversationList";
import { CaseList } from "@/components/sidebar/CaseList";
import { Button } from "@/components/ui/button";
import { TooltipProvider } from "@/components/ui/tooltip";
import { CASES_ENABLED } from "@/lib/features";

interface NavItemProps {
  icon: LucideIcon;
  label: string;
  active?: boolean;
  onClick?: () => void;
  testId?: string;
  /**
   * Render the row as non-interactive with a "قيد التطوير" badge. Used to keep
   * a feature visible in the nav while it is gated off (see lib/features.ts).
   */
  disabled?: boolean;
  disabledBadge?: string;
}

function NavItem({
  icon: Icon,
  label,
  active,
  onClick,
  testId,
  disabled,
  disabledBadge,
}: NavItemProps) {
  if (disabled) {
    return (
      <div
        aria-disabled
        className="flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-muted-foreground/50 cursor-not-allowed select-none"
      >
        <Icon className="h-4 w-4 shrink-0" />
        <span className="flex-1 truncate text-sm">{label}</span>
        {disabledBadge && (
          <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[9px] font-medium leading-none text-muted-foreground">
            {disabledBadge}
          </span>
        )}
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testId}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        active
          ? "bg-accent font-medium text-foreground"
          : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
      )}
    >
      <Icon
        className={cn(
          "h-4 w-4 shrink-0",
          active ? "text-primary" : "text-muted-foreground/70"
        )}
      />
      <span className="truncate">{label}</span>
    </button>
  );
}

export function Sidebar() {
  const router = useRouter();
  const pathname = usePathname();
  const { isOpen, activeTab, setActiveTab, setOpen, setSelectedConversation } =
    useSidebarStore();

  useEffect(() => {
    const handleResize = () => {
      if (window.innerWidth < 768) {
        setOpen(false);
      }
    };
    handleResize();
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [setOpen]);

  // On mobile the sidebar is an overlay — close it when a row navigates away,
  // otherwise the drawer keeps covering the destination page.
  const navigate = (href: string) => {
    if (window.innerWidth < 768) setOpen(false);
    router.push(href);
  };

  const handleNewConversation = () => {
    if (activeTab !== "conversations") setActiveTab("conversations");
    // Lazy creation: do NOT persist a conversation here. Just route to the
    // empty composer (/chat). The conversation row is created only when the
    // user actually sends the first message (see app/chat/page.tsx). This stops
    // empty "محادثة جديدة" rows from piling up every time "+" is clicked.
    setSelectedConversation(null);
    navigate("/chat");
  };

  return (
    <TooltipProvider delayDuration={300}>
      {!isOpen && (
        <div className="fixed top-3 start-3 z-50 md:hidden">
          <Button variant="outline" size="icon" className="h-9 w-9" onClick={() => setOpen(true)}>
            <Menu className="h-4 w-4" />
          </Button>
        </div>
      )}

      {isOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 md:hidden"
          onClick={() => setOpen(false)}
        />
      )}

      <aside
        className={cn(
          "flex flex-col bg-sidebar border-e border-sidebar-border transition-all duration-200 ease-in-out z-50",
          "relative",
          isOpen ? "w-64" : "w-0 overflow-hidden",
          "max-md:fixed max-md:inset-y-0 max-md:start-0",
          isOpen ? "max-md:w-72" : "max-md:w-0"
        )}
      >
        <SidebarHeader />

        {/* Primary action — compose a new conversation */}
        <div className="px-3 pt-3 shrink-0">
          <button
            type="button"
            onClick={handleNewConversation}
            data-testid="sidebar-new-chat"
            className={cn(
              "flex w-full items-center gap-2.5 rounded-lg border border-primary/25 px-2.5 py-2 text-sm font-medium text-primary transition-colors",
              "hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            )}
          >
            <SquarePen className="h-4 w-4 shrink-0" />
            محادثة جديدة
          </button>
        </div>

        {/* Compact nav — each row opens its full page directly. The old
            in-sidebar peek lists (قوالبي/مدوناتي/مكتبتي) are gone: the /mine
            pages are the single browsing surface for each collection. */}
        <nav className="flex flex-col gap-0.5 px-3 pt-3 pb-1 shrink-0">
          {CASES_ENABLED ? (
            <NavItem
              icon={Scale}
              label="القضايا"
              active={activeTab === "cases"}
              onClick={() =>
                setActiveTab(activeTab === "cases" ? "conversations" : "cases")
              }
              testId="sidebar-nav-cases"
            />
          ) : (
            <NavItem
              icon={Scale}
              label="القضايا"
              disabled
              disabledBadge="قيد التطوير"
            />
          )}
          <NavItem
            icon={LayoutTemplate}
            label="قوالبي"
            active={pathname?.startsWith("/templates")}
            onClick={() => navigate("/templates/mine")}
            testId="sidebar-nav-templates"
          />
          <NavItem
            icon={Newspaper}
            label="مدوناتي"
            active={pathname?.startsWith("/blogs")}
            onClick={() => navigate("/blogs/mine")}
            testId="sidebar-nav-blogs"
          />
          <NavItem
            icon={BookMarked}
            label="مكتبتي"
            active={pathname?.startsWith("/library/mine")}
            onClick={() => navigate("/library/mine")}
            testId="sidebar-nav-library"
          />
        </nav>

        {/* Chats — always appended below the nav (cases list swaps in only
            when the gated القضايا feature is re-enabled). */}
        <div className="flex-1 flex flex-col min-h-0">
          {CASES_ENABLED && activeTab === "cases" ? (
            <CaseList />
          ) : (
            <ConversationList />
          )}
        </div>

        <SidebarFooter />
      </aside>
    </TooltipProvider>
  );
}
