import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { conversationsApi } from "@/lib/api";
import type { CreateConversationRequest } from "@/types";

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
    onSuccess: () => {
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
    onSuccess: () => {
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
