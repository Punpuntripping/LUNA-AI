// TanStack Query keys for the chat. Same shapes as the web
// (frontend/hooks/use-conversations.ts, use-messages.ts, use-workspace.ts),
// except the conversation index + search live UNDER `lists()` so one
// invalidation refreshes every list after a send / rename / delete.

export const conversationKeys = {
  all: ["conversations"] as const,
  lists: () => [...conversationKeys.all, "list"] as const,
  /** Infinite, offset-paginated index (`starred` = «المميّزة» filter). */
  index: (starred: boolean) => [...conversationKeys.lists(), "index", starred] as const,
  /** Infinite search results for a trimmed, non-empty `q`. */
  search: (q: string, starred: boolean) => [...conversationKeys.lists(), "search", q, starred] as const,
  detail: (id: string) => [...conversationKeys.all, "detail", id] as const,
};

export const messageKeys = {
  all: ["messages"] as const,
  list: (conversationId: string) => [...messageKeys.all, "list", conversationId] as const,
};

export const workspaceKeys = {
  all: ["workspace"] as const,
  byConversation: (conversationId: string) => [...workspaceKeys.all, "conversation", conversationId] as const,
  detail: (itemId: string) => [...workspaceKeys.all, "detail", itemId] as const,
  references: (itemId: string, usedOnly: boolean) => [...workspaceKeys.all, "references", itemId, usedOnly] as const,
};
