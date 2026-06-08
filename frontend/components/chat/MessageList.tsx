"use client";

import { useEffect, useRef, useCallback, useMemo, useState } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { useMessages } from "@/hooks/use-messages";
import { useConversationWorkspace } from "@/hooks/use-workspace";
import { useChatStore } from "@/stores/chat-store";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { TypingIndicator } from "@/components/chat/TypingIndicator";
import { ScrollToBottom } from "@/components/chat/ScrollToBottom";
import type { Message, WorkspaceItemKind } from "@/types";

/**
 * Layer 1: an assistant row with no content yet is an in-flight placeholder —
 * the backend inserts the assistant message empty at run start and only fills
 * it when the pipeline completes. It must never render as a finished (blank)
 * card; the list shows a thinking indicator for it instead.
 */
function isEmptyAssistantRow(m: Message): boolean {
  return (
    m.role === "assistant" &&
    !m.isOptimistic &&
    (m.content ?? "").trim() === "" &&
    !(m.artifact_ids && m.artifact_ids.length > 0)
  );
}

/** Pixel threshold: user is considered "near bottom" if within this distance. */
const NEAR_BOTTOM_THRESHOLD = 100;

interface MessageListProps {
  conversationId: string;
  className?: string;
  /** Called when user clicks Regenerate on an assistant message */
  onRegenerate?: (messageId: string) => void;
  /** Called when user edits their own message and clicks Save & Send */
  onEditResend?: (messageId: string, newContent: string) => void;
  /** Called when user clicks Retry on a failed message */
  onRetry?: (messageId: string) => void;
}

