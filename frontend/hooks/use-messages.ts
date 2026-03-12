import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { messagesApi } from "@/lib/api";

const MESSAGE_PAGE_SIZE = 30;

export const messageKeys = {
  all: ["messages"] as const,
  list: (conversationId: string) => [...messageKeys.all, "list", conversationId] as const,
};

export function useMessages(conversationId: string | undefined) {
  return useInfiniteQuery({
    queryKey: messageKeys.list(conversationId!),
    queryFn: ({ pageParam }) =>
      messagesApi.list(conversationId!, {
        limit: MESSAGE_PAGE_SIZE,
        before: pageParam as string | undefined,
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => {
      if (!lastPage.has_more || lastPage.messages.length === 0) return undefined;
      // Use the oldest message's ID as the cursor for the next page
      return lastPage.messages[lastPage.messages.length - 1].message_id;
    },
    enabled: !!conversationId,
    // Messages are ordered newest-first from the API; we reverse in the UI
  });
}

export function useInvalidateMessages() {
  const qc = useQueryClient();
  return (conversationId: string) =>
    qc.invalidateQueries({ queryKey: messageKeys.list(conversationId) });
}
