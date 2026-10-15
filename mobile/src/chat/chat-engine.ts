import type { QueryClient } from "@tanstack/react-query";

import { listMessages, MESSAGE_PAGE_SIZE } from "@/api/messages";
import { openMessageStream } from "@/api/stream";
import { listConversationWorkspace } from "@/api/workspace";
import { queryClient } from "@/lib/query-client";

import { conversationKeys, messageKeys, workspaceKeys } from "./keys";
import type { ChatCacheAdapter, MessagesData } from "./message-cache";
import { createChatEngine, type ChatEngine } from "./run-send";
import { streamStore } from "./stream-store";

// Production wiring of the pure send engine: TanStack cache + expo/fetch
// stream + the app's stream store. One engine for the whole app (sends in
// different conversations run side by side; it keys everything per id).

export function createQueryCacheAdapter(qc: QueryClient): ChatCacheAdapter {
  return {
    getMessages: (id) => qc.getQueryData<MessagesData>(messageKeys.list(id)),
    setMessages: (id, updater) => {
      qc.setQueryData<MessagesData>(messageKeys.list(id), (old) => updater(old));
    },
    cancelMessages: (id) => qc.cancelQueries({ queryKey: messageKeys.list(id) }),
    invalidateMessages: (id) => {
      void qc.invalidateQueries({ queryKey: messageKeys.list(id) });
    },
    invalidateConversationLists: () => {
      void qc.invalidateQueries({ queryKey: conversationKeys.lists() });
    },
    invalidateWorkspace: (id) => {
      void qc.invalidateQueries({ queryKey: workspaceKeys.byConversation(id) });
    },
    fetchLatestMessages: (id) => listMessages(id, { limit: MESSAGE_PAGE_SIZE }),
    lookupWorkspaceItems: async (id, itemIds) => {
      const res = await qc.fetchQuery({
        queryKey: workspaceKeys.byConversation(id),
        queryFn: () => listConversationWorkspace(id),
        staleTime: 0,
      });
      const wanted = new Set(itemIds);
      return res.items
        .filter((item) => wanted.has(item.item_id))
        .map((item) => ({ item_id: item.item_id, kind: item.kind ?? null, title: item.title ?? "" }));
    },
  };
}

export const chatEngine: ChatEngine = createChatEngine({
  openStream: openMessageStream,
  cache: createQueryCacheAdapter(queryClient),
  store: streamStore,
});
