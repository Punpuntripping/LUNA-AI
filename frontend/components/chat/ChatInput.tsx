"use client";

import {
  useState,
  useCallback,
  useRef,
  useEffect,
  type ClipboardEvent,
  type KeyboardEvent,
} from "react";
import TextareaAutosize from "react-textarea-autosize";
import { Send, Square, Paperclip } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/stores/chat-store";
import { FilePreview } from "@/components/chat/FilePreview";
import { BlogChips } from "@/components/chat/BlogChip";
import { api, workspaceApi, ApiClientError } from "@/lib/api";
import { workspaceKeys } from "@/hooks/use-workspace";
import {
  runResumableUpload,
  type ImperativeUploadHandle,
} from "@/hooks/use-resumable-upload";
import type { PendingBlog, PendingFile } from "@/types";

interface ChatInputProps {
  onSend: (content: string) => void;
  onStop?: () => void;
  disabled?: boolean;
  className?: string;
  /** When set, file uploads are enabled. */
  caseId?: string | null;
  /** Conversation the chat input belongs to; needed for attachment uploads. */
  conversationId?: string | null;
  /**
   * New-chat mode: called when files are picked but no conversation exists yet.
   * The handler creates + stores the conversation, then navigates to it; the
   * picked files are stashed in the chat-store and the destination ChatInput
   * resumes their uploads. When provided, the attach button is enabled even
   * without a ``conversationId``.
   */
  onRequireConversation?: (files: File[]) => void;
}

const MAX_CHARS = 10_000;
const MAX_FILES = 5;
const MAX_FILE_SIZE = 50 * 1024 * 1024; // 50MB
const ACCEPTED_TYPES = ["application/pdf", "image/png", "image/jpeg"];
// Blog paste-chips (blog_import plan §D4): max pasted blogs held in the
// composer at once. A blog token is 32 lowercase hex chars.
const MAX_BLOG_CHIPS = 3;

