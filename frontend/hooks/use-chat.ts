import { useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { messagesApi, documentsApi } from "@/lib/api";
import { useChatStore } from "@/stores/chat-store";
import { messageKeys } from "@/hooks/use-messages";
import { conversationKeys } from "@/hooks/use-conversations";
import { workspaceKeys } from "@/hooks/use-workspace";
import type {
  Message,
  MessageListResponse,
  SSEMessageStart,
  SSEToken,
  SSECitations,
  SSEDone,
  SSEAgentSelected,
  SSEAgentRunStarted,
  SSEAgentRunFinished,
  SSEWorkspaceItemCreated,
  SSEWorkspaceItemUpdated,
  SSEWorkspaceItemLocked,
  SSEWorkspaceItemUnlocked,
  WorkspaceItem,
  WorkspaceItemListResponse,
} from "@/types";

interface SendMessageParams {
  conversationId: string;
  content: string;
  caseId?: string | null;
}

interface RegenerateParams {
  conversationId: string;
  messageId: string;
  caseId?: string | null;
}

interface EditAndResendParams {
  conversationId: string;
  messageId: string;
  newContent: string;
  caseId?: string | null;
}

interface RetryParams {
  conversationId: string;
  messageId: string;
  caseId?: string | null;
}

interface UseSendMessageReturn {
  sendMessage: (params: SendMessageParams) => Promise<void>;
  stopStreaming: () => void;
  /** Re-sends the user message that preceded the given assistant message */
  regenerateMessage: (params: RegenerateParams) => Promise<void>;
  /** Sends edited content as a new message in the conversation */
  editAndResend: (params: EditAndResendParams) => Promise<void>;
  /** Re-sends a failed message using its original content */
  retryMessage: (params: RetryParams) => Promise<void>;
}

/**
 * Core SSE streaming hook.
 * Handles optimistic updates, SSE stream parsing, and cache invalidation.
 */
export function useSendMessage(): UseSendMessageReturn {
  const qc = useQueryClient();
  const {
    startStreaming,
    appendToken,
    stopStreaming: storeStopStreaming,
    setError,
    setAbortController,
  } = useChatStore.getState();

  const sendMessage = useCallback(
    async ({ conversationId, content, caseId }: SendMessageParams) => {
      // 0. Upload pending files (if any and conversation has a case)
      const { pendingFiles, clearPendingFiles } = useChatStore.getState();
      const attachmentIds: string[] = [];

      // Build optimistic attachment list from pending files (for UI display)
      const optimisticAttachments = pendingFiles.map((pf) => ({
        id: pf.id,
        document_id: pf.id,
        attachment_type: (pf.mimeType === "application/pdf" ? "pdf" : pf.mimeType.startsWith("image/") ? "image" : "file") as "pdf" | "image" | "file",
        filename: pf.name,
        file_size: pf.size,
      }));

      if (pendingFiles.length > 0 && caseId) {
        for (const pf of pendingFiles) {
          try {
            const doc = await documentsApi.upload(caseId, pf.file);
            if (doc?.document_id) attachmentIds.push(doc.document_id);
          } catch (err) {
            console.error("File upload failed:", pf.name, err);
          }
        }
        clearPendingFiles();
      } else if (pendingFiles.length > 0) {
        // General conversation — no case to upload to, clear files
        clearPendingFiles();
      }

      // If no text but files are pending, use a default (backend requires min_length=1)
      const messageContent = content || (optimisticAttachments.length > 0 ? "مرفق" : "");
      if (!messageContent) return;

      // 1. Create optimistic user message
      const optimisticId = `optimistic-${Date.now()}`;
      const optimisticMessage: Message = {
        message_id: optimisticId,
        conversation_id: conversationId,
        role: "user",
        content: messageContent,
        attachments: optimisticAttachments,
        created_at: new Date().toISOString(),
        isOptimistic: true,
      };

      // Cancel any in-flight queries so they don't overwrite our optimistic update
      await qc.cancelQueries({ queryKey: messageKeys.list(conversationId) });

      // Add optimistic message to the query cache
      qc.setQueryData<{ pages: MessageListResponse[]; pageParams: (string | undefined)[] }>(
        messageKeys.list(conversationId),
        (old) => {
          if (!old) {
            return {
              pages: [{ messages: [optimisticMessage], has_more: false }],
              pageParams: [undefined],
            };
          }
          const newPages = [...old.pages];
          // Prepend to the first page (newest messages)
          newPages[0] = {
            ...newPages[0],
            messages: [optimisticMessage, ...newPages[0].messages],
          };
          return { ...old, pages: newPages };
        }
      );

      let assistantMessageId: string | null = null;

      // 3. Capture task type before the first attempt (cleared after first send)
      const { selectedAgentFamily } = useChatStore.getState();
      const sendOptions = {
        agent_family: selectedAgentFamily ?? undefined,
        attachment_ids: attachmentIds.length ? attachmentIds : undefined,
      };

      // Retry loop: attempt SSE connection up to (1 + maxReconnectAttempts) times.
      // The user message is already saved in the DB before this loop, so on retry we
      // only re-establish the SSE stream — we never re-send the user message.
      let attemptSucceeded = false;

      while (!attemptSucceeded) {
        // Create a fresh AbortController for each attempt so a previous abort signal
        // (from stopStreaming) does not immediately cancel a retry attempt.
        const attemptController = new AbortController();
        setAbortController(attemptController);

        try {
          const response = await messagesApi.send(
            conversationId,
            messageContent,
            attemptController.signal,
            sendOptions,
          );

          // Clear agent selection after the first successful HTTP response
          useChatStore.getState().resetAgentSelection();

          if (!response.ok) {
            // HTTP error — only 5xx errors are retryable
            const isServerError = response.status >= 500;
            if (isServerError) {
              // Let the outer catch handle retry logic via a thrown error
              const err = Object.assign(new Error("Server error"), { status: response.status });
              throw err;
            }
            // 4xx and other non-retryable HTTP errors: fail immediately
            let errorDetail = "حدث خطأ أثناء إرسال الرسالة";
            try {
              const errorBody = await response.json();
              if (typeof errorBody.detail === "string") {
                errorDetail = errorBody.detail;
              }
            } catch {
              // Use default Arabic error
            }
            markOptimisticFailed(qc, conversationId, optimisticId);
            setError(errorDetail);
            useChatStore.getState().resetReconnect();
            return;
          }

          if (!response.body) {
            markOptimisticFailed(qc, conversationId, optimisticId);
            setError("لم يتم استلام استجابة من الخادم");
            useChatStore.getState().resetReconnect();
            return;
          }

          // 4. Parse SSE stream
          const reader = response.body.getReader();
          const decoder = new TextDecoder("utf-8");
          let buffer = "";
          let currentEvent = "";

          try {
            while (true) {
              const { done, value } = await reader.read();
              if (done) break;

              buffer += decoder.decode(value, { stream: true });
              const lines = buffer.split("\n");
              // Keep the last incomplete line in the buffer
              buffer = lines.pop() ?? "";

              for (const line of lines) {
                if (line.startsWith("event: ")) {
                  currentEvent = line.slice(7).trim();
                } else if (line.startsWith("data: ")) {
                  const jsonStr = line.slice(6);
                  handleSSEEvent(currentEvent, jsonStr);
                  currentEvent = "";
                }
                // Empty lines are event delimiters — we already handle per-line
              }
            }

            // Process any remaining buffer
            if (buffer.trim()) {
              const lines = buffer.split("\n");
              for (const line of lines) {
                if (line.startsWith("event: ")) {
                  currentEvent = line.slice(7).trim();
                } else if (line.startsWith("data: ")) {
                  const jsonStr = line.slice(6);
                  handleSSEEvent(currentEvent, jsonStr);
                  currentEvent = "";
                }
              }
            }
          } catch (streamErr) {
            // AbortError is expected when the user presses stop — do not retry
            if (streamErr instanceof DOMException && streamErr.name === "AbortError") {
              void qc.invalidateQueries({ queryKey: messageKeys.list(conversationId) });
              useChatStore.getState().resetReconnect();
              return;
            }
            // Any other mid-stream read error propagates to the retry logic below
            throw streamErr;
          }

          // 5. Stream completed successfully
          attemptSucceeded = true;
          void qc.invalidateQueries({ queryKey: messageKeys.list(conversationId) });
          void qc.invalidateQueries({ queryKey: conversationKeys.lists() });

        } catch (err) {
          // User intentionally aborted — never retry
          if (err instanceof DOMException && err.name === "AbortError") {
            void qc.invalidateQueries({ queryKey: messageKeys.list(conversationId) });
            useChatStore.getState().resetReconnect();
            return;
          }

          // Determine if the error is retryable:
          //   - TypeError means fetch itself failed (network unreachable, DNS, etc.)
          //   - An object with a status >= 500 is a server error
          const isNetworkError = err instanceof TypeError;
          const isServerError =
            err !== null &&
            typeof err === "object" &&
            "status" in err &&
            typeof (err as { status: unknown }).status === "number" &&
            (err as { status: number }).status >= 500;
          const isRetryable = isNetworkError || isServerError;

          const { reconnectAttempts, maxReconnectAttempts, startReconnect, resetReconnect } =
            useChatStore.getState();

          if (isRetryable && reconnectAttempts < maxReconnectAttempts) {
            // Exponential backoff: 1 s, 2 s, 4 s, 8 s, 16 s — capped at 30 s
            const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), 30000);
            startReconnect();
            await new Promise<void>((resolve) => setTimeout(resolve, delay));
            // Loop continues — re-establishes the SSE stream without touching the
            // optimistic user message or re-submitting to the DB
          } else {
            // Non-retryable error or max retries exceeded
            resetReconnect();
            markOptimisticFailed(qc, conversationId, optimisticId);
            if (reconnectAttempts >= maxReconnectAttempts) {
              setError("فشل الاتصال بعد عدة محاولات. يرجى المحاولة مرة أخرى.");
            } else {
              setError("حدث خطأ غير متوقع. يرجى المحاولة مرة أخرى.");
            }
            return;
          }
        }
      }

      function handleSSEEvent(eventType: string, jsonStr: string): void {
        try {
          const data = JSON.parse(jsonStr);

          switch (eventType) {
            case "message_start": {
              const payload = data as SSEMessageStart;
              assistantMessageId = payload.assistant_message_id;
              // Replace optimistic user message ID with real one
              replaceOptimisticId(qc, conversationId, optimisticId, payload.user_message_id);
              // Start streaming the assistant message
              useChatStore.getState().startStreaming(payload.assistant_message_id);
              break;
            }
            case "token": {
              const payload = data as SSEToken;
              useChatStore.getState().appendToken(payload.text);
              break;
            }
            case "citations": {
              const payload = data as SSECitations;
              useChatStore.getState().setStreamingCitations(payload.articles);
              break;
            }
            case "done": {
              const _payload = data as SSEDone;
              // Inject assistant message into cache BEFORE clearing streaming state
              // so there's no flash (streaming bubble disappears → same text reappears from server)
              const finalContent = useChatStore.getState().streamingContent;
              const finalCitations = useChatStore.getState().streamingCitations;
              if (assistantMessageId && finalContent) {
                qc.setQueryData<{ pages: MessageListResponse[]; pageParams: (string | undefined)[] }>(
                  messageKeys.list(conversationId),
                  (old) => {
                    if (!old) return old;
                    const newPages = [...old.pages];
                    newPages[0] = {
                      ...newPages[0],
                      messages: [
                        {
                          message_id: assistantMessageId!,
                          conversation_id: conversationId,
                          role: "assistant" as const,
                          content: finalContent,
                          attachments: [],
                          created_at: new Date().toISOString(),
                          metadata:
                            finalCitations.length > 0
                              ? { citations: finalCitations }
                              : undefined,
                        },
                        ...newPages[0].messages,
                      ],
                    };
                    return { ...old, pages: newPages };
                  }
                );
              }
              // Clear streaming state without aborting the fetch
              useChatStore.getState().finishStreaming();
              break;
            }
            case "workspace_item_created": {
              const payload = data as SSEWorkspaceItemCreated;
              void qc.invalidateQueries({
                queryKey: workspaceKeys.byConversation(conversationId),
              });
              useChatStore.getState().openWorkspaceItem(payload.item_id);
              break;
            }
            case "agent_selected": {
              const payload = data as SSEAgentSelected;
              useChatStore.getState().setSelectedAgentFamily(payload.agent_family);
              break;
            }
            case "agent_run_started": {
              const payload = data as SSEAgentRunStarted;
              useChatStore.getState().startAgentRun(payload.agent_family, payload.subtype ?? null);
              break;
            }
            case "agent_run_finished": {
              const _payload = data as SSEAgentRunFinished;
              useChatStore.getState().finishAgentRun();
              break;
            }
            case "workspace_item_updated": {
              const payload = data as SSEWorkspaceItemUpdated;
              void qc.invalidateQueries({
                queryKey: workspaceKeys.byConversation(conversationId),
              });
              void qc.invalidateQueries({
                queryKey: workspaceKeys.detail(payload.item_id),
              });
              break;
            }
            case "workspace_item_locked": {
              const payload = data as SSEWorkspaceItemLocked;
              patchWorkspaceItemLock(qc, conversationId, payload.item_id, payload.locked_until);
              break;
            }
            case "workspace_item_unlocked": {
              const payload = data as SSEWorkspaceItemUnlocked;
              patchWorkspaceItemLock(qc, conversationId, payload.item_id, null);
              break;
            }
            case "heartbeat":
              // Keep-alive ping from server — ignore silently
              break;
            case "error": {
              const errorMsg = (data as { detail?: string }).detail ?? "حدث خطأ أثناء المعالجة";
              useChatStore.getState().setError(errorMsg);
              break;
            }
          }
        } catch {
          // JSON parse error — skip malformed event
        }
      }
    },
    [qc, startStreaming, appendToken, storeStopStreaming, setError, setAbortController]
  );

  const stopStreaming = useCallback(() => {
    useChatStore.getState().stopStreaming();
  }, []);

  /**
   * Find all messages in the query cache for a conversation, flattened
   * in chronological order (oldest first).
   */
  const getMessagesFromCache = useCallback(
    (conversationId: string): Message[] => {
      const cached = qc.getQueryData<{
        pages: MessageListResponse[];
        pageParams: (string | undefined)[];
      }>(messageKeys.list(conversationId));
      if (!cached?.pages) return [];

      const all: Message[] = [];
      // Pages are [newest, older, oldest...], messages within each are newest-first
      for (let i = cached.pages.length - 1; i >= 0; i--) {
        const page = cached.pages[i];
        all.push(...[...page.messages].reverse());
      }
      return all;
    },
    [qc]
  );

  /**
   * Regenerate: find the user message that preceded the given assistant message
   * and re-send it through the normal sendMessage flow.
   */
  const regenerateMessage = useCallback(
    async ({ conversationId, messageId, caseId }: RegenerateParams) => {
      const messages = getMessagesFromCache(conversationId);
      const assistantIdx = messages.findIndex((m) => m.message_id === messageId);
      if (assistantIdx < 0) return;

      // Walk backwards from the assistant message to find the preceding user message
      let userContent: string | null = null;
      for (let i = assistantIdx - 1; i >= 0; i--) {
        if (messages[i].role === "user") {
          userContent = messages[i].content;
          break;
        }
      }

      if (!userContent) return;

      await sendMessage({ conversationId, content: userContent, caseId });
    },
    [sendMessage, getMessagesFromCache]
  );

  /**
   * Edit and resend: send the new (edited) content as a fresh message.
   * The original message stays in history.
   */
  const editAndResend = useCallback(
    async ({ conversationId, newContent, caseId }: EditAndResendParams) => {
      if (!newContent.trim()) return;
      await sendMessage({ conversationId, content: newContent, caseId });
    },
    [sendMessage]
  );

  /**
   * Retry: re-send a failed message using its original content.
   */
  const retryMessage = useCallback(
    async ({ conversationId, messageId, caseId }: RetryParams) => {
      const messages = getMessagesFromCache(conversationId);
      const failedMsg = messages.find((m) => m.message_id === messageId);
      if (!failedMsg) return;

      // Remove the failed message from cache before re-sending
      qc.setQueryData<{
        pages: MessageListResponse[];
        pageParams: (string | undefined)[];
      }>(messageKeys.list(conversationId), (old) => {
        if (!old) return old;
        return {
          ...old,
          pages: old.pages.map((page) => ({
            ...page,
            messages: page.messages.filter((m) => m.message_id !== messageId),
          })),
        };
      });

      await sendMessage({ conversationId, content: failedMsg.content, caseId });
    },
    [sendMessage, getMessagesFromCache, qc]
  );

  return { sendMessage, stopStreaming, regenerateMessage, editAndResend, retryMessage };
}

