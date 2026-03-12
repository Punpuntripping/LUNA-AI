"use client";

import { useEffect, useRef, useCallback, useMemo } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { useMessages } from "@/hooks/use-messages";
import { useChatStore } from "@/stores/chat-store";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { TypingIndicator } from "@/components/chat/TypingIndicator";
import type { Message } from "@/types";

interface MessageListProps {
  conversationId: string;
  className?: string;
}

export function MessageList({ conversationId, className }: MessageListProps) {
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

  // Auto-scroll to bottom when new messages arrive or streaming updates.
  // Uses direct scrollTop manipulation instead of scrollIntoView to avoid
  // layout shifts caused by scrollIntoView propagating to parent scrollable
  // ancestors (especially inside Radix ScrollArea or flex layouts).
  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return;

    const currentCount = messages.length;
    const isNewMessage = currentCount > prevMessageCountRef.current;
    prevMessageCountRef.current = currentCount;

    // On initial load or during streaming, scroll instantly (no animation).
    // On subsequent new messages, use smooth scroll.
    if (isInitialLoadRef.current && currentCount > 0) {
      isInitialLoadRef.current = false;
      container.scrollTop = container.scrollHeight;
    } else if (isStreaming) {
      container.scrollTop = container.scrollHeight;
    } else if (isNewMessage) {
      container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
    }
  }, [messages.length, isStreaming, streamingContent]);

  // Reset initial load flag when conversation changes
  useEffect(() => {
    isInitialLoadRef.current = true;
    prevMessageCountRef.current = 0;
  }, [conversationId]);

  // Intersection observer for infinite scroll (load older messages)
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
      className={cn("flex-1 overflow-y-auto", className)}
    >
      <div className="flex flex-col p-4 min-h-full">
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
    </div>
  );
}
