"use client";

import { useCallback, useEffect } from "react";
import { useParams } from "next/navigation";
import { PanelRightOpen } from "lucide-react";
import { Sidebar } from "@/components/sidebar/Sidebar";
import { WorkspacePane } from "@/components/workspace/WorkspacePane";
import { PromoCodePopup } from "@/components/promo/PromoCodePopup";
import { OnboardingDialog } from "@/components/onboarding/OnboardingDialog";
import TourOverlay from "@/components/tour/TourOverlay";
import { EduLessonHost } from "@/components/edu/EduLessonHost";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { useChatStore } from "@/stores/chat-store";
import { useSidebarStore } from "@/stores/sidebar-store";
import { useIsMobile } from "@/hooks/use-media-query";
import { Button } from "@/components/ui/button";

interface ChatLayoutClientProps {
  children: React.ReactNode;
}

export function ChatLayoutClient({ children }: ChatLayoutClientProps) {
  const params = useParams();
  const conversationId = params?.id as string | undefined;
  const isWorkspaceOpen = useChatStore(
    (s) =>
      (conversationId
        ? s.workspaceByConversation[conversationId]?.isOpen
        : false) ?? false,
  );
  const splitRatio = useChatStore((s) => s.splitRatio);
  const setSplitRatio = useChatStore((s) => s.setSplitRatio);
  const isSidebarOpen = useSidebarStore((s) => s.isOpen);
  const setSidebarOpen = useSidebarStore((s) => s.setOpen);
  const isMobile = useIsMobile();

  // A workspace item arriving mid-stream auto-opens the pane (use-chat.ts,
  // `workspace_item_created`). On a phone the resizable split would give each
  // side ~190px, so below `md` the workspace becomes a full-viewport overlay
  // instead and the chat stays mounted underneath it.
  const showSplit = isWorkspaceOpen && conversationId && !isMobile;
  const showOverlay = isWorkspaceOpen && conversationId && isMobile;

  const closeWorkspace = useChatStore((s) => s.closeWorkspace);

  /**
   * Dismissal contract for the full-viewport mobile overlay: Android back,
   * Escape, and a page that cannot scroll underneath it.
   *
   * The back button is the important one — on a phone the overlay covers the
   * whole conversation, and the system back gesture is what every user reaches
   * for first. Without an entry of our own on the history stack it exited the
   * conversation entirely, losing the thread they were reading.
   *
   * The pushed state is MERGED into whatever the App Router put there
   * (`__NA`, the private tree): the URL is unchanged, so Next's entry stays
   * valid and only gains a marker. On any other kind of dismissal the cleanup
   * pops our entry back off, so «رجوع» never has to be pressed twice.
   */
  useEffect(() => {
    if (!showOverlay || !conversationId) return;

    const href = window.location.href;
    window.history.pushState(
      { ...(window.history.state as Record<string, unknown> | null), wsOverlay: true },
      "",
    );

    const close = () => closeWorkspace(conversationId);
    const handlePopState = () => close();
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };

    window.addEventListener("popstate", handlePopState);
    window.addEventListener("keydown", handleKeyDown);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      window.removeEventListener("popstate", handlePopState);
      window.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
      // Closed by something other than Back (the X, a nav, the desktop
      // breakpoint) → our entry is still on top; drop it. The URL guard keeps
      // this from undoing a real navigation that unmounted the overlay.
      const state = window.history.state as { wsOverlay?: boolean } | null;
      if (state?.wsOverlay && window.location.href === href) {
        window.history.back();
      }
    };
  }, [showOverlay, conversationId, closeWorkspace]);

  const handleLayout = useCallback(
    (sizes: number[]) => {
      // ``sizes`` is [chat, workspace]; we store the chat-side ratio.
      if (sizes.length >= 1 && Number.isFinite(sizes[0])) {
        setSplitRatio(sizes[0]);
      }
    },
    [setSplitRatio],
  );

  return (
    <div className="flex h-dvh overflow-hidden bg-background">
      {/* Sidebar — in RTL, this renders on the right side */}
      <Sidebar />

      {/* Main content area */}
      <main className="relative flex-1 flex min-w-0 overflow-hidden">
        {/* Floating sidebar toggle — DESKTOP ONLY. Below `md` the sidebar
            renders its own fixed hamburger (Sidebar.tsx), and this one stacked
            on top of it at the same corner, intercepting the taps meant for it.
            Gated twice on purpose: `!isMobile` keeps it out of the tree, and
            `max-md:hidden` covers the pre-hydration frame. */}
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

        {showSplit ? (
          <ResizablePanelGroup
            direction="horizontal"
            onLayout={handleLayout}
            className="flex-1"
          >
            <ResizablePanel defaultSize={splitRatio} minSize={25} id="chat">
              <div className="flex h-full flex-col min-w-0 overflow-hidden">
                {children}
              </div>
            </ResizablePanel>
            <ResizableHandle withHandle />
            <ResizablePanel
              defaultSize={100 - splitRatio}
              minSize={25}
              id="workspace"
            >
              <WorkspacePane conversationId={conversationId} />
            </ResizablePanel>
          </ResizablePanelGroup>
        ) : (
          <div className="flex flex-1 flex-col min-w-0 overflow-hidden">
            {children}
          </div>
        )}
      </main>

      {/* Mobile workspace — full-viewport overlay.
          z-[60] deliberately clears the sidebar drawer and its floating menu
          button (both z-50): the full-screen workspace is a focused mode, and
          the X in `PaneHeader` (closeWorkspace) is the way back to the chat.

          ⚠ 60 is a CEILING for app chrome, not a floor. Every portalled Radix
          layer in `components/ui` sits at z-[70] precisely so the surfaces
          opened from inside this overlay — «عرض المصدر», the add-item menu, the
          item action bar — render ABOVE it. When they sat at z-50 the dialog
          opened underneath this div: invisible, while Radix put
          `pointer-events: none` on <body> and locked scroll, so the whole app
          went dead with no tappable way out. Anything new added here must stay
          under 70.
          Safe-area padding is required because `viewportFit: "cover"` lets the
          page paint under the notch and the home indicator. */}
      {showOverlay && (
        <div
          className="fixed inset-0 z-[60] flex flex-col bg-background md:hidden"
          style={{
            paddingTop: "env(safe-area-inset-top)",
            paddingBottom: "env(safe-area-inset-bottom)",
            paddingLeft: "env(safe-area-inset-left)",
            paddingRight: "env(safe-area-inset-right)",
          }}
          role="dialog"
          aria-modal="true"
          aria-label="لوحة العناصر"
        >
          <WorkspacePane conversationId={conversationId} />
        </div>
      )}

      {/* «عندك رمز تفعيل؟» — the two-week activation-code campaign, and the
          HIGHEST-priority chrome in this file: it is the only surface here the
          user may have arrived specifically to use, holding a code off a
          WhatsApp message. Self-gating on plan, on a once-per-account
          preference, and on a hard-coded window that expires the whole campaign
          without a deploy (`components/promo/promo-campaign.ts`). Its sibling
          below stands down while it is owed. */}
      <PromoCodePopup />

      {/* first-run «اتعرف على ريحان» tour — self-gating, renders nothing once seen */}
      <OnboardingDialog />

      {/* «جولة المخرجات» — the 5-step coach-mark tour over the shared demo
          conversation. Also self-gating and also renders null when closed, so
          it mounts unconditionally beside its sibling above.

          The two must never share the screen: the tour gates on
          `preferences.tour_workspace_seen !== true` AND a successful hydrate
          AND «اتعرف على ريحان» being closed (plan §8). Both flags fail CLOSED
          on a hydrate failure — `preferences-store` defaults them to `true` —
          so an API blip can never re-nag an existing user.

          Its root sits at z-[80]: above the mobile workspace overlay (z-[60])
          and above every portalled Radix layer (z-[70]), because Act 3 points
          INSIDE the «عرض المصدر» dialog. */}
      <TourOverlay />

      {/* «سلسلة تعلّم ريحان» — one lesson every 4 completed turns, in syllabus
          order, once each. Self-gating and renders null almost always; every
          rule lives in `edu-store`. Deliberately the LOWEST-priority chrome in
          this file (the card is z-40): it is the one surface here the user did
          not ask for, and the engine refuses to show it while any of the three
          above are open. */}
      <EduLessonHost />

      {/* «حدود الاستخدام» / «إعدادات المحادثة» are NOT mounted here. They hang
          off `Sidebar` (see SidebarDialogs) so they also exist on /templates,
          /blogs and /library/mine, which render the same sidebar through
          SidebarPageShell. Mounting them here as well would double-mount. */}
    </div>
  );
}
