"use client";

import { useCallback } from "react";
import { useParams } from "next/navigation";
import { PanelRightOpen } from "lucide-react";
import { Sidebar } from "@/components/sidebar/Sidebar";
import { WorkspacePane } from "@/components/workspace/WorkspacePane";
import { OnboardingDialog } from "@/components/onboarding/OnboardingDialog";
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

      {/* first-run «اتعرف على ريحان» tour — self-gating, renders nothing once seen */}
      <OnboardingDialog />
    </div>
  );
}
