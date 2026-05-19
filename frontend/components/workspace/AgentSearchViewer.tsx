"use client";

import { ScrollArea } from "@/components/ui/scroll-area";
import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";
import { ReferencePanel } from "./ReferencePanel";
import type { AgentSearchMetadata, WorkspaceItem } from "@/types";

interface AgentSearchViewerProps {
  item: WorkspaceItem;
}

/**
 * Read-only render for ``agent_search`` items.
 *
 * The synthesis body (``content_md``) renders as markdown; the reference list
 * renders below it as a JSON-driven ``ReferencePanel`` fed from
 * ``metadata.references`` — references live entirely on the artifact, never
 * in the chat.
 *
 * deep_search produces immutable synthesis output -- if the user wants to
 * modify it, the writer pipeline produces a separate ``agent_writing`` row.
 */
export function AgentSearchViewer({ item }: AgentSearchViewerProps) {
  const metadata = (item.metadata ?? {}) as AgentSearchMetadata;
  const references = metadata.references ?? [];

  return (
    <ScrollArea className="flex-1">
      <div className="p-6" dir="rtl">
        <MarkdownRenderer content={item.content_md ?? ""} />
        <ReferencePanel references={references} />
      </div>
    </ScrollArea>
  );
}
