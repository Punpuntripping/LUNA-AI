"use client";

import { useState, useCallback, useRef, useEffect, type KeyboardEvent } from "react";
import TextareaAutosize from "react-textarea-autosize";
import { Send, Square, Paperclip } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/stores/chat-store";
import { FilePreview } from "@/components/chat/FilePreview";
import {
  runResumableUpload,
  type ImperativeUploadHandle,
} from "@/hooks/use-resumable-upload";
import type { PendingFile } from "@/types";

interface ChatInputProps {
  onSend: (content: string) => void;
  onStop?: () => void;
  disabled?: boolean;
  className?: string;
  /** When set, file uploads are enabled. */
  caseId?: string | null;
  /** Conversation the chat input belongs to; needed for attachment uploads. */
  conversationId?: string | null;
}

const MAX_CHARS = 10_000;
const MAX_FILES = 5;
const MAX_FILE_SIZE = 50 * 1024 * 1024; // 50MB
const ACCEPTED_TYPES = ["application/pdf", "image/png", "image/jpeg"];

export function ChatInput({
  onSend,
  onStop,
  disabled,
  className,
  conversationId,
}: ChatInputProps) {
  const [content, setContent] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // Keep live cancel handles keyed by pendingFile.id so removePendingFile
  // can abort the matching tus upload. The handle is dropped once the
  // upload terminates (completed/failed/cancelled).
  const uploadHandlesRef = useRef<Map<string, ImperativeUploadHandle>>(new Map());
  const qc = useQueryClient();

  const isStreaming = useChatStore((s) => s.isStreaming);
  const pendingFiles = useChatStore((s) => s.pendingFiles);
  const addPendingFile = useChatStore((s) => s.addPendingFile);
  const removePendingFile = useChatStore((s) => s.removePendingFile);
  const updatePendingFile = useChatStore((s) => s.updatePendingFile);

  // Block send while any attachment is still uploading. Failed / cancelled
  // files don't block — the user can either remove them or send anyway
  // (only `completed` files contribute attachment_ids in use-chat.ts).
  const hasInFlightUpload = pendingFiles.some(
    (f) => f.uploadStatus === "queued" || f.uploadStatus === "uploading",
  );

  const canSend =
    (content.trim().length > 0 || pendingFiles.length > 0) &&
    !isStreaming &&
    !disabled &&
    !hasInFlightUpload;

  // Abort any live tus uploads on unmount (e.g. user navigates away
  // mid-upload). Cancel handles also call the backend cancel endpoint
  // best-effort.
  useEffect(() => {
    const handles = uploadHandlesRef.current;
    return () => {
      handles.forEach((h) => h.cancel());
      handles.clear();
    };
  }, []);

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      setContent(e.target.value);
    },
    [],
  );

  const handleSend = useCallback(() => {
    const trimmed = content.trim();
    if (!trimmed && pendingFiles.length === 0) return;

    if (trimmed.length > MAX_CHARS) {
      setValidationError(`الحد الأقصى ${MAX_CHARS.toLocaleString("ar-SA")} حرف`);
      return;
    }

    setValidationError(null);
    onSend(trimmed);
    setContent("");
  }, [content, onSend, pendingFiles.length]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        if (canSend) handleSend();
      }
    },
    [canSend, handleSend],
  );

  /**
   * Wraps `removePendingFile` so the matching tus upload (if any) is
   * aborted alongside the store removal. The backend cancel endpoint
   * runs best-effort inside `cancel`.
   */
  const handleRemoveFile = useCallback(
    (id: string) => {
      const handle = uploadHandlesRef.current.get(id);
      if (handle) {
        handle.cancel();
        uploadHandlesRef.current.delete(id);
      }
      removePendingFile(id);
    },
    [removePendingFile],
  );

  const handleFileSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (!files) return;

      setValidationError(null);

      // TODO(upload-reliability Phase 2): attachments require an existing
      // conversation_id for the /init endpoint. In "general mode" (new
      // chat with no conversation yet) the conversation is created on
      // send, so we'd need to defer the upload kickoff until the create
      // returns. For now, block attachments without a conversation —
      // matches the existing behaviour (use-chat.ts only uploaded for
      // case-scoped sends or after a conversation existed). Re-enable
      // once the conversation-create-then-upload flow is in place.
      if (!conversationId) {
        setValidationError(
          "ابدأ محادثة أولاً قبل إضافة المرفقات",
        );
        e.target.value = "";
        return;
      }

      const currentCount = pendingFiles.length;
      const newFiles = Array.from(files);

      if (currentCount + newFiles.length > MAX_FILES) {
        setValidationError(`الحد الأقصى ${MAX_FILES} ملفات`);
        return;
      }

      for (const file of newFiles) {
        if (!ACCEPTED_TYPES.includes(file.type)) {
          setValidationError("الملفات المقبولة: PDF، PNG، JPG فقط");
          return;
        }

        if (file.size > MAX_FILE_SIZE) {
          setValidationError("الحد الأقصى لحجم الملف 50 ميجابايت");
          return;
        }

        const pendingId = `file-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
        const pendingFile: PendingFile = {
          id: pendingId,
          file,
          previewUrl: file.type.startsWith("image/")
            ? URL.createObjectURL(file)
            : "",
          name: file.name,
          size: file.size,
          mimeType: file.type,
          uploadStatus: "queued",
          uploadProgress: 0,
          itemId: null,
          errorMessage: null,
        };

        addPendingFile(pendingFile);

        // Kick off the resumable upload. Status flips through the
        // chat-store via updatePendingFile so the AttachmentUploadCard
        // re-renders with live progress.
        const handle = runResumableUpload(
          { kind: "attachment", conversationId },
          file,
          qc,
          {
            onInitialized: (itemId) => {
              updatePendingFile(pendingId, {
                itemId,
                uploadStatus: "uploading",
              });
            },
            onProgress: (s) => {
              // We can't tell `progress 0 → 0` from the very first call
              // apart, but the visible bar handles 0 fine. Only push
              // updates while we're actively transferring bytes.
              if (s.status === "uploading" || s.status === "finalizing") {
                updatePendingFile(pendingId, {
                  uploadStatus:
                    s.status === "finalizing" ? "uploading" : "uploading",
                  uploadProgress: s.progress,
                });
              }
            },
            onCompleted: (row) => {
              uploadHandlesRef.current.delete(pendingId);
              updatePendingFile(pendingId, {
                uploadStatus: "completed",
                uploadProgress: 1,
                // For attachment uploads `row` is a WorkspaceItem; pull
                // the canonical item_id off the row in case it differs
                // from the one /init returned (shouldn't, but defensive).
                itemId: "item_id" in row ? row.item_id : null,
              });
            },
            onFailed: (error) => {
              uploadHandlesRef.current.delete(pendingId);
              updatePendingFile(pendingId, {
                uploadStatus: "failed",
                errorMessage: error,
              });
            },
            onCancelled: () => {
              uploadHandlesRef.current.delete(pendingId);
              // No state update — handleRemoveFile already removed the
              // pendingFile from the store before invoking cancel.
            },
          },
        );
        uploadHandlesRef.current.set(pendingId, handle);
      }

      e.target.value = "";
    },
    [
      pendingFiles.length,
      addPendingFile,
      updatePendingFile,
      conversationId,
      qc,
    ],
  );

  const handleAddFile = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const handleStopClick = useCallback(() => {
    onStop?.();
  }, [onStop]);

  return (
    <div dir="rtl" lang="ar" className={cn("border-t bg-background px-4 py-3", className)}>
      {pendingFiles.length > 0 && (
        <FilePreview
          files={pendingFiles}
          onRemove={handleRemoveFile}
          className="mb-2"
        />
      )}

      {validationError && (
        <p className="text-xs text-destructive mb-2">{validationError}</p>
      )}

      <div className="relative flex items-end gap-2">
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".pdf,.png,.jpg,.jpeg"
          className="hidden"
          onChange={handleFileSelect}
        />

        <TextareaAutosize
          ref={textareaRef}
          dir="rtl"
          lang="ar"
          value={content}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder="اكتب رسالتك هنا..."
          minRows={1}
          maxRows={6}
          readOnly={isStreaming}
          disabled={disabled}
          className={cn(
            "flex-1 resize-none rounded-xl border bg-muted/50 px-4 py-2.5 text-sm",
            "placeholder:text-muted-foreground",
            "focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1",
            "disabled:cursor-not-allowed disabled:opacity-50",
            isStreaming && "cursor-not-allowed opacity-70",
          )}
        />

        <Button
          variant="ghost"
          size="icon"
          className="h-10 w-10 shrink-0"
          onClick={handleAddFile}
          disabled={isStreaming || !conversationId}
          aria-label="إضافة مرفق"
        >
          <Paperclip className="h-5 w-5" />
        </Button>

        {isStreaming ? (
          <Button
            variant="destructive"
            size="icon"
            className="h-10 w-10 shrink-0"
            onClick={handleStopClick}
            aria-label="إيقاف"
          >
            <Square className="h-4 w-4" />
          </Button>
        ) : (
          <Button
            size="icon"
            className="h-10 w-10 shrink-0"
            onClick={handleSend}
            disabled={!canSend}
            aria-label={hasInFlightUpload ? "جارٍ رفع المرفقات" : "إرسال"}
          >
            <Send className="h-4 w-4" />
          </Button>
        )}
      </div>
    </div>
  );
}
