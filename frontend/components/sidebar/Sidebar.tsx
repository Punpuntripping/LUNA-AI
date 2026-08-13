"use client";

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
import { useIsMobile, useIsMobileNow } from "@/hooks/use-media-query";
import { SidebarHeader } from "@/components/sidebar/SidebarHeader";
import { SidebarFooter } from "@/components/sidebar/SidebarFooter";
import { ConversationList } from "@/components/sidebar/ConversationList";
import { CaseList } from "@/components/sidebar/CaseList";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
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
          <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-xs font-medium leading-none text-muted-foreground">
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
  // Two readings of the same breakpoint, deliberately:
  //  • `useIsMobileNow` decides whether the DRAWER (a portalled Radix layer)
  //    exists at all — it must be right on the very first client render, or a
  //    desktop load runs the sheet's scroll-lock effects for one commit.
  //  • `useIsMobile` drives ordinary post-hydration behaviour (closing the
  //    drawer on navigation), where the extra render costs nothing.
  const isMobileNow = useIsMobileNow();
  const isMobile = useIsMobile();

  // On mobile the sidebar is a drawer over the page — close it when a row
  // navigates away, otherwise it keeps covering the destination.
  const navigate = (href: string) => {
    if (isMobile) setOpen(false);
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

  // Inside the mobile drawer the sidebar is by definition expanded — `isOpen`
  // is what put the drawer on screen. On desktop it drives the rail's collapse.
  const body = (
    <>
      <SidebarHeader expanded={isMobileNow ? true : isOpen} />

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
    </>
  );

  // ──────────────────────────────────────────────────────────────────────
  // Below `md` the sidebar is a DRAWER, and a drawer is a modal surface:
  // focus trap, scroll lock, Escape, and — the one that mattered most on a
  // phone — the Android back button, all of which Radix gives us for free.
  // The hand-rolled `max-md:fixed` aside + `bg-black/50` scrim it replaces had
  // none of them, and its unthrottled resize listener fought the store on
  // every keyboard open. Desktop keeps the in-flow collapsible rail.
  //
  // Only ONE of the two branches is ever mounted, so there is exactly one
  // `sidebar-new-chat` / `sidebar-nav-*` node in the document at any width.
  // ──────────────────────────────────────────────────────────────────────
  if (isMobileNow) {
    return (
      <TooltipProvider delayDuration={300}>
        {!isOpen && (
          <div className="fixed top-3 start-3 z-50 md:hidden">
            <Button
              variant="outline"
              size="icon"
              className="h-9 w-9"
              onClick={() => setOpen(true)}
              aria-label="فتح الشريط الجانبي"
              data-testid="sidebar-open-mobile"
            >
              <Menu className="h-4 w-4" />
            </Button>
          </div>
        )}

        <Sheet open={isOpen} onOpenChange={setOpen}>
          <SheetContent
            side="start"
            showClose={false}
            // The drawer carries its own header (with the collapse button) and
            // its own footer, so the sheet contributes structure only: no gap,
            // no padding, sidebar surface colour.
            className="w-72 gap-0 border-e border-sidebar-border bg-sidebar p-0"
            aria-describedby={undefined}
          >
            <SheetTitle className="sr-only">الشريط الجانبي</SheetTitle>
            {body}
          </SheetContent>
        </Sheet>
      </TooltipProvider>
    );
  }

  return (
    <TooltipProvider delayDuration={300}>
      <aside
        className={cn(
          "relative z-50 flex flex-col bg-sidebar border-e border-sidebar-border",
          "transition-all duration-200 ease-in-out",
          isOpen ? "w-64" : "w-0 overflow-hidden"
        )}
      >
        {body}
      </aside>
    </TooltipProvider>
  );
}
