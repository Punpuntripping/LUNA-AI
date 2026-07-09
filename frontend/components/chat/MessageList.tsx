"use client";

import { memo, useEffect, useRef, useCallback, useMemo, useState } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { useMessages, PLACEHOLDER_MAX_AGE_MS } from "@/hooks/use-messages";
import { useConversationWorkspace } from "@/hooks/use-workspace";
import { useChatStore } from "@/stores/chat-store";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { TypingIndicator } from "@/components/chat/TypingIndicator";
import { FailedResponseBubble } from "@/components/chat/FailedResponseBubble";
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
  // Deliberately NOT the content string: the list must not re-render per
  // reveal frame. StreamingMessageRow (bottom of file) is the only content
  // subscriber; the list only needs the empty→non-empty flip to swap the
  // typing indicator for the live bubble.
  const hasStreamContent = useChatStore((s) => s.streamingContent.length > 0);
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
  // The id itself is resolved per message in the render loop (a value-stable
  // string); this navigate callback is identity-stable, so memo(MessageBubble)
  // and the memoized MarkdownRenderer under it never re-render — the old
  // per-render closure here defeated both memos on every stream token.
  const handleCitationNavigate = useCallback(
    (artifactId: string, n: number) => {
      openWorkspaceItemAtReference(conversationId, artifactId, n);
    },
    [openWorkspaceItemAtReference, conversationId],
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

    // Streaming transitions (indicator mounts, first token lands): pin to
    // bottom if the user is there. Per-frame growth is followed by the
    // imperative store subscription below, not by this effect.
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
  }, [messages.length, isStreaming, hasStreamContent]);

  // Pin-to-bottom while text streams in. Reveal frames update streamingContent
  // up to once per animation frame; subscribing imperatively lets us follow
  // the growth with a bare scrollTop write — zero React re-renders involved.
  useEffect(() => {
    if (!isStreaming) return;
    return useChatStore.subscribe((state, prev) => {
      if (state.streamingContent === prev.streamingContent) return;
      if (!isNearBottomRef.current) return;
      const container = scrollContainerRef.current;
      if (container) container.scrollTop = container.scrollHeight;
    });
  }, [isStreaming]);

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
        {messages.map((msg, idx) => {
          // `isStreaming` is already scoped to this conversation, so the
          // global stream is only ever applied to its own conversation.
          const isStreamingThis =
            isStreaming &&
            (msg.isStreaming ||
              (streamingMessageId !== null &&
                msg.message_id === streamingMessageId));
          // Layer 1: never render an empty assistant placeholder as a finished
          // card. Show the thinking state until content lands — independent of
          // `isStreaming`, so it survives a dropped stream, reconnect, or a
          // page refresh while the run is still in flight.
          const isEmptyContent = isStreamingThis
            ? !hasStreamContent
            : (msg.content ?? "").trim() === "";
          if (
            msg.role === "assistant" &&
            !msg.isOptimistic &&
            isEmptyContent &&
            !(msg.artifact_ids && msg.artifact_ids.length > 0)
          ) {
            // The run behind this placeholder is dead once it's older than the
            // background-recovery window (the useMessages poll has given up)
            // AND no stream is feeding it here — e.g. the request died on
            // logout / a server restart. Convert the perpetual "ريحان يحلّل"
            // spinner into a failed bubble with a retry so it never spins
            // forever. An actively-streaming run (isStreamingThis) is never
            // failed, no matter how long it's been silent.
            const ageMs = Date.now() - new Date(msg.created_at).getTime();
            const isDead =
              !isStreamingThis && ageMs >= PLACEHOLDER_MAX_AGE_MS;
            if (isDead) {
              // A dead placeholder superseded by a newer turn (e.g. after a
              // retry) is just noise — drop it so only a still-relevant
              // failure shows.
              if (idx !== messages.length - 1) return null;
              return (
                <FailedResponseBubble
                  key={msg.message_id}
                  createdAt={msg.created_at}
                  // Retry = regenerate: re-run the user message that preceded
                  // this dead placeholder.
                  onRetry={
                    onRegenerate
                      ? () => onRegenerate(msg.message_id)
                      : undefined
                  }
                />
              );
            }
            return (
              <div key={msg.message_id} className="flex justify-end mb-4">
                <TypingIndicator />
              </div>
            );
          }
          // Hot-path isolation: the row bound to the live stream subscribes
          // to the streaming buffer itself, so each reveal frame re-renders
          // it alone while every settled bubble above stays untouched.
          if (isStreamingThis) {
            return <StreamingMessageRow key={msg.message_id} message={msg} />;
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
              message={msg}
              onRegenerate={onRegenerate}
              onEditResend={onEditResend}
              onRetry={onRetry}
              artifactIds={ids}
              artifactLookup={artifactLookup}
              onOpenArtifact={handleOpenArtifact}
              citationArtifactId={ids?.find(
                (id) => artifactLookup[id]?.kind === "agent_search",
              )}
              onCitationNavigate={handleCitationNavigate}
              referencedItemIds={referencedIds}
              onJumpToReferencedItem={handleJumpToReferencedItem}
              templateOffer={templateOffersByMessage[msg.message_id]}
            />
          );
        })}

        {/* Typing indicator: streaming started but no content yet — unless an
            empty placeholder row is already showing its own (Layer 1). */}
        {isStreaming && !hasStreamContent && !hasIncompletePlaceholder && (
          <div className="flex justify-end mb-4">
            <TypingIndicator />
          </div>
        )}

        {/* Streaming assistant bubble (when message_id hasn't been added to the query cache yet) */}
        {isStreaming &&
          streamingMessageId &&
          hasStreamContent &&
          !messages.some((m) => m.message_id === streamingMessageId) && (
            <StreamingMessageRow
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

/**
 * The sole subscriber of the per-frame streaming buffer. Isolating the
 * ``streamingContent`` read here means each reveal frame re-renders only this
 * one row — MessageList and every settled bubble above it stay untouched.
 */
const StreamingMessageRow = memo(function StreamingMessageRow({
  message,
}: {
  message: Message;
}) {
  const streamingContent = useChatStore((s) => s.streamingContent);
  const streamingMessage = useMemo(
    () => (message.isStreaming ? message : { ...message, isStreaming: true }),
    [message],
  );
  return (
    <MessageBubble
      message={streamingMessage}
      streamingContent={streamingContent}
    />
  );
});
