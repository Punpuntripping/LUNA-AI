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
import { useRouter } from "next/navigation";
import { Send, Square, Sparkles, Plus } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/stores/chat-store";
import { useSidebarStore } from "@/stores/sidebar-store";
import {
  DEMO_COMPOSER_CTA,
  DEMO_COMPOSER_HINT,
  DEMO_DISABLED_HINT,
  useIsDemoConversation,
} from "@/hooks/use-demo-conversation";
import { FilePreview } from "@/components/chat/FilePreview";
import { BlogChips } from "@/components/chat/BlogChip";
import { LibraryItemChips } from "@/components/chat/LibraryItemChip";
import { TemplateChip } from "@/components/chat/TemplateChip";
import { ComposerPlusMenu } from "@/components/chat/ComposerPlusMenu";
// Chat-depth analytics (product_analytics §3b). Fire-and-forget, individually
// guarded inside the tracker — a tracking failure must never block a send (T9).
import {
  trackChatSend,
  trackConversationOpened,
} from "@/components/analytics/run-tracker";
import { api, workspaceApi, ApiClientError } from "@/lib/api";
import { workspaceKeys } from "@/hooks/use-workspace";
import { conversationKeys } from "@/hooks/use-conversations";
import {
  runResumableUpload,
  type ImperativeUploadHandle,
} from "@/hooks/use-resumable-upload";
import type {
  LibraryItemRef,
  PendingBlog,
  PendingFile,
  PendingLibraryItem,
  PendingTemplate,
} from "@/types";

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

/**
 * `viewportFit: "cover"` (app/layout.tsx) lets the page paint under the home
 * indicator, so the composer's bottom padding has to carry the inset itself or
 * the send button sits under the system bar. `env()` is 0 everywhere else, so
 * this is a no-op on desktop rather than a `max-md:` special case. The 0.75rem
 * is the wrapper's own `py-3`, written as a literal rem to match the
 * arbitrary-value convention the FAB and the anon CTA sheet already use.
 */
const COMPOSER_SAFE_AREA = "pb-[calc(0.75rem+env(safe-area-inset-bottom))]";

