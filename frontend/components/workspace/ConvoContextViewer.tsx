"use client";

import { ScrollArea } from "@/components/ui/scroll-area";
import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";
import type { WorkspaceItem } from "@/types";

interface ConvoContextViewerProps {
  item: WorkspaceItem;
}

/**
 * Read-only render of the running conversation summary.
 *
 * Wave 8D will wire generation cadence; for now this just displays whatever
 * the backend stored.
 */
export function ConvoContextViewer({ item }: ConvoContextViewerProps) {
  if (!item.content_md) {
    return (
      <div className="flex flex-1 items-center justify-center p-8 text-center">
        <p className="text-sm text-muted-foreground">
          لم يُولَّد ملخّص للمحادثة بعد
        </p>
      </div>
    );
  }

  return (
    <ScrollArea className="flex-1">
      <div className="p-6" dir="rtl">
        <MarkdownRenderer content={item.content_md} />
      </div>
    </ScrollArea>
  );
}
