"use client";

import { useMemo } from "react";
import { Paperclip } from "lucide-react";
import { useChatStore } from "@/stores/chat-store";
import { trackWiOpened } from "@/components/analytics/run-tracker";
import {
  sortFilesNewestFirst,
  workspaceAttachmentCardModel,
} from "@/lib/contracts";
import type { WorkspaceItem } from "@/types";
import { FileCard } from "./FileCard";
import { FILES_COPY } from "./copy";

interface PaneFileListProps {
  conversationId: string;
  /** The conversation's `attachment` items (already filtered by kind). */
  items: WorkspaceItem[] | undefined;
  isLoading: boolean;
}

/**
 * The pane's «الملفات» tab (plan §3.1): the conversation's files as file
 * cards, newest first, each with its WI badge.
 *
 * Mirrors what `WorkspaceList` + `WorkspaceCard` do for the other kinds: the
 * card keeps the `workspace-item-{id}` DOM id, rings and scrolls into view
 * while a chat chip highlights it, and every user-initiated open goes through
 * `trackWiOpened` (product_analytics §3b) before the pane switches to it.
 */
export function PaneFileList({ conversationId, items, isLoading }: PaneFileListProps) {
  const openWorkspaceItem = useChatStore((s) => s.openWorkspaceItem);
  const highlightedItemId = useChatStore(
    (s) => s.workspaceByConversation[conversationId]?.highlightedItemId ?? null,
  );
  // The send is reading this conversation's unread files right now — the only
  // time a file without `ocr_status` is really «جارٍ استخراج النص…».
  const extracting = useChatStore(
    (s) => s.streams[conversationId]?.extractingDocuments ?? false,
  );

  const sorted = useMemo(() => sortFilesNewestFirst(items ?? []), [items]);

  if (isLoading) {
    return (
      <div className="space-y-2 p-3" aria-hidden="true">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="h-20 w-full animate-pulse rounded-lg bg-muted" />
        ))}
      </div>
    );
  }

  if (sorted.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 p-8 text-center">
        <Paperclip className="h-10 w-10 text-muted-foreground/50" aria-hidden="true" />
        <p className="text-sm text-muted-foreground">{FILES_COPY.paneFilesEmpty}</p>
      </div>
    );
  }

  return (
    <div className="space-y-1.5 p-2">
      {sorted.map((item) => (
        <FileCard
          key={item.item_id}
          domId={`workspace-item-${item.item_id}`}
          file={workspaceAttachmentCardModel(item, { extracting })}
          showWiBadge
          highlighted={highlightedItemId === item.item_id}
          onOpen={() => {
            trackWiOpened(item.item_id, item.kind, item.created_at);
            openWorkspaceItem(conversationId, item.item_id);
          }}
        />
      ))}
    </div>
  );
}
