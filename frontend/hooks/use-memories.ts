import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { memoriesApi } from "@/lib/api";

export const memoryKeys = {
  all: ["memories"] as const,
  lists: () => [...memoryKeys.all, "list"] as const,
  list: (caseId: string, type?: string) => [...memoryKeys.lists(), caseId, type] as const,
};

export function useMemories(caseId: string | undefined, type?: string) {
  return useQuery({
    queryKey: memoryKeys.list(caseId!, type),
    queryFn: () => memoriesApi.list(caseId!, { type }),
    enabled: !!caseId,
  });
}

export function useCreateMemory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      caseId,
      body,
    }: {
      caseId: string;
      body: { memory_type: string; content_ar: string };
    }) => memoriesApi.create(caseId, body),
    onSuccess: (_data, variables) => {
      void qc.invalidateQueries({ queryKey: memoryKeys.list(variables.caseId) });
    },
  });
}

export function useUpdateMemory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      memoryId,
      body,
    }: {
      memoryId: string;
      body: { content_ar?: string; memory_type?: string };
    }) => memoriesApi.update(memoryId, body),
    onSuccess: () => {
      // Invalidate all memory lists since we don't have caseId in the response
      void qc.invalidateQueries({ queryKey: memoryKeys.lists() });
    },
  });
}

export function useDeleteMemory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (memoryId: string) => memoriesApi.delete(memoryId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: memoryKeys.lists() });
    },
  });
}
