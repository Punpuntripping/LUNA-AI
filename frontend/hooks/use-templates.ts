import {
  keepPreviousData,
  useQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { templatesApi } from "@/lib/api";
import type {
  CreateTemplateRequest,
  TemplateListResponse,
  UpdateTemplateRequest,
} from "@/types";

/**
 * ⚠ `lists()` vs `list(q)` — see the same note in `use-my-blogs.ts`. `lists()`
 * is the invalidation prefix that covers the unfiltered listing AND every live
 * search cache; `list()` is the unfiltered entry alone, and the only one a
 * `setQueryData` splice may touch (a BM25 ranking is the server's, not ours).
 */
export const templateKeys = {
  all: ["templates"] as const,
  lists: () => [...templateKeys.all, "list"] as const,
  list: (q = "") => [...templateKeys.lists(), q] as const,
  detail: (id: string) => [...templateKeys.all, id] as const,
};

/**
 * قوالبي. ``q`` ranks the same list through ``bm25_search()`` over title +
 * ``content_md`` (bm25_navigation_search.md Wave D) instead of newest-updated
 * first; the envelope is unchanged, so `MyTemplatesGrid` keeps its own card and
 * its own markdown preview. The term must have cleared the 3-character floor —
 * `useSearchQuery` is what guarantees that.
 */
export function useTemplates(q = "") {
  const term = q.trim();
  return useQuery({
    queryKey: templateKeys.list(term),
    queryFn: () => templatesApi.list(term),
    // Keeps the grid on screen between keystrokes instead of collapsing to a
    // spinner on every new cache key.
    placeholderData: keepPreviousData,
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
      void qc.invalidateQueries({ queryKey: templateKeys.lists() });
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
      void qc.invalidateQueries({ queryKey: templateKeys.lists() });
    },
  });
}

export function useDeleteTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (templateId: string) => templatesApi.delete(templateId),
    onSuccess: (_data, templateId) => {
      // Optimistic removal on the UNFILTERED list only. A search page's
      // ordering came from BM25 and its contents from a server-side id filter,
      // so splicing a row out of it by hand would be guessing; the invalidate
      // below re-fetches those instead.
      qc.setQueryData<TemplateListResponse>(templateKeys.list(), (prev) =>
        prev
          ? {
              templates: prev.templates.filter(
                (t) => t.template_id !== templateId,
              ),
            }
          : prev,
      );
      void qc.invalidateQueries({ queryKey: templateKeys.lists() });
    },
  });
}