// -----------------------------------------------
// Helper: mark an optimistic message as failed
// -----------------------------------------------

function markOptimisticFailed(
  qc: ReturnType<typeof useQueryClient>,
  conversationId: string,
  optimisticId: string
): void {
  qc.setQueryData<{ pages: MessageListResponse[]; pageParams: (string | undefined)[] }>(
    messageKeys.list(conversationId),
    (old) => {
      if (!old) return old;
      return {
        ...old,
        pages: old.pages.map((page) => ({
          ...page,
          messages: page.messages.map((msg) =>
            msg.message_id === optimisticId
              ? { ...msg, isFailed: true, isOptimistic: false }
              : msg
          ),
        })),
      };
    }
  );
}

// -----------------------------------------------
// Helper: replace optimistic ID with real server ID
// -----------------------------------------------

/**
 * Patch the lock state of a workspace_item across both the detail cache and
 * the conversation list cache so the NoteEditor's lock UI flips immediately
 * without a network refetch. ``locked_until = null`` clears the lock.
 */
function patchWorkspaceItemLock(
  qc: ReturnType<typeof useQueryClient>,
  conversationId: string,
  itemId: string,
  lockedUntil: string | null,
): void {
  const apply = <T extends Pick<WorkspaceItem, "item_id" | "metadata">>(
    item: T,
  ): T => ({
    ...item,
    metadata: {
      ...(item.metadata ?? {}),
      locked_until: lockedUntil,
    },
    locked_by_agent_until: lockedUntil,
  } as T);

  qc.setQueryData<WorkspaceItem>(workspaceKeys.detail(itemId), (old) =>
    old && old.item_id === itemId ? apply(old) : old,
  );

  qc.setQueryData<WorkspaceItemListResponse>(
    workspaceKeys.byConversation(conversationId),
    (old) => {
      if (!old) return old;
      return {
        ...old,
        items: old.items.map((it) => (it.item_id === itemId ? apply(it) : it)),
      };
    },
  );
}

function replaceOptimisticId(
  qc: ReturnType<typeof useQueryClient>,
  conversationId: string,
  optimisticId: string,
  realId: string
): void {
  qc.setQueryData<{ pages: MessageListResponse[]; pageParams: (string | undefined)[] }>(
    messageKeys.list(conversationId),
    (old) => {
      if (!old) return old;
      return {
        ...old,
        pages: old.pages.map((page) => ({
          ...page,
          messages: page.messages.map((msg) =>
            msg.message_id === optimisticId
              ? { ...msg, message_id: realId, isOptimistic: false }
              : msg
          ),
        })),
      };
    }
  );
}
