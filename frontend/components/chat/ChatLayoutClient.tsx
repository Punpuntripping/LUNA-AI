"use client";

import { useCallback } from "react";
import { useParams } from "next/navigation";
import { PanelRightOpen } from "lucide-react";
import { Sidebar } from "@/components/sidebar/Sidebar";
import { WorkspacePane } from "@/components/workspace/WorkspacePane";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { useChatStore } from "@/stores/chat-store";
import { useSidebarStore } from "@/stores/sidebar-store";
import { Button } from "@/components/ui/button";

interface ChatLayoutClientProps {
  children: React.ReactNode;
}

export function ChatLayoutClient({ children }: ChatLayoutClientProps) {
  const params = useParams();
  const conversationId = params?.id as string | undefined;
  const isWorkspaceOpen = useChatStore((s) => s.workspace.isOpen);
  const splitRatio = useChatStore((s) => s.workspace.splitRatio);
  const setSplitRatio = useChatStore((s) => s.setSplitRatio);
  const isSidebarOpen = useSidebarStore((s) => s.isOpen);
  const setSidebarOpen = useSidebarStore((s) => s.setOpen);

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

        {isWorkspaceOpen && conversationId ? (
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
    </div>
  );
}
