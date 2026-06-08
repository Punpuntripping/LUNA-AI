import { useCallback } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { messagesApi } from "@/lib/api";
import { useChatStore } from "@/stores/chat-store";
import { messageKeys } from "@/hooks/use-messages";
import { conversationKeys } from "@/hooks/use-conversations";
import { workspaceKeys } from "@/hooks/use-workspace";
import type {
  Attachment,
  Message,
  MessageListResponse,
  SSEMessageStart,
  SSEToken,
  SSEDone,
  SSEDuplicate,
  SSEQuotaExceeded,
  SSEAgentRunStarted,
  SSEAgentRunFinished,
  SSEAgentQuestion,
  SSEAgentResumed,
  SSEWorkspaceItemCreated,
  SSEWorkspaceItemUpdated,
  SSEWorkspaceItemLocked,
  SSEWorkspaceItemUnlocked,
  SSEReferencedExistingItem,
  SSETemplateSaveOffer,
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
    async ({ conversationId, content }: SendMessageParams) => {
      // A new send supersedes any stream still in flight. The store tracks a
      // single global stream buffer, so abort + clear the previous stream
      // first — otherwise two conversations' tokens interleave into the same
      // buffer. regenerate/retry/editAndResend all funnel through here, so
      // this one guard covers every send path.
      storeStopStreaming();

      // 0. Collect already-uploaded attachment ids.
      //
      // Phase 2 (upload reliability): files are uploaded the moment the
      // user picks them via the resumable TUS flow in ChatInput. By the
      // time send runs every kept pending file should be in status
      // 'completed' with a valid item_id (ChatInput blocks send while
      // any upload is in flight). Failed / cancelled files contribute
      // no attachment_ids — we silently drop them so the message still
      // sends with whatever made it through.
      const { pendingFiles, clearPendingFiles } = useChatStore.getState();
      const attachmentIds: string[] = pendingFiles
        .filter((pf) => pf.uploadStatus === "completed" && pf.itemId)
        .map((pf) => pf.itemId as string);

      // Build optimistic attachment list from pending files (for UI display).
      // We carry every pending file into the optimistic bubble so the user
      // sees what they intended to attach; the server-side state of the row
      // is the source of truth for what the agent actually receives.
      const optimisticAttachments = pendingFiles.map((pf) => ({
        id: pf.id,
        document_id: pf.itemId ?? pf.id,
        attachment_type: (pf.mimeType === "application/pdf"
          ? "pdf"
          : pf.mimeType.startsWith("image/")
            ? "image"
            : "file") as "pdf" | "image" | "file",
        filename: pf.name,
        file_size: pf.size,
      }));

      // Clear pending files immediately after capture — the bytes already
      // live on Supabase, the workspace cache will refresh on its own.
      if (pendingFiles.length > 0) clearPendingFiles();

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
        // Window C: user messages never carry artifact_ids; explicit so the
        // shape stays consistent with backend MessageResponse.
        artifact_ids: undefined,
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
      // Layer 2: flips true once the backend confirms the run is committed
      // (message_start arrives → user row saved, placeholder created, slot
      // reserved). After that, a dropped stream must NOT re-POST — the run
      // finishes in the background, so we recover it with a read-only poll.
      let messageStartSeen = false;

      const sendOptions = {
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

          // Layer 2: the run is already committed server-side (we received
          // message_start). The backend keeps it alive in the background after
          // a disconnect (detach-to-background), so re-POSTing would only
          // collide with it (dedup-rejected) and never surface the answer.
          // Instead, recover read-only: refresh so the placeholder shows as
          // "thinking" (Layer 1); useMessages.refetchInterval then polls until
          // the background run fills it. The running pipeline is never touched.
          if (messageStartSeen) {
            void qc.invalidateQueries({ queryKey: messageKeys.list(conversationId) });
            useChatStore.getState().finishStreaming();
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
              messageStartSeen = true;
              // Re-assert the user message from the real id. The normal path
              // swaps the optimistic row in place; but a brand-new
              // conversation's initial messages-fetch can land empty (before
              // the user row is persisted) and wipe the optimistic bubble — in
              // that case this re-adds it so the message never disappears.
              reinstateUserMessage(
                qc,
                conversationId,
                optimisticId,
                payload.user_message_id,
                messageContent,
                optimisticAttachments,
              );
              // Start streaming the assistant message, tagged with the
              // conversation so other conversations don't render this stream.
              useChatStore
                .getState()
                .startStreaming(payload.assistant_message_id, conversationId);
              break;
            }
            case "duplicate": {
              // Backend rejected this send: a pipeline is already running for
              // this conversation (per-conversation dedup). Drop our optimistic
              // duplicate user message and refetch — the existing in-flight
              // assistant message will fill in on completion. Do NOT mark the
              // message failed or retry; this is expected, not an error.
              const payload = data as SSEDuplicate;
              removeOptimisticMessage(qc, conversationId, optimisticId);
              useChatStore.getState().finishStreaming();
              useChatStore.getState().resetReconnect();
              void qc.invalidateQueries({
                queryKey: messageKeys.list(conversationId),
              });
              // Surface a brief, non-error notice so the user understands the
              // resend was absorbed rather than silently dropped. The in-flight
              // run fills the card via useMessages.refetchInterval (Layer 2) —
              // we never re-POST, so the running pipeline is untouched.
              useChatStore.getState().setError(payload.detail);
              break;
            }
            case "quota_exceeded": {
              // Per-user quota gate (shared/quota) rejected this send. The
              // user message is saved server-side; no assistant placeholder
              // exists. Refetch so the message list catches up, surface the
              // banner via quotaInfo, end streaming. Don't mark failed —
              // the message itself is fine; the request just couldn't run.
              const payload = data as SSEQuotaExceeded;
              useChatStore.getState().finishStreaming();
              useChatStore.getState().resetReconnect();
              useChatStore.getState().setQuotaInfo(payload);
              void qc.invalidateQueries({
                queryKey: messageKeys.list(conversationId),
              });
              break;
            }
            case "token": {
              const payload = data as SSEToken;
              useChatStore.getState().appendToken(payload.text);
              break;
            }
            case "done": {
              const payload = data as SSEDone;
              // Inject assistant message into cache BEFORE clearing streaming state
              // so there's no flash (streaming bubble disappears → same text reappears from server)
              const finalContent = useChatStore.getState().streamingContent;
              if (assistantMessageId && finalContent) {
                qc.setQueryData<{ pages: MessageListResponse[]; pageParams: (string | undefined)[] }>(
                  messageKeys.list(conversationId),
                  (old) => {
                    if (!old) return old;
                    // Dedupe: the post-stream invalidate (around line 272) refetches
                    // the messages list and the server now returns the persisted
                    // assistant message. If that refetch lands before this `done`
                    // handler, prepending again produces a duplicate `message_id`
                    // (React keyed-children warning). Skip the prepend when the id
                    // is already present anywhere in the cached pages.
                    const alreadyPresent = old.pages.some((page) =>
                      page.messages.some(
                        (m) => m.message_id === assistantMessageId,
                      ),
                    );
                    if (alreadyPresent) return old;
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
                          // Window B Tasks 5–7: read the linkage from the live
                          // `done` event so MessageBubble's `hasArtifacts`
                          // gate flips True immediately — no wait for the
                          // post-stream invalidate + refetch. Backend echoes
                          // null when the turn produced nothing.
                          artifact_ids: payload.artifact_ids ?? null,
                          referenced_item_ids: payload.referenced_item_ids ?? null,
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
              useChatStore
                .getState()
                .openWorkspaceItem(conversationId, payload.item_id);
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
            case "agent_question": {
              // Agent paused via ask_user. The orchestrator already persisted the
              // question as a 'assistant' message with metadata.kind='agent_question'
              // and will emit `done` immediately after, which triggers the messages
              // refetch — so we only need to clear the running-spinner here. The
              // question bubble will appear once the cache invalidation in the
              // post-stream block lands.
              const _payload = data as SSEAgentQuestion;
              useChatStore.getState().finishAgentRun();
              // Also clear any in-progress streaming bubble — the assistant
              // message that arrives via refetch is the canonical question.
              useChatStore.getState().finishStreaming();
              break;
            }
            case "agent_resumed": {
              // Server resumed a paused agent_run after the user replied.
              // Surface the spinner again so the UI shows the agent is working.
              const payload = data as SSEAgentResumed;
              useChatStore.getState().startAgentRun(payload.agent_family, null);
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
            case "referenced_existing_item": {
              // Phase E (full_redesign §3.4a / §6.3 / §9 O5):
              // Planner's responder decided a prior workspace_item already
              // covers this question — no new card was published. Stash the
              // referenced item_id against the in-flight assistant message
              // so the MessageBubble can render a clickable chip
              // ("راجع البطاقة السابقة") that jumps to and highlights the
              // existing card in the workspace pane.
              const payload = data as SSEReferencedExistingItem;
              if (assistantMessageId) {
                useChatStore
                  .getState()
                  .recordReferencedItem(assistantMessageId, payload.item_id);
              }
              break;
            }
            case "template_save_offer": {
              // Wave E (writer_planner_user_templates §D6): the writer
              // pipeline judged an attached doc template-worthy and offered to
              // save it (non-blocking, emitted after publish). Stash the offer
              // against the in-flight assistant message so MessageBubble can
              // render the «احفظ المرفق كقالب؟ [نعم]» chip. Like
              // ``referenced_existing_item`` this lives on the store (keyed by
              // message_id) so it survives the post-stream messages-cache
              // invalidate. Ephemeral — not persisted across reload.
              const payload = data as SSETemplateSaveOffer;
              if (assistantMessageId) {
                useChatStore
                  .getState()
                  .recordTemplateOffer(
                    assistantMessageId,
                    payload.item_id,
                    payload.title_hint,
                  );
              }
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
// Helper: remove an optimistic message entirely
// -----------------------------------------------

/**
 * Drop an optimistic user message from the cache. Used when a send is rejected
 * as a duplicate (a pipeline is already running for the conversation): the
 * optimistic bubble is a duplicate of the message that's already being
 * answered, so we remove it rather than leaving it stuck or flagging it failed.
 */
function removeOptimisticMessage(
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
          messages: page.messages.filter((m) => m.message_id !== optimisticId),
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

/**
 * Upsert the user message into the cache once the server confirms it
 * (message_start). Normally this just swaps the optimistic row's id in place.
 * But the initial messages-fetch for a brand-new conversation can resolve
 * empty (before the user row is persisted) and wipe the optimistic bubble — in
 * that case the optimistic id is gone, so we re-add the user message from the
 * real id + content so it never disappears mid-run.
 */
function reinstateUserMessage(
  qc: QueryClient,
  conversationId: string,
  optimisticId: string,
  realId: string,
  content: string,
  attachments: Attachment[],
): void {
  qc.setQueryData<{ pages: MessageListResponse[]; pageParams: (string | undefined)[] }>(
    messageKeys.list(conversationId),
    (old) => {
      const base =
        old ?? { pages: [{ messages: [], has_more: false }], pageParams: [undefined] };
      const present = base.pages.some((p) =>
        p.messages.some(
          (m) => m.message_id === optimisticId || m.message_id === realId,
        ),
      );
      if (present) {
        // Optimistic (or real) row still in cache → swap the id in place.
        return {
          ...base,
          pages: base.pages.map((page) => ({
            ...page,
            messages: page.messages.map((msg) =>
              msg.message_id === optimisticId
                ? { ...msg, message_id: realId, isOptimistic: false }
                : msg,
            ),
          })),
        };
      }
      // Optimistic row was clobbered by an empty fetch → re-add it.
      const restored: Message = {
        message_id: realId,
        conversation_id: conversationId,
        role: "user",
        content,
        attachments,
        created_at: new Date().toISOString(),
        artifact_ids: undefined,
      };
      const pages = base.pages.length ? base.pages : [{ messages: [], has_more: false }];
      const newPages = [...pages];
      newPages[0] = { ...newPages[0], messages: [restored, ...newPages[0].messages] };
      return { ...base, pages: newPages };
    },
  );
}
