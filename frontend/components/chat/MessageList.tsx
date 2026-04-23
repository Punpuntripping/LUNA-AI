"use client";

import { useEffect, useRef, useCallback, useMemo, useState } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { useMessages } from "@/hooks/use-messages";
import { useChatStore } from "@/stores/chat-store";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { TypingIndicator } from "@/components/chat/TypingIndicator";
import { ScrollToBottom } from "@/components/chat/ScrollToBottom";
import type { Message } from "@/types";

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

  const isStreaming = useChatStore((s) => s.isStreaming);
  const streamingMessageId = useChatStore((s) => s.streamingMessageId);
  const streamingContent = useChatStore((s) => s.streamingContent);

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

  // Flatten pages into a single array, reversing since API returns newest-first
  const messages: Message[] = useMemo(() => {
    if (!data?.pages) return [];
    const all: Message[] = [];
    // Pages are in order [newest, older, oldest...]
    // We need oldest-first for display, so reverse pages then reverse messages within
    for (let i = data.pages.length - 1; i >= 0; i--) {
      const page = data.pages[i];
      // Messages within a page are newest-first, so reverse them
      all.push(...[...page.messages].reverse());
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
          const isStreamingThis =
            msg.isStreaming ||
            (streamingMessageId !== null && msg.message_id === streamingMessageId);
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
            />
          );
        })}

        {/* Typing indicator: streaming started but no content yet */}
        {isStreaming && streamingContent === "" && (
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
