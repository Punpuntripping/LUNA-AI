"use client";

import { useCallback, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  NotebookPen,
  Upload,
  Link2,
  BookOpen,
  Loader2,
  FileText,
  FolderOpen,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  useAttachFromDocument,
  useCreateNote,
  useCreateReference,
  useUploadAttachment,
  workspaceKeys,
} from "@/hooks/use-workspace";
import { fileKeys, useFilesEnabled } from "@/hooks/use-files";
import { useFileMatchQueue } from "@/hooks/use-file-match-queue";
import { FileMatchDialog } from "@/components/files/FileMatchDialog";
import { workspaceApi } from "@/lib/api";
import {
  fileMatchRequestFor,
  planMatchedUpload,
  type FileMatch,
  type FileMatchDecision,
  type MatchedUploadStatus,
} from "@/lib/contracts";
import { useDocuments } from "@/hooks/use-documents";
import { useChatStore } from "@/stores/chat-store";
import { DEMO_DISABLED_HINT } from "@/hooks/use-demo-conversation";
import { FILES_COPY } from "@/components/files/copy";
import { SoonTag } from "@/components/files/FileCard";
import type { Document } from "@/types";

/**
 * Which options the menu offers. ``all`` is the menu as it has always been.
 * With «الملفات» on, the pane's two tabs each get their half (my_files.md §3.1):
 * ``items`` → note / reference, ``files`` → upload / link from case documents.
 */
export type WorkspaceAddMenuSection = "all" | "items" | "files";

interface WorkspaceAddMenuProps {
  conversationId: string;
  caseId: string | null;
  /**
   * Shared demo conversation (plan §4.2): the button is **rendered but
   * disabled**, with a hover hint. Act 5 of the tour points at it and explains
   * it — hiding it would delete the anchor; enabling it would let one reader
   * write items into everybody's conversation (the POST is refused server-side
   * regardless; this makes the refusal visible instead of an error).
   */
  isDemo?: boolean;
  /** Which half of the menu to offer. Default ``all`` (unchanged menu). */
  section?: WorkspaceAddMenuSection;
  /**
   * Phase 3 «من الملفات» in the ``files`` half, shown inactive («قريبًا»).
   * Default hidden — attaching from the library is not live yet.
   */
  showLibraryPick?: boolean;
}

/**
 * Add-item button for the workspace pane footer.
 *
 * Renders a single ``+ إضافة عنصر`` button that opens a dropdown menu of
 * creation options (note / upload file / link from case docs / references
 * stub). On success the new item is opened in detail view.
 *
 * Replaces the deleted ConversationContextBar — chips are no longer shown
 * in the chat header; the workspace pane is the sole entry point for
 * managing items.
 */
