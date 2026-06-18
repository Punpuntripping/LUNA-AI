import {
  useQuery,
  useMutation,
  useQueryClient,
  useInfiniteQuery,
} from "@tanstack/react-query";
import { conversationsApi } from "@/lib/api";
import type { CreateConversationRequest, ConversationListResponse } from "@/types";

/** Page size for the /chats infinite-scroll list + search results. */
export const CHATS_PAGE_SIZE = 30;

export const conversationKeys = {
  all: ["conversations"] as const,
  lists: () => [...conversationKeys.all, "list"] as const,
  list: (caseId: string | null) => [...conversationKeys.lists(), caseId] as const,
  detail: (id: string) => [...conversationKeys.all, "detail", id] as const,
  /** Infinite, offset-paginated index for the /chats page. */
  index: (starred: boolean) =>
    [...conversationKeys.all, "index", starred] as const,
  /** Search results keyed on the query + starred filter. */
  search: (q: string, starred: boolean) =>
    [...conversationKeys.all, "search", q, starred] as const,
};

export function useConversations(caseId?: string | null) {
  return useQuery({
    queryKey: conversationKeys.list(caseId ?? null),
    queryFn: () => conversationsApi.list({ case_id: caseId ?? null, limit: 50 }),
  });
}

/**
 * Offset-paginated, infinite-scroll conversation index for the /chats page.
 * Server orders starred-first then most-recent. ``starred`` restricts to the
 * starred subset (used by the "المميّزة" filter when no search query is set).
 */
export function useConversationsIndex(opts: { starred: boolean }) {
  const { starred } = opts;
  return useInfiniteQuery({
    queryKey: conversationKeys.index(starred),
    queryFn: ({ pageParam }) =>
      conversationsApi.list({
        limit: CHATS_PAGE_SIZE,
        offset: pageParam as number,
        starred: starred || undefined,
      }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) => {
      if (!lastPage.has_more) return undefined;
      return allPages.length * CHATS_PAGE_SIZE;
    },
  });
}

/**
 * Search conversations by title OR message content (server-scoped to the user).
 * Offset-paginated infinite scroll, 30/page. ``opts.starred`` restricts the
 * search to starred conversations. Disabled until there is a non-empty query
 * (or, when starred-only, lets the index hook handle the no-query case — this
 * query stays disabled with an empty ``q``).
 */
export function useSearchConversations(q: string, opts: { starred: boolean }) {
  const trimmed = q.trim();
  const { starred } = opts;
  return useInfiniteQuery({
    queryKey: conversationKeys.search(trimmed, starred),
    queryFn: ({ pageParam }) =>
      conversationsApi.list({
        q: trimmed,
        starred: starred || undefined,
        limit: CHATS_PAGE_SIZE,
        offset: pageParam as number,
      }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) => {
      if (!lastPage.has_more) return undefined;
      return allPages.length * CHATS_PAGE_SIZE;
    },
    enabled: trimmed.length > 0,
  });
}

export function useConversationDetail(conversationId: string | undefined) {
  return useQuery({
    queryKey: conversationKeys.detail(conversationId!),
    queryFn: () => conversationsApi.get(conversationId!),
    enabled: !!conversationId,
  });
}

export function useCreateConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: CreateConversationRequest) => conversationsApi.create(data),
    onMutate: async (data) => {
      await qc.cancelQueries({ queryKey: conversationKeys.lists() });
      const previousLists = qc.getQueriesData<ConversationListResponse>({
        queryKey: conversationKeys.lists(),
      });

      const optimistic = {
        conversation_id: `optimistic-${Date.now()}`,
        case_id: data.case_id ?? null,
        title_ar: "محادثة جديدة",
        message_count: 0,
        is_active: true,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        is_starred: false,
        starred_at: null,
      };

      qc.setQueriesData<ConversationListResponse>(
        { queryKey: conversationKeys.lists() },
        (old) => {
          if (!old) return old;
          return {
            ...old,
            conversations: [optimistic, ...old.conversations],
            total: old.total + 1,
          };
        }
      );

      return { previousLists };
    },
    onError: (_err, _data, context) => {
      if (context?.previousLists) {
        for (const [key, data] of context.previousLists) {
          qc.setQueryData(key, data);
        }
      }
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: conversationKeys.lists() });
    },
  });
}