const MAX_CHARS = 10_000;
const MAX_FILES = 5;
const MAX_FILE_SIZE = 50 * 1024 * 1024; // 50MB
const ACCEPTED_TYPES = ["application/pdf", "image/png", "image/jpeg"];
// Blog paste-chips (blog_import plan §D4): max pasted blogs held in the
// composer at once. A blog token is 32 lowercase hex chars.
const MAX_BLOG_CHIPS = 3;
// Library carry-chips (simple_search_family §8): max library pages held in the
// composer at once. Same budget as blogs — each one costs a `references`
// workspace item and a slice of the turn's context, and a lookup question is
// about ONE object by definition (D5). Excess refs in the carry slot are
// dropped rather than queued.
const MAX_LIBRARY_CHIPS = 3;

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
  // INPUT MODALITY, not viewport (mobile_compatibility §1.3): on a coarse
  // pointer Enter is the keyboard's newline key and there is no Shift to pair
  // with it, so send-on-Enter makes a multi-line legal question impossible to
  // type. Deliberately NOT `useIsMobile` — a 768px iPad with a hardware
  // keyboard should keep the desktop behaviour. Read once per mount: the
  // pointer type of a session does not change under us, and re-reading per
  // keystroke would cost a layout query on the hot path. `typeof window`
  // guards the SSR pass; nothing it feeds is rendered, so no hydration risk.
  const [isCoarsePointer] = useState(
    () =>
      typeof window !== "undefined" &&
      window.matchMedia("(pointer: coarse)").matches,
  );
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // Keep live cancel handles keyed by pendingFile.id so removePendingFile
  // can abort the matching tus upload. The handle is dropped once the
  // upload terminates (completed/failed/cancelled).
  const uploadHandlesRef = useRef<Map<string, ImperativeUploadHandle>>(new Map());
  const qc = useQueryClient();
  const router = useRouter();
  // The ONE shared demo conversation is read-only for everyone (D2). Every
  // send path is refused server-side anyway; this replaces the composer with
  // the conversion affordance instead of letting the user type into a wall.
  const isDemo = useIsDemoConversation(conversationId);

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
  const pendingLibraryItems = useChatStore((s) => s.pendingLibraryItems);
  const addPendingLibraryItem = useChatStore((s) => s.addPendingLibraryItem);
  const removePendingLibraryItem = useChatStore(
    (s) => s.removePendingLibraryItem,
  );
  const updatePendingLibraryItem = useChatStore(
    (s) => s.updatePendingLibraryItem,
  );
  const clearPendingLibraryItems = useChatStore(
    (s) => s.clearPendingLibraryItems,
  );
  const pendingTemplate = useChatStore((s) => s.pendingTemplate);
  const setPendingTemplate = useChatStore((s) => s.setPendingTemplate);

  // Block send while any attachment is still uploading (or a pasted blog is
  // still importing). Failed / cancelled entries don't block — the user can
  // either remove them or send anyway (only `completed`/`ready` entries
  // contribute attachment_ids in use-chat.ts).
  const hasInFlightUpload =
    pendingFiles.some(
      (f) => f.uploadStatus === "queued" || f.uploadStatus === "uploading",
    ) ||
    pendingBlogs.some((b) => b.status === "loading") ||
    pendingLibraryItems.some((i) => i.status === "loading");

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

  // Ready library chips likewise — «تحدّث مع ريحان عن هذه الصفحة» has to be able
  // to send with the page alone and no typed text.
  const sendableLibraryCount = pendingLibraryItems.filter(
    (i) => i.status === "ready" && i.itemId,
  ).length;

  const canSend =
    (content.trim().length > 0 ||
      sendableFileCount > 0 ||
      sendableBlogCount > 0 ||
      sendableLibraryCount > 0 ||
      pendingTemplate !== null) &&
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
    clearPendingLibraryItems();
    setPendingTemplate(null);
  }, [
    conversationId,
    clearPendingFiles,
    clearPendingBlogs,
    clearPendingLibraryItems,
    setPendingTemplate,
  ]);

  // Analytics `conversation_opened` (product_analytics §3b). The composer is
  // the one component mounted for exactly one conversation at a time, which
  // makes it the cheapest honest hook for "this conversation was loaded".
  // It is the event that reframes abandonment: a user who closed the tab
  // during a five-minute deep_search and read the answer the next morning has
  // not churned. Chat is authed, so that join works across sessions without
  // any persistent anonymous identifier. Deduped inside the tracker, so a
  // remount cannot inflate the count.
  useEffect(() => {
    if (!conversationId) return;
    trackConversationOpened(conversationId);
  }, [conversationId]);

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
    // Template chip carried across the create-on-attach navigation (the
    // conversationId clear-effect above already ran and wiped the live slot;
    // restore it from the carry slot the source ChatInput stashed).
    const carriedTemplate = useChatStore.getState().pendingTemplateCarry;
    if (carriedTemplate) {
      useChatStore.getState().setPendingTemplateCarry(null);
      useChatStore.getState().setPendingTemplate(carriedTemplate);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Live composer injection (onboarding starter questions): fires while the
  // composer is already mounted — the mount-time pendingComposerDraft read
  // above can't cover that. Applies the text, focuses the box, clears the
  // slot (the clear re-runs the effect with null; the guard makes it a no-op).
  const composerInjection = useChatStore((s) => s.composerInjection);
  useEffect(() => {
    if (!composerInjection) return;
    setContent(composerInjection.text);
    useChatStore.getState().clearComposerInjection();
    textareaRef.current?.focus();
  }, [composerInjection]);

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      setContent(e.target.value);
    },
    [],
  );

  const handleSend = useCallback(() => {
    const trimmed = content.trim();
    const template = useChatStore.getState().pendingTemplate;
    if (
      !trimmed &&
      pendingFiles.length === 0 &&
      pendingBlogs.length === 0 &&
      pendingLibraryItems.length === 0 &&
      !template
    ) {
      return;
    }

    // Template chip → explicit directive line appended to the outgoing text.
    // The writer_planner picks قوالبي templates by matching the user's words
    // against its <my_templates> titles, so a verbatim «استخدم القالب: ...»
    // line is the strongest signal it can consume — no id travels over the
    // wire, and the directive stays visible in the message history.
    const outgoing = template
      ? [trimmed, `استخدم القالب: «${template.title.trim()}»`]
          .filter(Boolean)
          .join("\n\n")
      : trimmed;

    if (outgoing.length > MAX_CHARS) {
      setValidationError(`الحد الأقصى ${MAX_CHARS.toLocaleString("ar-SA")} حرف`);
      return;
    }

    setValidationError(null);

    // `chat_send` — t₀ for the whole run, and USER SUBMIT ONLY (T14). Fired
    // here, BEFORE the POST, so a send the quota gate refuses is still
    // measured; and never from use-chat's SSE (re)connect path, or every
    // reconnect would look like a fresh question and wait tolerance would be
    // measured against the wrong clock. Read from the store rather than the
    // render-scope counts so this stays out of the callback's dependencies.
    const store = useChatStore.getState();
    trackChatSend({
      conversationId: conversationId ?? null,
      hasAttachment:
        store.pendingFiles.some((f) => f.uploadStatus === "completed") ||
        store.pendingBlogs.some((b) => b.status === "ready" && !!b.itemId) ||
        store.pendingLibraryItems.some(
          (i) => i.status === "ready" && !!i.itemId,
        ),
    });

    onSend(outgoing);
    setContent("");
    if (template) setPendingTemplate(null);
  }, [
    content,
    conversationId,
    onSend,
    pendingFiles.length,
    pendingBlogs.length,
    pendingLibraryItems.length,
    setPendingTemplate,
  ]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      // Touch keyboards: Enter inserts a newline (default action, not
      // prevented) and the send button is the only way to send.
      if (isCoarsePointer) return;
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        if (canSend) handleSend();
      }
    },
    [canSend, handleSend, isCoarsePointer],
  );

  /**
   * iOS shrinks the VISUAL viewport when the keyboard opens but does not
   * reflow the layout viewport (`interactiveWidget: "resizes-content"` is not
   * honoured), so a focused composer can end up behind the keyboard. Scrolling
   * it into view after a frame — once the keyboard animation has told the
   * browser the new visual viewport — lifts it back above the keys.
   */
  const handleTextareaFocus = useCallback(() => {
    requestAnimationFrame(() => {
      textareaRef.current?.scrollIntoView({ block: "nearest" });
    });
  }, []);

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
            // The import may have retitled a fresh «محادثة جديدة» after the
            // blog — refresh the sidebar so the new title shows.
            void qc.invalidateQueries({ queryKey: conversationKeys.all });
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

  // Carry a library page into this conversation as a ``kind='references'``
  // workspace item (simple_search_family §8 / §12a C3) — the library twin of
  // ``importBlogTokens``: fire on arrival (pre-send), track per-chip status in
  // the store, and let the send path collect the itemIds into the EXISTING
  // ``attachment_ids`` array. ``createdByChip`` is set only when the server
  // says this call created the item, so removing a chip never deletes a card
  // that was already in the conversation.
  const importLibraryRefs = useCallback(
    (refs: LibraryItemRef[]) => {
      if (!conversationId || refs.length === 0) return;

      for (const ref of refs) {
        const chipId = `lib-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
        const chip: PendingLibraryItem = {
          id: chipId,
          pageType: ref.pageType,
          pageId: ref.pageId,
          title: ref.title,
          status: "loading",
          itemId: null,
          createdByChip: false,
          errorMessage: null,
        };
        addPendingLibraryItem(chip);

        api
          .createLibraryItem(conversationId, ref.pageType, ref.pageId)
          .then((res) => {
            updatePendingLibraryItem(chipId, {
              status: "ready",
              itemId: res.item.item_id,
              // Prefer the server's title — it is the canonical document name;
              // the page heading we carried was only a placeholder.
              title: (res.item.title ?? "").trim() || ref.title,
              createdByChip: res.already_attached === false,
            });
            void qc.invalidateQueries({
              queryKey: workspaceKeys.byConversation(conversationId),
            });
            // The carry may have retitled a fresh «محادثة جديدة» after the
            // page — refresh the sidebar so the new title shows.
            void qc.invalidateQueries({ queryKey: conversationKeys.all });
          })
          .catch((err) => {
            updatePendingLibraryItem(chipId, {
              status: "failed",
              errorMessage:
                err instanceof ApiClientError
                  ? err.message
                  : "تعذّر إضافة الصفحة إلى المحادثة",
            });
          });
      }
    },
    [conversationId, addPendingLibraryItem, updatePendingLibraryItem, qc],
  );

  // New-chat handoff for carried library pages: «تحدّث مع ريحان عن هذه الصفحة»
  // stashes the page in ``pendingLibraryRefs`` (the pendingBlogTokens twin) and
  // navigates — from the library page for a brand-new chat, from the
  // destination picker for an existing one, and from the AuthGuard intent after
  // an anonymous visitor signs in. All three land here. Declared AFTER the
  // conversationId-change clear effect so the fresh chips aren't wiped, and the
  // slot is cleared BEFORE the import so a re-run is a no-op.
  useEffect(() => {
    if (!conversationId) return;
    const carried = useChatStore.getState().pendingLibraryRefs;
    if (carried.length === 0) return;
    useChatStore.getState().clearPendingLibraryRefs();
    // Dedup by (pageType, pageId) against the live chips, then cap. Two carries
    // of the same page would otherwise send the same item_id twice.
    const liveItems = useChatStore.getState().pendingLibraryItems;
    const seen = new Set(liveItems.map((i) => `${i.pageType}:${i.pageId}`));
    const fresh = carried.filter((ref) => {
      const key = `${ref.pageType}:${ref.pageId}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    const room = MAX_LIBRARY_CHIPS - liveItems.length;
    if (room <= 0 || fresh.length === 0) return;
    importLibraryRefs(fresh.slice(0, room));
  }, [conversationId, importLibraryRefs]);

  // Queue blog tokens as composer chips — the single entry point shared by
  // the paste handler, the «مدونة → من مدوناتي» menu list, and the «إضافة
  // رابط» dialog. Dedups against live chips AND the new-chat carry slot,
  // enforces MAX_BLOG_CHIPS, and rides the create-on-attach flow when no
  // conversation exists yet (carrying the draft + any template chip across
  // the navigation). Returns true when at least one token was queued.
  const queueBlogTokens = useCallback(
    (tokens: string[], draft: string): boolean => {
      const store = useChatStore.getState();
      const already = new Set([
        ...store.pendingBlogs.map((b) => b.token),
        ...store.pendingBlogTokens,
      ]);
      const fresh = tokens.filter(
        (t, i) => !already.has(t) && tokens.indexOf(t) === i,
      );
      if (fresh.length === 0) return false;

      const room =
        MAX_BLOG_CHIPS -
        (store.pendingBlogs.length + store.pendingBlogTokens.length);
      if (room <= 0) {
        setValidationError(`الحد الأقصى ${MAX_BLOG_CHIPS} مدونات في الرسالة`);
        return false;
      }
      setValidationError(null);
      const capped = fresh.slice(0, room);

      // Brand-new chat: stash the tokens (+ draft + template chip) and ride
      // the same create-then-navigate flow the file picker uses.
      if (!conversationId) {
        if (!onRequireConversation) {
          setValidationError("ابدأ محادثة أولاً قبل إضافة المدونات");
          return false;
        }
        store.setPendingBlogTokens([...store.pendingBlogTokens, ...capped]);
        if (draft.trim()) store.setPendingComposerDraft(draft);
        if (store.pendingTemplate) {
          store.setPendingTemplateCarry(store.pendingTemplate);
        }
        onRequireConversation([]);
        return true;
      }

      importBlogTokens(capped);
      return true;
    },
    [conversationId, onRequireConversation, importBlogTokens],
  );

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

      // Insert the pasted text minus the blog URLs at the caret position.
      const remainder = text.replace(stripRe, " ").replace(/\s{2,}/g, " ").trim();
      const target = e.currentTarget;
      const start = target.selectionStart ?? content.length;
      const end = target.selectionEnd ?? content.length;
      const nextContent = remainder
        ? content.slice(0, start) + remainder + content.slice(end)
        : content;

      const queued = queueBlogTokens(tokens, nextContent);
      if (queued && remainder) setContent(nextContent);
    },
    [pendingBlogs, content, queueBlogTokens],
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

  // Remove a library chip. Deletes the underlying ``references`` item only when
  // THIS chip's carry created it (the server said ``already_attached: false``) —
  // never one the conversation already held, and never on an unknown flag.
  const handleRemoveLibraryItem = useCallback(
    (id: string) => {
      const item = useChatStore
        .getState()
        .pendingLibraryItems.find((i) => i.id === id);
      removePendingLibraryItem(id);
      if (item?.itemId && item.createdByChip && conversationId) {
        workspaceApi
          .delete(item.itemId)
          .then(() => {
            void qc.invalidateQueries({
              queryKey: workspaceKeys.byConversation(conversationId),
            });
          })
          .catch(() => {});
      }
    },
    [removePendingLibraryItem, conversationId, qc],
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
          const store = useChatStore.getState();
          if (content.trim()) {
            store.setPendingComposerDraft(content);
          }
          // A template chip attached before the conversation existed rides
          // the carry slot — the destination's clear effect wipes the live one.
          if (store.pendingTemplate) {
            store.setPendingTemplateCarry(store.pendingTemplate);
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

  // قالب pick from the «+» menu — single chip; picking another replaces it.
  const handlePickTemplate = useCallback(
    (template: PendingTemplate) => {
      setValidationError(null);
      setPendingTemplate(template);
    },
    [setPendingTemplate],
  );

  // مدونة pick (list item or validated link dialog) from the «+» menu.
  const handleAddBlogTokens = useCallback(
    (tokens: string[]) => {
      queueBlogTokens(tokens, content);
    },
    [queueBlogTokens, content],
  );

  const handleStopClick = useCallback(() => {
    onStop?.();
  }, [onStop]);

  // D7: the demo's only exit is a real conversation. Mirrors the sidebar /
  // /chats "new chat" flow exactly — no row is persisted here; `/chat` is the
  // empty composer and creates the conversation on the first send.
  const handleStartRealChat = useCallback(() => {
    useSidebarStore.getState().setSelectedConversation(null);
    router.push("/chat");
  }, [router]);

  // ==========================================================================
  // DEMO CONVERSATION — hint bar in place of the composer (plan §4.2 / D7).
  // Declared AFTER every hook above so the hook order is identical in both
  // branches; only the returned tree differs.
  // ==========================================================================
  if (isDemo) {
    return (
      <div
        dir="rtl"
        lang="ar"
        className={cn(
          "border-t bg-background px-4 py-3",
          COMPOSER_SAFE_AREA,
          className,
        )}
      >
        {/* Same max-w-3xl rail as the composer it replaces, so the thread
            column doesn't jump when the user opens the demo. */}
        <div className="mx-auto w-full max-w-3xl">
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-dashed border-border bg-muted/40 px-4 py-3">
            <div className="flex items-center gap-2">
              {/* The composer «+», rendered here DISABLED and nowhere else on
                  this screen.

                  The demo replaces the whole composer with this bar, so the
                  attachment «+» — the last step of «جولة المخرجات» and the one
                  control that explains OCR — had nothing to point at. A twin of
                  ComposerPlusMenu's trigger (same ghost/icon/h-10 shape, same
                  aria-label) keeps the tour's finger on a real «+» in the real
                  place, and reads as "greyed out here" rather than as a control
                  the demo forgot to wire.

                  `title` sits on the WRAPPER, not the button: browsers suppress
                  pointer events on a disabled control, so its own tooltip would
                  never surface — same trick as WorkspaceAddMenu. */}
              <span title={DEMO_DISABLED_HINT} className="inline-flex">
                <Button
                  variant="ghost"
                  size="icon"
                  data-tour="composer-add"
                  className="h-10 w-10 shrink-0"
                  aria-label="إضافة إلى الرسالة"
                  disabled
                >
                  <Plus className="h-5 w-5" />
                </Button>
              </span>
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <Sparkles className="h-4 w-4 shrink-0 text-primary" />
                {DEMO_COMPOSER_HINT}
              </p>
            </div>
            <Button
              size="sm"
              className="gap-1.5"
              onClick={handleStartRealChat}
              data-testid="demo-start-real-chat"
            >
              <Plus className="h-4 w-4" />
              {DEMO_COMPOSER_CTA}
            </Button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      dir="rtl"
      lang="ar"
      className={cn(
        "border-t bg-background px-4 py-3",
        COMPOSER_SAFE_AREA,
        className,
      )}
    >
      {/* Same column rail as MessageList so the composer aligns with the thread */}
      <div className="mx-auto w-full max-w-3xl">
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

      {pendingLibraryItems.length > 0 && (
        <LibraryItemChips
          items={pendingLibraryItems}
          onRemove={handleRemoveLibraryItem}
          className="mb-2"
        />
      )}

      {pendingTemplate && (
        <TemplateChip
          template={pendingTemplate}
          onRemove={() => setPendingTemplate(null)}
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
          onFocus={handleTextareaFocus}
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

        <ComposerPlusMenu
          disabled={isStreaming || disabled}
          canAttach={!!conversationId || !!onRequireConversation}
          onPickFiles={handleAddFile}
          onPickTemplate={handlePickTemplate}
          onAddBlogTokens={handleAddBlogTokens}
        />

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
    </div>
  );
}
