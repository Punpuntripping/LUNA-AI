import { useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { messagesApi } from "@/lib/api";
import { useChatStore } from "@/stores/chat-store";
import { messageKeys } from "@/hooks/use-messages";
import { conversationKeys } from "@/hooks/use-conversations";
import { artifactKeys } from "@/hooks/use-artifacts";
import type { Message, MessageListResponse, SSEMessageStart, SSEToken, SSECitations, SSEDone, SSEArtifactCreated, SSEAgentSelected, Citation } from "@/types";

interface SendMessageParams {
  conversationId: string;
  content: string;
}

interface UseSendMessageReturn {
  sendMessage: (params: SendMessageParams) => Promise<void>;
  stopStreaming: () => void;
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
      // 1. Create optimistic user message
      const optimisticId = `optimistic-${Date.now()}`;
      const optimisticMessage: Message = {
        message_id: optimisticId,
        conversation_id: conversationId,
        role: "user",
        content,
        attachments: [],
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

      // 2. Set up AbortController
      const controller = new AbortController();
      setAbortController(controller);

      let assistantMessageId: string | null = null;
      let citations: Citation[] = [];

      try {
        // 3. Send message via SSE (with agent selection + modifiers from chat store)
        const { selectedAgentFamily, modifiers } = useChatStore.getState();
        const response = await messagesApi.send(
          conversationId, content, controller.signal,
          {
            agent_family: selectedAgentFamily ?? undefined,
            modifiers: modifiers.length ? modifiers : undefined,
          }
        );
        // Clear agent selection after send
        useChatStore.getState().resetAgentSelection();

        if (!response.ok) {
          let errorDetail = "حدث خطأ أثناء إرسال الرسالة";
          try {
            const errorBody = await response.json();
            if (errorBody.detail) errorDetail = errorBody.detail;
          } catch {
            // Use default Arabic error
          }
          // Mark optimistic message as failed
          markOptimisticFailed(qc, conversationId, optimisticId);
          setError(errorDetail);
          return;
        }

        if (!response.body) {
          markOptimisticFailed(qc, conversationId, optimisticId);
          setError("لم يتم استلام استجابة من الخادم");
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
        } catch (err) {
          // AbortError is expected when user stops streaming
          if (err instanceof DOMException && err.name === "AbortError") {
            // Streaming was intentionally stopped
          } else {
            throw err;
          }
        }

        // 5. On completion, invalidate to refetch from server
        void qc.invalidateQueries({ queryKey: messageKeys.list(conversationId) });
        void qc.invalidateQueries({ queryKey: conversationKeys.lists() });
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") {
          // User intentionally stopped — invalidate to get server state
          void qc.invalidateQueries({ queryKey: messageKeys.list(conversationId) });
          return;
        }
        markOptimisticFailed(qc, conversationId, optimisticId);
        setError("حدث خطأ غير متوقع. يرجى المحاولة مرة أخرى.");
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
              citations = payload.articles;
              break;
            }
            case "done": {
              const _payload = data as SSEDone;
              // Inject assistant message into cache BEFORE clearing streaming state
              // so there's no flash (streaming bubble disappears → same text reappears from server)
              const finalContent = useChatStore.getState().streamingContent;
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
            case "artifact_created": {
              const payload = data as SSEArtifactCreated;
              void qc.invalidateQueries({ queryKey: artifactKeys.byConversation(conversationId) });
              useChatStore.getState().openArtifactPanel(payload.artifact_id);
              break;
            }
            case "agent_selected": {
              const payload = data as SSEAgentSelected;
              useChatStore.getState().setSelectedAgentFamily(payload.agent_family);
              break;
            }
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

  return { sendMessage, stopStreaming };
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