export function MessageList({
  conversationId,
  className,
  onRegenerate,
  onEditResend,
  onRetry,
}: MessageListProps) {
  const {
    data,
    isLoading,
    isFetchingNextPage,
    hasNextPage,
    fetchNextPage,
  } = useMessages(conversationId);

  const streamingMessageId = useChatStore((s) => s.streamingMessageId);
  const streamingContent = useChatStore((s) => s.streamingContent);
  // The streaming buffer is global; only treat it as "streaming here" when the
  // active stream actually belongs to this conversation. Without this guard one
  // conversation's stream renders inside every other conversation.
  const isStreaming = useChatStore(
    (s) => s.isStreaming && s.streamingConversationId === conversationId,
  );

  // Window C: artifact lookup keyed by workspace_item.item_id → {kind, title}.
  // Re-uses the workspace list query the WorkspacePane already loads so this
  // is free of additional network cost in steady state.
  const { data: workspaceData } = useConversationWorkspace(conversationId);
  const artifactLookup = useMemo(() => {
    const out: Record<string, { kind: WorkspaceItemKind; title: string }> = {};
    for (const item of workspaceData?.items ?? []) {
      out[item.item_id] = { kind: item.kind, title: item.title };
    }
    return out;
  }, [workspaceData?.items]);

  const openWorkspaceItem = useChatStore((s) => s.openWorkspaceItem);
  const openWorkspaceItemAtReference = useChatStore(
    (s) => s.openWorkspaceItemAtReference,
  );
  const highlightWorkspaceItem = useChatStore((s) => s.highlightWorkspaceItem);
  // Phase E (§9 O5): item ids the planner referenced for each assistant
  // message in this conversation. Reading the whole map is cheap (keyed by
  // message_id, sparse) and means a new SSE event causes an O(1) selector
  // re-render even when the message-cache is otherwise stale.
  const referencedItemsByMessage = useChatStore(
    (s) => s.referencedItemsByMessage,
  );
  // Wave E (writer_planner_user_templates §D6): the "save attachment as
  // template" offer for each assistant message in this conversation, keyed by
  // message_id. Same rationale as ``referencedItemsByMessage`` — store-keyed
  // so the chip survives the post-stream messages-cache invalidate. Ephemeral
  // (live session only), so it's read solely from the store.
  const templateOffersByMessage = useChatStore(
    (s) => s.templateOffersByMessage,
  );

  const handleOpenArtifact = useCallback(
    (itemId: string) => {
      openWorkspaceItem(conversationId, itemId);
    },
    [openWorkspaceItem, conversationId],
  );

  // Phase E (§9 O5): chip-click handler — opens the workspace pane and
  // briefly rings the matching card.
  const handleJumpToReferencedItem = useCallback(
    (itemId: string) => {
      highlightWorkspaceItem(conversationId, itemId);
    },
    [highlightWorkspaceItem, conversationId],
  );

  // Citation clicks always target the message's first agent_search artifact.
  // Resolves the id from the lookup; no-op if the message has no agent_search
  // among its artifacts (defensive — should not happen for deep_search runs).
  const buildCitationHandler = useCallback(
    (artifactIds: string[] | null | undefined) => {
      if (!artifactIds || artifactIds.length === 0) return undefined;
      const firstSearchId = artifactIds.find(
        (id) => artifactLookup[id]?.kind === "agent_search",
      );
      if (!firstSearchId) return undefined;
      return (n: number) => {
        openWorkspaceItemAtReference(conversationId, firstSearchId, n);
      };
    },
    [artifactLookup, openWorkspaceItemAtReference, conversationId],
  );

  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const prevMessageCountRef = useRef(0);
  const isInitialLoadRef = useRef(true);

  // -----------------------------------------------
  // Smart scroll state
  // -----------------------------------------------
  const [isNearBottom, setIsNearBottom] = useState(true);
  const [newMessageCount, setNewMessageCount] = useState(0);
  const isNearBottomRef = useRef(true);
  const rafIdRef = useRef<number | null>(null);

  // Flatten pages into a single array, reversing since API returns newest-first.
  // Dedupe by message_id — the SSE `done` handler optimistically prepends the
  // assistant message into page 0 just before the post-stream invalidate
  // refetches the list, so the same id can land in two pages until the next
  // render settles. Skipping the second occurrence keeps React's keyed-children
  // happy without losing the no-flash UX.
  const messages: Message[] = useMemo(() => {
    if (!data?.pages) return [];
    const seen = new Set<string>();
    const all: Message[] = [];
    // Pages are in order [newest, older, oldest...]
    // We need oldest-first for display, so reverse pages then reverse messages within
    for (let i = data.pages.length - 1; i >= 0; i--) {
      const page = data.pages[i];
      // Messages within a page are newest-first, so reverse them
      for (const m of [...page.messages].reverse()) {
        if (seen.has(m.message_id)) continue;
        seen.add(m.message_id);
        all.push(m);
      }
    }
    return all;
  }, [data?.pages]);

  // -----------------------------------------------
  // Scroll position tracking (throttled via rAF)
  // -----------------------------------------------
  const handleScroll = useCallback(() => {
    // Cancel any pending rAF to avoid stacking
    if (rafIdRef.current !== null) return;

    rafIdRef.current = requestAnimationFrame(() => {
      rafIdRef.current = null;
      const container = scrollContainerRef.current;
      if (!container) return;

      const distanceFromBottom =
        container.scrollHeight - container.scrollTop - container.clientHeight;
      const nearBottom = distanceFromBottom <= NEAR_BOTTOM_THRESHOLD;

      // Only update state (and trigger re-render) when the value changes
      if (nearBottom !== isNearBottomRef.current) {
        isNearBottomRef.current = nearBottom;
        setIsNearBottom(nearBottom);

        // When user scrolls back to bottom, clear the new message badge
        if (nearBottom) {
          setNewMessageCount(0);
        }
      }
    });
  }, []);

  // Cleanup rAF on unmount
  useEffect(() => {
    return () => {
      if (rafIdRef.current !== null) {
        cancelAnimationFrame(rafIdRef.current);
      }
    };
  }, []);

  // -----------------------------------------------
  // Auto-scroll to bottom (smart: respects user intent)
  // -----------------------------------------------
  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return;

    const currentCount = messages.length;
    const isNewMessage = currentCount > prevMessageCountRef.current;
    prevMessageCountRef.current = currentCount;

    // On initial load, always scroll instantly to bottom
    if (isInitialLoadRef.current && currentCount > 0) {
      isInitialLoadRef.current = false;
      container.scrollTop = container.scrollHeight;
      // Mark user as "near bottom" after initial scroll
      isNearBottomRef.current = true;
      setIsNearBottom(true);
      return;
    }

    // During streaming: only auto-scroll if user is near the bottom
    if (isStreaming && isNearBottomRef.current) {
      container.scrollTop = container.scrollHeight;
      return;
    }

    // New message arrived (not streaming): scroll if near bottom, else bump badge
    if (isNewMessage && !isStreaming) {
      if (isNearBottomRef.current) {
        container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
      } else {
        // User is scrolled up — increment new message badge count
        setNewMessageCount((prev) => prev + 1);
      }
    }
  }, [messages.length, isStreaming, streamingContent]);

  // Reset initial load flag when conversation changes
  useEffect(() => {
    isInitialLoadRef.current = true;
    prevMessageCountRef.current = 0;
    isNearBottomRef.current = true;
    setIsNearBottom(true);
    setNewMessageCount(0);
  }, [conversationId]);

  // -----------------------------------------------
  // Scroll-to-bottom button handler
  // -----------------------------------------------
  const scrollToBottom = useCallback(() => {
    const container = scrollContainerRef.current;
    if (!container) return;
    container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
    // Optimistically set near bottom so button hides immediately
    isNearBottomRef.current = true;
    setIsNearBottom(true);
    setNewMessageCount(0);
  }, []);

  // -----------------------------------------------
  // Intersection observer for infinite scroll (load older messages)
  // -----------------------------------------------
  const observerRef = useRef<IntersectionObserver | null>(null);
  const topSentinelCallback = useCallback(
    (node: HTMLDivElement | null) => {
      if (observerRef.current) observerRef.current.disconnect();
      if (!node) return;

      observerRef.current = new IntersectionObserver(
        (entries) => {
          if (entries[0].isIntersecting && hasNextPage && !isFetchingNextPage) {
            void fetchNextPage();
          }
        },
        { threshold: 0.1 }
      );
      observerRef.current.observe(node);
    },
    [hasNextPage, isFetchingNextPage, fetchNextPage]
  );

  // Loading skeleton
  if (isLoading) {
    return (
      <div className={cn("flex-1 flex flex-col gap-4 p-4", className)}>
        {[1, 2, 3].map((i) => (
          <div
            key={i}
            className={cn(
              "rounded-2xl h-16 animate-pulse bg-muted",
              i % 2 === 0 ? "ms-auto w-3/5" : "me-auto w-2/3"
            )}
          />
        ))}
      </div>
    );
  }

  // Empty state
  if (messages.length === 0 && !isStreaming) {
    return (
      <div
        dir="rtl"
        lang="ar"
        className={cn(
          "flex-1 flex items-center justify-center text-center p-8",
          className
        )}
      >
        <p className="text-sm text-muted-foreground">
          ابدأ المحادثة بإرسال رسالة
        </p>
      </div>
    );
  }

  // Layer 1: if an empty placeholder is already in the list it renders its own
  // thinking indicator above — don't also show the standalone one (avoids two).
  const hasIncompletePlaceholder = messages.some(isEmptyAssistantRow);

  return (
    <div
      ref={scrollContainerRef}
      onScroll={handleScroll}
      className={cn("flex-1 overflow-y-auto relative", className)}
    >
      <div className="flex flex-col p-4 min-h-full max-w-3xl mx-auto w-full">
        {/* Top sentinel for infinite scroll */}
        <div ref={topSentinelCallback} className="h-1 shrink-0" />

        {/* Fetching older messages indicator */}
        {isFetchingNextPage && (
          <div className="flex justify-center py-3">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        )}

        {/* Messages */}
        {messages.map((msg) => {
          // `isStreaming` is already scoped to this conversation, so the
          // global streamingContent is only ever applied to its own stream.
          const isStreamingThis =
            isStreaming &&
            (msg.isStreaming ||
              (streamingMessageId !== null &&
                msg.message_id === streamingMessageId));
          // Layer 1: never render an empty assistant placeholder as a finished
          // card. Show the thinking state until content lands — independent of
          // `isStreaming`, so it survives a dropped stream, reconnect, or a
          // page refresh while the run is still in flight.
          const liveContent = isStreamingThis
            ? (streamingContent ?? "")
            : msg.content;
          if (
            msg.role === "assistant" &&
            !msg.isOptimistic &&
            (liveContent ?? "").trim() === "" &&
            !(msg.artifact_ids && msg.artifact_ids.length > 0)
          ) {
            return (
              <div key={msg.message_id} className="flex justify-end mb-4">
                <TypingIndicator />
              </div>
            );
          }
          const ids = msg.artifact_ids;
          // Window B Tasks 5–7: prefer the persisted row value over the
          // store-only entry. The store is populated live by the
          // ``referenced_existing_item`` SSE event but does not survive a
          // refresh; the persisted column on ``messages.referenced_item_ids``
          // is the durable source.
          const referencedIds =
            (Array.isArray(msg.referenced_item_ids) && msg.referenced_item_ids.length > 0
              ? msg.referenced_item_ids
              : undefined) ?? referencedItemsByMessage[msg.message_id];
          return (
            <MessageBubble
              key={msg.message_id}
              message={
                isStreamingThis
                  ? { ...msg, isStreaming: true }
                  : msg
              }
              streamingContent={isStreamingThis ? streamingContent : undefined}
              onRegenerate={onRegenerate}
              onEditResend={onEditResend}
              onRetry={onRetry}
              artifactIds={ids}
              artifactLookup={artifactLookup}
              onOpenArtifact={handleOpenArtifact}
              onCitationClick={buildCitationHandler(ids)}
              referencedItemIds={referencedIds}
              onJumpToReferencedItem={handleJumpToReferencedItem}
              templateOffer={templateOffersByMessage[msg.message_id]}
            />
          );
        })}

        {/* Typing indicator: streaming started but no content yet — unless an
            empty placeholder row is already showing its own (Layer 1). */}
        {isStreaming && streamingContent === "" && !hasIncompletePlaceholder && (
          <div className="flex justify-end mb-4">
            <TypingIndicator />
          </div>
        )}

        {/* Streaming assistant bubble (when message_id hasn't been added to the query cache yet) */}
        {isStreaming &&
          streamingMessageId &&
          streamingContent !== "" &&
          !messages.some((m) => m.message_id === streamingMessageId) && (
            <MessageBubble
              message={{
                message_id: streamingMessageId,
                conversation_id: conversationId,
                role: "assistant",
                content: "",
                attachments: [],
                created_at: new Date().toISOString(),
                // Window C: stream-in-progress bubbles never carry artifacts;
                // the chip and citation clicks stay disabled until the message
                // is replaced by the canonical row from the messages cache.
                artifact_ids: undefined,
                isStreaming: true,
              }}
              streamingContent={streamingContent}
            />
          )}

        {/* Bottom spacer */}
        <div className="h-1 shrink-0" />
      </div>

      {/* Scroll-to-bottom floating button — sticky to viewport bottom of scroll area */}
      <div className="sticky bottom-0 h-0 w-full pointer-events-none">
        <ScrollToBottom
          visible={!isNearBottom}
          newMessageCount={newMessageCount}
          onClick={scrollToBottom}
        />
      </div>
    </div>
  );
}
