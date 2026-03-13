"use client";

import { useParams } from "next/navigation";
import { Sidebar } from "@/components/sidebar/Sidebar";
import { ArtifactPanel } from "@/components/artifacts/ArtifactPanel";
import { useChatStore } from "@/stores/chat-store";

interface ChatLayoutClientProps {
  children: React.ReactNode;
}

export function ChatLayoutClient({ children }: ChatLayoutClientProps) {
  const params = useParams();
  const conversationId = params?.id as string | undefined;
  const isArtifactPanelOpen = useChatStore((s) => s.isArtifactPanelOpen);

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      {/* Sidebar — in RTL, this renders on the right side */}
      <Sidebar />

      {/* Main content area */}
      <main className="flex-1 flex min-w-0 overflow-hidden">
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