export function ChatInput({
  onSend,
  onStop,
  disabled,
  className,
  conversationId,
  onRequireConversation,
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
  const clearPendingFiles = useChatStore((s) => s.clearPendingFiles);
  const pendingBlogs = useChatStore((s) => s.pendingBlogs);
  const addPendingBlog = useChatStore((s) => s.addPendingBlog);
  const removePendingBlog = useChatStore((s) => s.removePendingBlog);
  const updatePendingBlog = useChatStore((s) => s.updatePendingBlog);
  const clearPendingBlogs = useChatStore((s) => s.clearPendingBlogs);

  // Block send while any attachment is still uploading (or a pasted blog is
  // still importing). Failed / cancelled entries don't block — the user can
  // either remove them or send anyway (only `completed`/`ready` entries
  // contribute attachment_ids in use-chat.ts).
  const hasInFlightUpload =
    pendingFiles.some(
      (f) => f.uploadStatus === "queued" || f.uploadStatus === "uploading",
    ) || pendingBlogs.some((b) => b.status === "loading");

  // Only count files the user can actually send. Failed/cancelled files in
  // the queue would otherwise let the send button activate with an empty
  // textarea, then fire a send with zero attachment_ids and no text — which
  // either no-ops or sends a blank message. Match the user's expectation:
  // the queue counts only if at least one file is `completed`.
  const sendableFileCount = pendingFiles.filter(
    (f) => f.uploadStatus === "completed",
  ).length;

  // Ready blog chips count like completed files for send purposes.
  const sendableBlogCount = pendingBlogs.filter(
    (b) => b.status === "ready" && b.itemId,
  ).length;

  const canSend =
    (content.trim().length > 0 || sendableFileCount > 0 || sendableBlogCount > 0) &&
    !isStreaming &&
    !disabled &&
    !hasInFlightUpload;

  // When the user navigates from one conversation to another, the chat-store
  // is a global singleton so its `pendingFiles` array would otherwise carry
  // the previous conversation's attachments into the new one — visually
  // "pinned" and incorrectly attributed. Abort any in-flight uploads tied
  // to the prior conversation, drop the cancel handles, and clear the queue
  // (blog chips too — their notes belong to the prior conversation).
  // Keyed on `conversationId` so the effect re-runs only on navigation.
  useEffect(() => {
    const handles = uploadHandlesRef.current;
    handles.forEach((h) => h.cancel());
    handles.clear();
    clearPendingFiles();
    clearPendingBlogs();
  }, [conversationId, clearPendingFiles, clearPendingBlogs]);

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

  // New-chat handoff: prefill the composer with any draft text carried from the
  // empty page when the user attached a file before sending. Runs once on mount;
  // a no-op on every normal mount (the slot is null).
  useEffect(() => {
    const draft = useChatStore.getState().pendingComposerDraft;
    if (draft) {
      setContent(draft);
      useChatStore.getState().setPendingComposerDraft(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      setContent(e.target.value);
    },
    [],
  );

  const handleSend = useCallback(() => {
    const trimmed = content.trim();
    if (!trimmed && pendingFiles.length === 0 && pendingBlogs.length === 0) return;

    if (trimmed.length > MAX_CHARS) {
      setValidationError(`الحد الأقصى ${MAX_CHARS.toLocaleString("ar-SA")} حرف`);
      return;
    }

    setValidationError(null);
    onSend(trimmed);
    setContent("");
  }, [content, onSend, pendingFiles.length, pendingBlogs.length]);

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

  // Kick off resumable uploads for a list of already-validated files. Requires
  // an existing conversation. Shared by the file picker (when a conversation is
  // present) and the post-create consume effect (files carried over from a brand
  // new chat). Status flips through the chat-store via updatePendingFile so the
  // AttachmentUploadCard re-renders with live progress.
  const startUploads = useCallback(
    (files: File[]) => {
      if (!conversationId || files.length === 0) return;

      for (const file of files) {
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

        const handle = runResumableUpload(
          { kind: "attachment", conversationId },
          file,
          qc,
          {
            onInitialized: (itemId) => {
              updatePendingFile(pendingId, { itemId, uploadStatus: "uploading" });
            },
            onProgress: (s) => {
              if (s.status === "uploading" || s.status === "finalizing") {
                updatePendingFile(pendingId, {
                  uploadStatus: "uploading",
                  uploadProgress: s.progress,
                });
              }
            },
            onCompleted: (row) => {
              uploadHandlesRef.current.delete(pendingId);
              updatePendingFile(pendingId, {
                uploadStatus: "completed",
                uploadProgress: 1,
                // For attachment uploads `row` is a WorkspaceItem; pull the
                // canonical item_id off the row in case it differs from the
                // one /init returned (shouldn't, but defensive).
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
    },
    [conversationId, addPendingFile, updatePendingFile, qc],
  );

  // New-chat handoff: when files were picked before a conversation existed, the
  // create-conversation flow stashed them in the store and navigated here. Now
  // that a conversation id is present, resume their uploads. Declared AFTER the
  // conversationId-change clear effect above so the carried files aren't wiped
  // by it on mount; clears the slot first so a re-run is a no-op.
  useEffect(() => {
    if (!conversationId) return;
    const carried = useChatStore.getState().pendingAttachFiles;
    if (carried.length === 0) return;
    useChatStore.getState().clearPendingAttachFiles();
    startUploads(carried);
  }, [conversationId, startUploads]);

  // Import pasted blog tokens as kind='agent_search' workspace items with a
  // real المراجع panel (blog_import plan §D4) — the blog twin of startUploads:
  // fire at paste time (pre-send), track per-chip status in the store, and let
  // the send path collect the itemIds. ``createdByChip`` records whether THIS
  // import created the item (vs. server dedup returning an existing one) so
  // chip removal never deletes an item that existed before the paste.
  const importBlogTokens = useCallback(
    (tokens: string[]) => {
      if (!conversationId || tokens.length === 0) return;

      for (const token of tokens) {
        const chipId = `blog-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
        const chip: PendingBlog = {
          id: chipId,
          token,
          title: null,
          status: "loading",
          itemId: null,
          createdByChip: false,
          errorMessage: null,
        };
        addPendingBlog(chip);

        api
          .createBlogItem(conversationId, token)
          .then((res) => {
            updatePendingBlog(chipId, {
              status: "ready",
              itemId: res.item.item_id,
              title: res.item.title,
              createdByChip: !res.already_attached,
            });
            void qc.invalidateQueries({
              queryKey: workspaceKeys.byConversation(conversationId),
            });
          })
          .catch((err) => {
            updatePendingBlog(chipId, {
              status: "failed",
              errorMessage:
                err instanceof ApiClientError
                  ? err.message
                  : "تعذّر استيراد المدونة",
            });
          });
      }
    },
    [conversationId, addPendingBlog, updatePendingBlog, qc],
  );

  // New-chat handoff for pasted blogs: tokens pasted before a conversation
  // existed were stashed in ``pendingBlogTokens`` (the pendingAttachFiles
  // twin). Now that a conversation id is present, import them. Declared AFTER
  // the conversationId-change clear effect so the fresh chips aren't wiped.
  useEffect(() => {
    if (!conversationId) return;
    const carried = useChatStore.getState().pendingBlogTokens;
    if (carried.length === 0) return;
    useChatStore.getState().clearPendingBlogTokens();
    importBlogTokens(carried);
  }, [conversationId, importBlogTokens]);

  // Detect blog share-links in pasted text: each unique ``/blog/<32-hex>``
  // URL becomes a chip (the URL itself is stripped from the inserted text —
  // "like an attachment"). Ordinary pastes fall through untouched. In a
  // brand-new chat the tokens ride the create-on-attach flow via the store
  // slot; the destination ChatInput's consume effect imports them.
  const handlePaste = useCallback(
    (e: ClipboardEvent<HTMLTextAreaElement>) => {
      const text = e.clipboardData?.getData("text") ?? "";
      if (!text || !text.toLowerCase().includes("/blog/")) return;

      // Fresh regexes per call — module-level /g regexes carry lastIndex.
      const tokenRe = /\/blog\/([0-9a-f]{32})(?![0-9a-f])/gi;
      const stripRe = /(?:https?:\/\/[^\s]*)?\/blog\/[0-9a-f]{32}[^\s]*/gi;

      const already = new Set(pendingBlogs.map((b) => b.token));
      const tokens: string[] = [];
      for (const m of text.matchAll(tokenRe)) {
        const token = m[1].toLowerCase();
        if (!already.has(token) && !tokens.includes(token)) tokens.push(token);
      }
      if (tokens.length === 0) return; // no NEW blog links — ordinary paste

      e.preventDefault();
      setValidationError(null);

      const room = MAX_BLOG_CHIPS - pendingBlogs.length;
      if (room <= 0) {
        setValidationError(`الحد الأقصى ${MAX_BLOG_CHIPS} مدونات في الرسالة`);
        return;
      }
      const capped = tokens.slice(0, room);

      // Insert the pasted text minus the blog URLs at the caret position.
      const remainder = text.replace(stripRe, " ").replace(/\s{2,}/g, " ").trim();
      const target = e.currentTarget;
      const start = target.selectionStart ?? content.length;
      const end = target.selectionEnd ?? content.length;
      const nextContent = remainder
        ? content.slice(0, start) + remainder + content.slice(end)
        : content;
      if (remainder) setContent(nextContent);

      // Brand-new chat: stash the tokens (+ draft) and ride the same
      // create-then-navigate flow the file picker uses (with no files).
      if (!conversationId) {
        if (onRequireConversation) {
          const store = useChatStore.getState();
          store.setPendingBlogTokens([...store.pendingBlogTokens, ...capped]);
          if (nextContent.trim()) store.setPendingComposerDraft(nextContent);
          onRequireConversation([]);
        } else {
          setValidationError("ابدأ محادثة أولاً قبل إضافة المدونات");
        }
        return;
      }

      importBlogTokens(capped);
    },
    [pendingBlogs, content, conversationId, onRequireConversation, importBlogTokens],
  );

  // Remove a blog chip. If this chip's import CREATED the item (not a dedup
  // reuse), delete it too — an accidental paste shouldn't leave a stray
  // workspace item behind. Best-effort: a failed delete leaves the item in
  // the pane where the user can remove it manually.
  const handleRemoveBlog = useCallback(
    (id: string) => {
      const blog = useChatStore.getState().pendingBlogs.find((b) => b.id === id);
      removePendingBlog(id);
      if (blog?.itemId && blog.createdByChip && conversationId) {
        workspaceApi
          .delete(blog.itemId)
          .then(() => {
            void qc.invalidateQueries({
              queryKey: workspaceKeys.byConversation(conversationId),
            });
          })
          .catch(() => {});
      }
    },
    [removePendingBlog, conversationId, qc],
  );

  const handleFileSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const picked = Array.from(e.target.files ?? []);
      e.target.value = "";
      if (picked.length === 0) return;

      setValidationError(null);

      // Validate count + each file's type/size up front so the new-chat path
      // never creates a conversation for an invalid selection.
      if (pendingFiles.length + picked.length > MAX_FILES) {
        setValidationError(`الحد الأقصى ${MAX_FILES} ملفات`);
        return;
      }
      for (const file of picked) {
        if (!ACCEPTED_TYPES.includes(file.type)) {
          setValidationError("الملفات المقبولة: PDF، PNG، JPG فقط");
          return;
        }
        if (file.size > MAX_FILE_SIZE) {
          setValidationError("الحد الأقصى لحجم الملف 50 ميجابايت");
          return;
        }
      }

      // Brand-new chat with no conversation yet: hand the validated files to the
      // create-then-upload flow (creates + stores the conversation, navigates,
      // and the destination ChatInput resumes the uploads via the effect above).
      // Carry the typed draft too so it isn't lost across the navigation.
      if (!conversationId) {
        if (onRequireConversation) {
          if (content.trim()) {
            useChatStore.getState().setPendingComposerDraft(content);
          }
          onRequireConversation(picked);
        } else {
          setValidationError("ابدأ محادثة أولاً قبل إضافة المرفقات");
        }
        return;
      }

      startUploads(picked);
    },
    [pendingFiles.length, conversationId, onRequireConversation, content, startUploads],
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

      {pendingBlogs.length > 0 && (
        <BlogChips
          blogs={pendingBlogs}
          onRemove={handleRemoveBlog}
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
          onPaste={handlePaste}
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
          disabled={isStreaming || (!conversationId && !onRequireConversation)}
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
