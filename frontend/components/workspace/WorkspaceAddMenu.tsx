"use client";

import { useRef, useState } from "react";
import {
  Plus,
  NotebookPen,
  Upload,
  Link2,
  BookOpen,
  Loader2,
  FileText,
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
} from "@/hooks/use-workspace";
import { useDocuments } from "@/hooks/use-documents";
import { useChatStore } from "@/stores/chat-store";
import type { Document } from "@/types";

interface WorkspaceAddMenuProps {
  conversationId: string;
  caseId: string | null;
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
}: WorkspaceAddMenuProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [linkPickerOpen, setLinkPickerOpen] = useState(false);

  const createNote = useCreateNote(conversationId);
  const createReference = useCreateReference(conversationId);
  const uploadAttachment = useUploadAttachment(conversationId);
  const openWorkspaceItem = useChatStore((s) => s.openWorkspaceItem);

  const isPending =
    createNote.isPending ||
    createReference.isPending ||
    uploadAttachment.isPending;

  function handleCreateNote() {
    createNote.mutate(
      { title: "ملاحظة جديدة", content_md: "" },
      { onSuccess: (item) => openWorkspaceItem(item.item_id) },
    );
  }

  function handleCreateReference() {
    createReference.mutate(
      { title: "مراجع", content_md: "" },
      { onSuccess: (item) => openWorkspaceItem(item.item_id) },
    );
  }

  function handleUploadClick() {
    fileInputRef.current?.click();
  }

  function handleFileSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    uploadAttachment.mutate(file, {
      onSuccess: (item) => openWorkspaceItem(item.item_id),
    });
    e.target.value = "";
  }

  return (
    <>
      <div dir="rtl" className="border-t bg-background p-2">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="outline"
              size="sm"
              className="w-full justify-center gap-2 h-9 rounded-full"
              aria-label="إضافة عنصر"
              disabled={isPending}
            >
              {isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Plus className="h-4 w-4" />
              )}
              <span>إضافة عنصر</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="center" side="top" sideOffset={6}>
            <DropdownMenuItem onClick={handleCreateNote}>
              <NotebookPen className="me-2 h-4 w-4" />
              ملاحظة
            </DropdownMenuItem>
            <DropdownMenuItem onClick={handleUploadClick}>
              <Upload className="me-2 h-4 w-4" />
              رفع ملف
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={() => setLinkPickerOpen(true)}
              disabled={!caseId}
            >
              <Link2 className="me-2 h-4 w-4" />
              ربط من مستندات القضية
            </DropdownMenuItem>
            <DropdownMenuItem onClick={handleCreateReference}>
              <BookOpen className="me-2 h-4 w-4" />
              مراجع (قيد التطوير)
            </DropdownMenuItem>
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
          openWorkspaceItem(item.item_id);
          onClose();
        },
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-[500px] max-h-[70vh] flex flex-col">
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
