import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { casesApi } from "@/lib/api";
import type { CreateCaseRequest } from "@/types";

export const caseKeys = {
  all: ["cases"] as const,
  lists: () => [...caseKeys.all, "list"] as const,
  list: (status?: string) => [...caseKeys.lists(), status] as const,
  detail: (id: string) => [...caseKeys.all, "detail", id] as const,
};

export function useCases(status?: string) {
  return useQuery({
    queryKey: caseKeys.list(status),
    queryFn: () => casesApi.list({ status }),
  });
}

export function useCaseDetail(caseId: string | undefined) {
  return useQuery({
    queryKey: caseKeys.detail(caseId!),
    queryFn: () => casesApi.get(caseId!),
    enabled: !!caseId,
  });
}

export function useCreateCase() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: CreateCaseRequest) => casesApi.create(data),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: caseKeys.lists() });
    },
  });
}

export function useUpdateCase() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ caseId, data }: { caseId: string; data: Partial<CreateCaseRequest> }) =>
      casesApi.update(caseId, data),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: caseKeys.lists() });
    },
  });
}

export function useUpdateCaseStatus() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ caseId, status }: { caseId: string; status: string }) =>
      casesApi.updateStatus(caseId, status),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: caseKeys.lists() });
    },
  });
}

export function useDeleteCase() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (caseId: string) => casesApi.delete(caseId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: caseKeys.lists() });
    },
  });
}
