import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { documentsApi } from "@/lib/api";

export const documentKeys = {
  all: ["documents"] as const,
  lists: () => [...documentKeys.all, "list"] as const,
  list: (caseId: string) => [...documentKeys.lists(), caseId] as const,
  detail: (id: string) => [...documentKeys.all, "detail", id] as const,
  download: (id: string) => [...documentKeys.all, "download", id] as const,
};

export function useDocuments(caseId: string | undefined) {
  return useQuery({
    queryKey: documentKeys.list(caseId!),
    queryFn: () => documentsApi.list(caseId!),
    enabled: !!caseId,
  });
}

export function useDocumentDetail(documentId: string | undefined) {
  return useQuery({
    queryKey: documentKeys.detail(documentId!),
    queryFn: () => documentsApi.get(documentId!),
    enabled: !!documentId,
  });
}

export function useDownloadUrl(documentId: string | undefined) {
  return useQuery({
    queryKey: documentKeys.download(documentId!),
    queryFn: () => documentsApi.download(documentId!),
    enabled: !!documentId,
    // Download URLs expire — don't cache for too long
    staleTime: 2 * 60 * 1000, // 2 minutes
  });
}

export function useUploadDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ caseId, file }: { caseId: string; file: File }) =>
      documentsApi.upload(caseId, file),
    onSuccess: (_data, variables) => {
      void qc.invalidateQueries({ queryKey: documentKeys.list(variables.caseId) });
    },
  });
}

export function useDeleteDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (documentId: string) => documentsApi.delete(documentId),
    onSuccess: () => {
      // Invalidate all document lists since we don't know the caseId here
      void qc.invalidateQueries({ queryKey: documentKeys.lists() });
    },
  });
}
