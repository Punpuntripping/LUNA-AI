import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { conversationsApi } from "@/lib/api";
import type { CreateConversationRequest, ConversationListResponse } from "@/types";

export const conversationKeys = {
  all: ["conversations"] as const,
  lists: () => [...conversationKeys.all, "list"] as const,
  list: (caseId: string | null) => [...conversationKeys.lists(), caseId] as const,
  detail: (id: string) => [...conversationKeys.all, "detail", id] as const,
};

export function useConversations(caseId?: string | null) {
  return useQuery({
    queryKey: conversationKeys.list(caseId ?? null),
    queryFn: () => conversationsApi.list({ case_id: caseId ?? null, limit: 50 }),
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
      conversationsApi.update(id, title_ar),
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

export function useEndSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (conversationId: string) => conversationsApi.endSession(conversationId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: conversationKeys.lists() });
    },
  });
}
