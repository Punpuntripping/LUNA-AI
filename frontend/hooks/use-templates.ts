import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { templatesApi } from "@/lib/api";
import type {
  CreateTemplateRequest,
  TemplateListResponse,
  UpdateTemplateRequest,
} from "@/types";

export const templateKeys = {
  all: ["templates"] as const,
  list: () => [...templateKeys.all, "list"] as const,
  detail: (id: string) => [...templateKeys.all, id] as const,
};

export function useTemplates() {
  return useQuery({
    queryKey: templateKeys.list(),
    queryFn: () => templatesApi.list(),
  });
}

export function useTemplate(templateId: string | undefined) {
  return useQuery({
    queryKey: templateKeys.detail(templateId!),
    queryFn: () => templatesApi.get(templateId!),
    enabled: !!templateId,
  });
}

export function useCreateTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: CreateTemplateRequest) => templatesApi.create(data),
    onSuccess: (created) => {
      qc.setQueryData(templateKeys.detail(created.template_id), created);
      void qc.invalidateQueries({ queryKey: templateKeys.list() });
    },
  });
}

export function useUpdateTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      templateId,
      data,
    }: {
      templateId: string;
      data: UpdateTemplateRequest;
    }) => templatesApi.update(templateId, data),
    onSuccess: (updated) => {
      qc.setQueryData(templateKeys.detail(updated.template_id), updated);
      void qc.invalidateQueries({ queryKey: templateKeys.list() });
    },
  });
}

export function useDeleteTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (templateId: string) => templatesApi.delete(templateId),
    onSuccess: (_data, templateId) => {
      qc.setQueryData<TemplateListResponse>(templateKeys.list(), (prev) =>
        prev
          ? {
              templates: prev.templates.filter(
                (t) => t.template_id !== templateId,
              ),
            }
          : prev,
      );
      void qc.invalidateQueries({ queryKey: templateKeys.list() });
    },
  });
}
