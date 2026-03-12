import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { preferencesApi, templatesApi } from "@/lib/api";

export const preferencesKeys = {
  all: ["preferences"] as const,
  templates: ["templates"] as const,
};

export function usePreferences() {
  return useQuery({
    queryKey: preferencesKeys.all,
    queryFn: () => preferencesApi.get(),
  });
}

export function useUpdatePreferences() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (preferences: Record<string, unknown>) =>
      preferencesApi.update(preferences),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: preferencesKeys.all });
    },
  });
}

export function useTemplates() {
  return useQuery({
    queryKey: preferencesKeys.templates,
    queryFn: () => templatesApi.list(),
  });
}

export function useCreateTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { title: string; description?: string; prompt_template: string; agent_family?: string }) =>
      templatesApi.create(data),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: preferencesKeys.templates });
    },
  });
}

export function useUpdateTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ templateId, data }: { templateId: string; data: Record<string, unknown> }) =>
      templatesApi.update(templateId, data),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: preferencesKeys.templates });
    },
  });
}

export function useDeleteTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (templateId: string) => templatesApi.delete(templateId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: preferencesKeys.templates });
    },
  });
}