export function WorkspaceAddMenu({
  conversationId,
  caseId,
  isDemo = false,
  section = "all",
  showLibraryPick = false,
}: WorkspaceAddMenuProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [linkPickerOpen, setLinkPickerOpen] = useState(false);

  const createNote = useCreateNote(conversationId);
  const createReference = useCreateReference(conversationId);
  const uploadAttachment = useUploadAttachment(conversationId);
  const openWorkspaceItem = useChatStore((s) => s.openWorkspaceItem);
  const qc = useQueryClient();

  // «الملفات» same-file approval (my_files.md §3.8) for «رفع ملف» in the pane —
  // inert unless the feature is on for this user. The pane's upload is one
  // request (no session to cancel), so a file answered «استخدام الموجود» while
  // it is still uploading is deleted the moment it lands; either way the pane
  // opens the EXISTING item instead of the new copy.
  const filesEnabled = useFilesEnabled();
  // Per pick: the upload's state, the existing item that replaced it, and —
  // while its question is open — whether the finished copy waits to be opened
  // (opening it would switch the pane to detail mode and close this popup).
  const paneUploadsRef = useRef<
    Map<
      string,
      {
        status: MatchedUploadStatus;
        itemId: string | null;
        replacedBy: string | null;
        asking: boolean;
      }
    >
  >(new Map());

  const refreshAfterCleanup = useCallback(() => {
    void qc.invalidateQueries({ queryKey: workspaceKeys.byConversation(conversationId) });
    // The library list only — never the once-per-session status query.
    void qc.invalidateQueries({ queryKey: fileKeys.list() });
  }, [qc, conversationId]);

  const applyMatchDecision = useCallback(
    (key: string, match: FileMatch, decision: FileMatchDecision) => {
      const record = paneUploadsRef.current.get(key);
      if (!record) return;
      record.asking = false;
      const plan = planMatchedUpload({
        match,
        answer: decision,
        upload: { status: record.status, itemId: record.itemId },
      });
      if (!plan.useExisting) {
        // The new copy stays — open it now if it finished while we asked.
        if (record.status === "completed" && record.itemId) {
          openWorkspaceItem(conversationId, record.itemId);
        }
        return;
      }
      record.replacedBy = match.item_id;
      if (plan.cleanup === "delete_new_item" && record.itemId) {
        workspaceApi.delete(record.itemId).catch(() => undefined).finally(refreshAfterCleanup);
      }
      // Still uploading: `onSuccess` below deletes the copy when it lands.
      openWorkspaceItem(conversationId, match.item_id);
    },
    [conversationId, openWorkspaceItem, refreshAfterCleanup],
  );

  const matchQueue = useFileMatchQueue({
    enabled: filesEnabled,
    isStillPending: (key) => paneUploadsRef.current.has(key),
    onDecision: applyMatchDecision,
    onAsk: (key) => {
      const record = paneUploadsRef.current.get(key);
      if (record) record.asking = true;
    },
  });

  const isPending =
    createNote.isPending ||
    createReference.isPending ||
    uploadAttachment.isPending;

  function handleCreateNote() {
    createNote.mutate(
      { title: "ملاحظة جديدة", content_md: "" },
      { onSuccess: (item) => openWorkspaceItem(conversationId, item.item_id) },
    );
  }

  function handleCreateReference() {
    createReference.mutate(
      { title: "مراجع", content_md: "" },
      { onSuccess: (item) => openWorkspaceItem(conversationId, item.item_id) },
    );
  }

  function handleUploadClick() {
    fileInputRef.current?.click();
  }

  const showItemOptions = section !== "files";
  const showFileOptions = section !== "items";
  const triggerLabel = section === "files" ? FILES_COPY.addFile : FILES_COPY.addItem;

  function handleFileSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!filesEnabled) {
      // «الملفات» off for this user: exactly as before.
      uploadAttachment.mutate(file, {
        onSuccess: (item) => openWorkspaceItem(conversationId, item.item_id),
      });
      e.target.value = "";
      return;
    }
    const key = `pane-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    paneUploadsRef.current.set(key, {
      status: "uploading",
      itemId: null,
      replacedBy: null,
      asking: false,
    });
    // `mutateAsync`, not `mutate(…, { onSuccess })`: opening the existing item
    // switches the pane to detail mode and unmounts this menu, and TanStack
    // drops per-call callbacks of an unmounted observer — the copy that lands
    // afterwards must still be deleted. The promise settles regardless.
    uploadAttachment
      .mutateAsync(file)
      .then((item) => {
        const record = paneUploadsRef.current.get(key);
        if (record?.replacedBy) {
          // Answered «استخدام الموجود» while this copy was uploading.
          paneUploadsRef.current.delete(key);
          workspaceApi.delete(item.item_id).catch(() => undefined).finally(refreshAfterCleanup);
          return;
        }
        if (record) {
          record.status = "completed";
          record.itemId = item.item_id;
          // Its question is open: the answer decides which item opens.
          if (record.asking) return;
        }
        openWorkspaceItem(conversationId, item.item_id);
      })
      .catch(() => {
        const record = paneUploadsRef.current.get(key);
        if (record) record.status = "failed";
      });
    // Beside the upload, never before it; a failed or slow answer is dropped.
    matchQueue.check(key, fileMatchRequestFor(file, conversationId));
    e.target.value = "";
  }

  return (
    <>
      {/* The hint lives on this wrapper, not on the button: browsers suppress
          pointer events on a disabled control, so its own `title` would never
          surface. `title` is undefined off-demo, so nothing changes there. */}
      <div
        dir="rtl"
        className="border-t bg-background p-2"
        title={isDemo ? DEMO_DISABLED_HINT : undefined}
      >
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="outline"
              size="sm"
              // Tour anchor `workspace-add` — RETIRED. The tour's last step now
              // points at the COMPOSER's «+» (`composer-add`) and talks about
              // attachments + extraction accuracy instead. Attribute kept: it is
              // still in TOUR_ANCHOR_IDS and still what a restored مساحة العمل
              // step would frame.
              data-tour="workspace-add"
              className="w-full justify-center gap-2 h-9 rounded-full"
              aria-label={triggerLabel}
              disabled={isPending || isDemo}
            >
              {isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Plus className="h-4 w-4" />
              )}
              <span>{triggerLabel}</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="center" side="top" sideOffset={6}>
            {showItemOptions && (
              <DropdownMenuItem onClick={handleCreateNote}>
                <NotebookPen className="me-2 h-4 w-4" />
                ملاحظة
              </DropdownMenuItem>
            )}
            {showFileOptions && (
              <DropdownMenuItem onClick={handleUploadClick}>
                <Upload className="me-2 h-4 w-4" />
                رفع ملف
              </DropdownMenuItem>
            )}
            {showFileOptions && section === "files" && showLibraryPick && (
              <DropdownMenuItem
                aria-disabled="true"
                title={FILES_COPY.soon}
                onSelect={(e) => e.preventDefault()}
                className="cursor-default text-muted-foreground opacity-60 focus:bg-transparent focus:text-muted-foreground"
              >
                <FolderOpen className="me-2 h-4 w-4" />
                {FILES_COPY.fromLibrary}
                <SoonTag className="ms-3" />
              </DropdownMenuItem>
            )}
            {showFileOptions && (
              <DropdownMenuItem
                onClick={() => setLinkPickerOpen(true)}
                disabled={!caseId}
              >
                <Link2 className="me-2 h-4 w-4" />
                ربط من مستندات القضية
              </DropdownMenuItem>
            )}
            {showItemOptions && (
              <DropdownMenuItem onClick={handleCreateReference}>
                <BookOpen className="me-2 h-4 w-4" />
                مراجع (قيد التطوير)
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>

        <input
          ref={fileInputRef}
          type="file"
          className="hidden"
          accept="application/pdf,image/png,image/jpeg"
          onChange={handleFileSelected}
        />
      </div>

      {filesEnabled && (
        <FileMatchDialog key={matchQueue.dialogKey} {...matchQueue.dialogProps} />
      )}

      {caseId && (
        <CaseDocumentPicker
          caseId={caseId}
          conversationId={conversationId}
          open={linkPickerOpen}
          onClose={() => setLinkPickerOpen(false)}
        />
      )}
    </>
  );
}

interface CaseDocumentPickerProps {
  caseId: string;
  conversationId: string;
  open: boolean;
  onClose: () => void;
}

function CaseDocumentPicker({
  caseId,
  conversationId,
  open,
  onClose,
}: CaseDocumentPickerProps) {
  const { data, isLoading } = useDocuments(open ? caseId : undefined);
  const attachFromDocument = useAttachFromDocument(conversationId);
  const openWorkspaceItem = useChatStore((s) => s.openWorkspaceItem);

  function handlePick(doc: Document) {
    attachFromDocument.mutate(
      { document_id: doc.document_id },
      {
        onSuccess: (item) => {
          openWorkspaceItem(conversationId, item.item_id);
          onClose();
        },
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent
        className="sm:max-w-[500px] max-h-[70dvh] flex flex-col"
        presentation="mobileSheet"
      >
        <DialogHeader>
          <DialogTitle dir="rtl">اختر مستنداً من القضية</DialogTitle>
        </DialogHeader>
        <ScrollArea className="flex-1 -mx-6 px-6">
          {isLoading ? (
            <div className="flex justify-center py-8">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : !data?.documents?.length ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              لا توجد مستندات في هذه القضية
            </p>
          ) : (
            <div className="space-y-1.5 py-2" dir="rtl">
              {data.documents.map((doc) => (
                <button
                  key={doc.document_id}
                  onClick={() => handlePick(doc)}
                  disabled={attachFromDocument.isPending}
                  className="flex w-full items-center gap-3 rounded-md border border-border/50 p-2.5 text-start text-sm hover:bg-accent disabled:opacity-60"
                >
                  <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                  <span className="flex-1 truncate">{doc.document_name}</span>
                </button>
              ))}
            </div>
          )}
        </ScrollArea>
      </DialogContent>
    </Dialog>
  );
}
