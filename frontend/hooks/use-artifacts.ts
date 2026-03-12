import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { artifactsApi } from "@/lib/api";

export const artifactKeys = {
  all: ["artifacts"] as const,
  byConversation: (id: string) => [...artifactKeys.all, "conversation", id] as const,
  byCase: (id: string) => [...artifactKeys.all, "case", id] as const,
  detail: (id: string) => [...artifactKeys.all, id] as const,
};

export function useConversationArtifacts(conversationId: string | undefined) {
  return useQuery({
    queryKey: artifactKeys.byConversation(conversationId!),
    queryFn: () => artifactsApi.listByConversation(conversationId!),
    enabled: !!conversationId,
  });
}

export function useCaseArtifacts(caseId: string | undefined) {
  return useQuery({
    queryKey: artifactKeys.byCase(caseId!),
    queryFn: () => artifactsApi.listByCase(caseId!),
    enabled: !!caseId,
  });
}

export function useArtifact(artifactId: string | undefined) {
  return useQuery({
    queryKey: artifactKeys.detail(artifactId!),
    queryFn: () => artifactsApi.get(artifactId!),
    enabled: !!artifactId,
  });
}

export function useUpdateArtifact() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ artifactId, data }: { artifactId: string; data: { title?: string; content_md?: string } }) =>
      artifactsApi.update(artifactId, data),
    onSuccess: (updated) => {
      qc.setQueryData(artifactKeys.detail(updated.artifact_id), updated);
      void qc.invalidateQueries({ queryKey: artifactKeys.all });
    },
  });
}

export function useDeleteArtifact() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (artifactId: string) => artifactsApi.delete(artifactId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: artifactKeys.all });
    },
  });
}
