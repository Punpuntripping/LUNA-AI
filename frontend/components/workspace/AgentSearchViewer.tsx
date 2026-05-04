"use client";

import { ScrollArea } from "@/components/ui/scroll-area";
import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";
import type { WorkspaceItem } from "@/types";

interface AgentSearchViewerProps {
  item: WorkspaceItem;
}

/**
 * Read-only markdown render for ``agent_search`` items.
 *
 * deep_search produces immutable synthesis output -- if the user wants to
 * modify it, the writer pipeline produces a separate ``agent_writing`` row.
 */
export function AgentSearchViewer({ item }: AgentSearchViewerProps) {
  return (
    <ScrollArea className="flex-1">
      <div className="p-6" dir="rtl">
        <MarkdownRenderer content={item.content_md ?? ""} />
      </div>
    </ScrollArea>
  );
}
