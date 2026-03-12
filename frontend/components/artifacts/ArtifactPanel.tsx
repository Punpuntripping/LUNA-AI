"use client";

import { X, ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/stores/chat-store";
import { useConversationArtifacts } from "@/hooks/use-artifacts";
import { ArtifactList } from "./ArtifactList";
import { ArtifactViewer } from "./ArtifactViewer";

interface ArtifactPanelProps {
  conversationId: string;
}

export function ArtifactPanel({ conversationId }: ArtifactPanelProps) {
  const activeArtifactId = useChatStore((s) => s.activeArtifactId);
  const closeArtifactPanel = useChatStore((s) => s.closeArtifactPanel);
  const openArtifactPanel = useChatStore((s) => s.openArtifactPanel);

  const { data, isLoading } = useConversationArtifacts(conversationId);

  const isViewingArtifact = !!activeArtifactId;

  function handleBackToList() {
    // Clear activeArtifactId but keep panel open
    useChatStore.setState({ activeArtifactId: null });
  }

  return (
    <div
      className={cn(
        "flex h-full w-[400px] shrink-0 flex-col border-e bg-background",
        "animate-in slide-in-from-start duration-200"
      )}
    >
      {/* Panel header */}
      <div className="flex items-center gap-2 border-b px-3 py-2.5">
        {isViewingArtifact && (
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8 shrink-0"
            onClick={handleBackToList}
            aria-label="رجوع"
          >
            <ArrowRight className="h-4 w-4" />
          </Button>
        )}

        <h2 className="flex-1 text-sm font-semibold text-foreground">
          {isViewingArtifact ? "عرض المستند" : "المستندات"}
        </h2>

        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 shrink-0"
          onClick={closeArtifactPanel}
          aria-label="إغلاق"
        >
          <X className="h-4 w-4" />
        </Button>
      </div>

      <Separator />

      {/* Panel content */}
      <div className="flex-1 flex flex-col min-h-0">
        {isViewingArtifact ? (
          <ArtifactViewer artifactId={activeArtifactId} />
        ) : (
          <ScrollArea className="flex-1">
            <ArtifactList
              artifacts={data?.artifacts}
              isLoading={isLoading}
              onArtifactClick={(id) => openArtifactPanel(id)}
            />
          </ScrollArea>
        )}
      </div>
    </div>
  );
}
