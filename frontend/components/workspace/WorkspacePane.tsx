"use client";

import { X, Loader2, ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useChatStore } from "@/stores/chat-store";
import {
  useWorkspaceItem,
  useConversationWorkspace,
} from "@/hooks/use-workspace";
import { useConversationDetail } from "@/hooks/use-conversations";
import { AttachmentRenderer } from "./AttachmentRenderer";
import { NoteEditor } from "./NoteEditor";
import { AgentSearchViewer } from "./AgentSearchViewer";
import { ConvoContextViewer } from "./ConvoContextViewer";
import { ReferencesRenderer } from "./ReferencesRenderer";
import { WorkspaceList } from "./WorkspaceList";
import { WorkspaceAddMenu } from "./WorkspaceAddMenu";
import type { WorkspaceItem } from "@/types";

interface WorkspacePaneProps {
  conversationId: string;
}

/**
 * Two-mode workspace pane.
 *
 * - **List mode** (default, ``openItemId === null``): renders ``WorkspaceList``
 *   with one button per workspace item, grouped by kind. Clicking a button
 *   switches to detail mode.
 * - **Detail mode** (``openItemId`` set): renders the kind-specific viewer
 *   for the focused item. Header shows a back arrow that returns to list
 *   mode (``closeWorkspaceItem``); the X button still closes the entire
 *   pane (``closeWorkspace``).
 *
 * Renderer dispatch by ``item.kind``:
 * | kind            | renderer              |
 * | --------------- | --------------------- |
 * | attachment      | AttachmentRenderer    |
 * | note            | NoteEditor            |
 * | agent_writing   | NoteEditor            |
 * | agent_search    | AgentSearchViewer     |
 * | convo_context   | ConvoContextViewer    |
 * | references      | ReferencesRenderer    |
 */
export function WorkspacePane({ conversationId }: WorkspacePaneProps) {
  const openItemId = useChatStore((s) => s.workspace.openItemId);
  const closeWorkspace = useChatStore((s) => s.closeWorkspace);
  const closeWorkspaceItem = useChatStore((s) => s.closeWorkspaceItem);
  const openWorkspaceItem = useChatStore((s) => s.openWorkspaceItem);

  // Always loaded so the list mode renders without an extra fetch on close.
  const { data: listData, isLoading: listLoading } =
    useConversationWorkspace(conversationId);
  const { data: item, isLoading: itemLoading, error: itemError } =
    useWorkspaceItem(openItemId ?? undefined);
  // Needed by WorkspaceAddMenu to enable the "link from case docs" option.
  const { data: convData } = useConversationDetail(conversationId);
  const caseId = convData?.conversation?.case_id ?? null;

  const inDetailMode = openItemId !== null;

  return (
    <div className="flex h-full flex-col bg-background">
      <PaneHeader
        item={inDetailMode ? item ?? null : null}
        inDetailMode={inDetailMode}
        onBack={closeWorkspaceItem}
        onClose={closeWorkspace}
      />

      <div className="flex flex-1 flex-col min-h-0 overflow-y-auto">
        {!inDetailMode ? (
          <WorkspaceList
            items={listData?.items}
            isLoading={listLoading}
            onItemClick={openWorkspaceItem}
          />
        ) : itemLoading ? (
          <LoadingState />
        ) : itemError || !item ? (
          <ErrorState />
        ) : (
          <KindRouter item={item} />
        )}
      </div>

      {/* Add-item menu lives at the bottom of the pane in list mode only.
          Detail mode has its own kind-specific viewer footer (e.g. autosave
          status) and shouldn't show the create-new dropdown. */}
      {!inDetailMode && (
        <WorkspaceAddMenu conversationId={conversationId} caseId={caseId} />
      )}
    </div>
  );
}

function PaneHeader({
  item,
  inDetailMode,
  onBack,
  onClose,
}: {
  item: WorkspaceItem | null;
  inDetailMode: boolean;
  onBack: () => void;
  onClose: () => void;
}) {
  return (
    <div className="flex items-center gap-1 border-b px-2 py-2.5">
      {inDetailMode && (
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 shrink-0"
          onClick={onBack}
          aria-label="رجوع إلى قائمة العناصر"
        >
          {/* RTL: ArrowRight points "back" toward the list (start side) */}
          <ArrowRight className="h-4 w-4" />
        </Button>
      )}
      <h2 className="flex-1 truncate px-1 text-sm font-semibold text-foreground">
        {inDetailMode ? item?.title ?? "..." : "العناصر"}
      </h2>
      <Button
        variant="ghost"
        size="icon"
        className="h-8 w-8 shrink-0"
        onClick={onClose}
        aria-label="إغلاق لوحة العناصر"
      >
        <X className="h-4 w-4" />
      </Button>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="flex flex-1 items-center justify-center p-8">
      <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
    </div>
  );
}

function ErrorState() {
  return (
    <div className="flex flex-1 items-center justify-center p-8">
      <p className="text-sm text-destructive">حدث خطأ في تحميل العنصر</p>
    </div>
  );
}

function KindRouter({ item }: { item: WorkspaceItem }) {
  switch (item.kind) {
    case "attachment":
      return <AttachmentRenderer item={item} />;
    case "note":
    case "agent_writing":
      return <NoteEditor item={item} />;
    case "agent_search":
      return <AgentSearchViewer item={item} />;
    case "convo_context":
      return <ConvoContextViewer item={item} />;
    case "references":
      return <ReferencesRenderer item={item} />;
    default:
      return (
        <div className="flex flex-1 items-center justify-center p-8 text-center">
          <p className="text-sm text-muted-foreground">
            نوع غير معروف: {(item as { kind: string }).kind}
          </p>
        </div>
      );
  }
}
