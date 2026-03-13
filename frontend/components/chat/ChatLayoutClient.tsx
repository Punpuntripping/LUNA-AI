"use client";

import { useParams } from "next/navigation";
import { PanelRightOpen } from "lucide-react";
import { Sidebar } from "@/components/sidebar/Sidebar";
import { ArtifactPanel } from "@/components/artifacts/ArtifactPanel";
import { useChatStore } from "@/stores/chat-store";
import { useSidebarStore } from "@/stores/sidebar-store";
import { Button } from "@/components/ui/button";

interface ChatLayoutClientProps {
  children: React.ReactNode;
}

export function ChatLayoutClient({ children }: ChatLayoutClientProps) {
  const params = useParams();
  const conversationId = params?.id as string | undefined;
  const isArtifactPanelOpen = useChatStore((s) => s.isArtifactPanelOpen);
  const isSidebarOpen = useSidebarStore((s) => s.isOpen);
  const setSidebarOpen = useSidebarStore((s) => s.setOpen);

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
            className="absolute top-3 end-3 z-30 h-9 w-9 text-muted-foreground hover:text-foreground"
            onClick={() => setSidebarOpen(true)}
            aria-label="فتح الشريط الجانبي"
          >
            <PanelRightOpen className="h-5 w-5" />
          </Button>
        )}

        {/* Chat area */}
        <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
          {children}
        </div>

        {/* Artifact Panel — slides in from the left in RTL */}
        {isArtifactPanelOpen && conversationId && (
          <ArtifactPanel conversationId={conversationId} />
        )}
      </main>
    </div>
  );
}
