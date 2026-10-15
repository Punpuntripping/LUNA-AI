import { useInfiniteQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { AppState, type AppStateStatus } from "react-native";

import { placeholderPollMs } from "@contracts";

import { listMessages, MESSAGE_PAGE_SIZE } from "@/api/messages";

import { chatEngine } from "./chat-engine";
import { messageKeys } from "./keys";
import { streamStore } from "./stream-store";

// X6 — a conversation's messages: newest page first, older pages through the
// `before` cursor (oldest loaded id). While the newest row is an empty
// assistant placeholder (the run is answering in the background) the list
// polls at `placeholderPollMs` — 4 s live / 10 s when the server says nothing
// runs — and stops once it fills or ages out. TanStack only polls in the
// foreground (focusManager ↔ AppState in src/lib/query-client.ts); no poll
// while this app's own stream feeds the conversation.

export function useMessages(conversationId: string | undefined) {
  const query = useInfiniteQuery({
    queryKey: messageKeys.list(conversationId ?? ""),
    queryFn: ({ pageParam }) =>
      listMessages(conversationId as string, { limit: MESSAGE_PAGE_SIZE, before: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => {
      if (!lastPage.has_more || lastPage.messages.length === 0) return undefined;
      return lastPage.messages[lastPage.messages.length - 1].message_id;
    },
    enabled: !!conversationId,
    refetchInterval: (q) => {
      if (!conversationId || streamStore.isActive(conversationId)) return false;
      const first = q.state.data?.pages[0];
      return placeholderPollMs(first?.messages[0], first?.run_active, Date.now());
    },
  });

  const firstPage = query.data?.pages[0];
  useEffect(() => {
    if (conversationId && firstPage) chatEngine.noteMessagesPage(conversationId, firstPage);
  }, [conversationId, firstPage]);

  return query;
}

/** The send / stop / retry actions (stable — the engine is a singleton). */
export function useChatActions() {
  return chatEngine;
}

/**
 * Mount ONCE under the signed-in layout: when the app returns to the
 * foreground, streams that went silent while backgrounded (the OS killed the
 * socket) are aborted and recovered read-only — never re-sent.
 */
export function useChatResumeOnForeground(): void {
  useEffect(() => {
    let last: AppStateStatus = AppState.currentState;
    const sub = AppState.addEventListener("change", (next) => {
      if (last !== "active" && next === "active") chatEngine.onAppActive();
      last = next;
    });
    return () => sub.remove();
  }, []);
}
