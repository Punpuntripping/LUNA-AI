import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { workspaceApi } from "@/lib/api";
import type {
  AttachFromDocumentRequest,
  CreateNoteRequest,
  CreateReferenceRequest,
  UpdateVisibilityRequest,
  UpdateWorkspaceItemRequest,
} from "@/types";

export const workspaceKeys = {
  all: ["workspace"] as const,
  byConversation: (id: string) =>
    [...workspaceKeys.all, "conversation", id] as const,
  byCase: (id: string) => [...workspaceKeys.all, "case", id] as const,
  detail: (id: string) => [...workspaceKeys.all, id] as const,
  fileUrl: (id: string) => [...workspaceKeys.all, id, "file"] as const,
};

export function useConversationWorkspace(conversationId: string | undefined) {
  return useQuery({
    queryKey: workspaceKeys.byConversation(conversationId!),
    queryFn: () => workspaceApi.listByConversation(conversationId!),
    enabled: !!conversationId,
  });
}

export function useCaseWorkspace(caseId: string | undefined) {
  return useQuery({
    queryKey: workspaceKeys.byCase(caseId!),
    queryFn: () => workspaceApi.listByCase(caseId!),
    enabled: !!caseId,
  });
}

export function useWorkspaceItem(itemId: string | undefined) {
  return useQuery({
    queryKey: workspaceKeys.detail(itemId!),
    queryFn: () => workspaceApi.get(itemId!),
    enabled: !!itemId,
  });
}

export function useWorkspaceItemFileUrl(
  itemId: string | undefined,
  enabled = true,
) {
  return useQuery({
    queryKey: workspaceKeys.fileUrl(itemId!),
    queryFn: () => workspaceApi.fileUrl(itemId!),
    enabled: !!itemId && enabled,
    staleTime: 50 * 60 * 1000,
  });
}

export function useUpdateWorkspaceItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      itemId,
      data,
    }: {
      itemId: string;
      data: UpdateWorkspaceItemRequest;
    }) => workspaceApi.update(itemId, data),
    onSuccess: (updated) => {
      qc.setQueryData(workspaceKeys.detail(updated.item_id), updated);
      void qc.invalidateQueries({ queryKey: workspaceKeys.all });
    },
  });
}

export function useDeleteWorkspaceItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (itemId: string) => workspaceApi.delete(itemId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: workspaceKeys.all });
    },
  });
}

export function useToggleWorkspaceVisibility() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      itemId,
      data,
    }: {
      itemId: string;
      data: UpdateVisibilityRequest;
    }) => workspaceApi.setVisibility(itemId, data),
    onSuccess: (updated) => {
      qc.setQueryData(workspaceKeys.detail(updated.item_id), updated);
      void qc.invalidateQueries({ queryKey: workspaceKeys.all });
    },
  });
}

export function useCreateNote(conversationId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: CreateNoteRequest) =>
      workspaceApi.createNote(conversationId!, data),
    onSuccess: () => {
      if (conversationId) {
        void qc.invalidateQueries({
          queryKey: workspaceKeys.byConversation(conversationId),
        });
      }
    },
  });
}

export function useCreateReference(conversationId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: CreateReferenceRequest) =>
      workspaceApi.createReference(conversationId!, data),
    onSuccess: () => {
      if (conversationId) {
        void qc.invalidateQueries({
          queryKey: workspaceKeys.byConversation(conversationId),
        });
      }
    },
  });
}

export function useUploadAttachment(conversationId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) =>
      workspaceApi.uploadAttachment(conversationId!, file),
    onSuccess: () => {
      if (conversationId) {
        void qc.invalidateQueries({
          queryKey: workspaceKeys.byConversation(conversationId),
        });
      }
    },
  });
}

export function useAttachFromDocument(conversationId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: AttachFromDocumentRequest) =>
      workspaceApi.attachFromDocument(conversationId!, data),
    onSuccess: () => {
      if (conversationId) {
        void qc.invalidateQueries({
          queryKey: workspaceKeys.byConversation(conversationId),
        });
      }
    },
  });
}