export function useDeleteConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => conversationsApi.delete(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: conversationKeys.lists() });
    },
  });
}

export function useRenameConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, title_ar }: { id: string; title_ar: string }) =>
      conversationsApi.update(id, { title_ar }),
    onMutate: async ({ id, title_ar }) => {
      await qc.cancelQueries({ queryKey: conversationKeys.lists() });
      const previousLists = qc.getQueriesData<ConversationListResponse>({
        queryKey: conversationKeys.lists(),
      });

      qc.setQueriesData<ConversationListResponse>(
        { queryKey: conversationKeys.lists() },
        (old) => {
          if (!old) return old;
          return {
            ...old,
            conversations: old.conversations.map((conv) =>
              conv.conversation_id === id
                ? { ...conv, title_ar, updated_at: new Date().toISOString() }
                : conv
            ),
          };
        }
      );

      return { previousLists };
    },
    onError: (_err, _data, context) => {
      if (context?.previousLists) {
        for (const [key, data] of context.previousLists) {
          qc.setQueryData(key, data);
        }
      }
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: conversationKeys.lists() });
    },
  });
}

/** Compare for starred-first then most-recent ordering (mirrors backend sort). */
function compareStarredFirst(
  a: ConversationListResponse["conversations"][number],
  b: ConversationListResponse["conversations"][number],
): number {
  // Starred float to the top, ordered by starred_at DESC.
  if (a.is_starred && b.is_starred) {
    return (b.starred_at ?? "").localeCompare(a.starred_at ?? "");
  }
  if (a.is_starred) return -1;
  if (b.is_starred) return 1;
  // Both unstarred → most-recent first.
  return (b.updated_at ?? "").localeCompare(a.updated_at ?? "");
}

/**
 * Star / unstar a conversation. Optimistically flips ``is_starred`` + sets/clears
 * ``starred_at`` across every cached conversation list (sidebar + /chats index +
 * search) and re-sorts starred-first, rolls back on error, and invalidates the
 * lists on settle so the server's authoritative ordering wins.
 */
export function useStarConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, starred }: { id: string; starred: boolean }) =>
      conversationsApi.update(id, { starred }),
    onMutate: async ({ id, starred }) => {
      await qc.cancelQueries({ queryKey: conversationKeys.all });

      // Flat list caches (sidebar via useConversations).
      const previousLists = qc.getQueriesData<ConversationListResponse>({
        queryKey: conversationKeys.lists(),
      });
      qc.setQueriesData<ConversationListResponse>(
        { queryKey: conversationKeys.lists() },
        (old) => {
          if (!old) return old;
          const conversations = old.conversations
            .map((conv) =>
              conv.conversation_id === id
                ? {
                    ...conv,
                    is_starred: starred,
                    starred_at: starred ? new Date().toISOString() : null,
                  }
                : conv,
            )
            .sort(compareStarredFirst);
          return { ...old, conversations };
        },
      );

      // Infinite caches (the /chats index + search results).
      const previousInfinite = qc.getQueriesData<{
        pages: ConversationListResponse[];
        pageParams: unknown[];
      }>({ queryKey: conversationKeys.all });
      const infiniteToPatch = previousInfinite.filter(
        ([, value]) => value && Array.isArray(value.pages),
      );
      for (const [key, value] of infiniteToPatch) {
        if (!value) continue;
        const pages = value.pages.map((page) => ({
          ...page,
          conversations: page.conversations.map((conv) =>
            conv.conversation_id === id
              ? {
                  ...conv,
                  is_starred: starred,
                  starred_at: starred ? new Date().toISOString() : null,
                }
              : conv,
          ),
        }));
        qc.setQueryData(key, { ...value, pages });
      }

      return { previousLists, previousInfinite: infiniteToPatch };
    },
    onError: (_err, _data, context) => {
      if (context?.previousLists) {
        for (const [key, data] of context.previousLists) {
          qc.setQueryData(key, data);
        }
      }
      if (context?.previousInfinite) {
        for (const [key, data] of context.previousInfinite) {
          qc.setQueryData(key, data);
        }
      }
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: conversationKeys.all });
    },
  });
}

export function useEndSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (conversationId: string) => conversationsApi.endSession(conversationId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: conversationKeys.lists() });
    },
  });
}
