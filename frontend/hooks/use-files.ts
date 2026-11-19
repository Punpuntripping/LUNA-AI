import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { filesApi } from "@/lib/api";
import type {
  FileMatchRequest,
  LibraryFileListResponse,
} from "@/lib/contracts";

/**
 * «الملفات» server state (.claude/plans/my_files.md §5 / §11).
 *
 * The list is one response (≤ 500 light rows); type filtering and search run
 * on the client over it, so there is exactly one list query per user.
 */
export const fileKeys = {
  all: ["files"] as const,
  list: () => [...fileKeys.all, "list"] as const,
  detail: (itemId: string) => [...fileKeys.all, "detail", itemId] as const,
};

export function useLibraryFiles(options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: fileKeys.list(),
    queryFn: () => filesApi.list(),
    enabled: options.enabled ?? true,
  });
}

/** Everything the opened file needs; also what the pane's file viewer reads. */
export function useLibraryFile(itemId: string | undefined) {
  return useQuery({
    queryKey: fileKeys.detail(itemId ?? ""),
    queryFn: () => filesApi.get(itemId!),
    enabled: !!itemId,
  });
}

/**
 * «حذف من الملفات». The card leaves the list at once (optimistic) and comes
 * back if the request fails; the list is refetched either way. The detail
 * cache is dropped — the conversation that used the file keeps it, and the
 * pane refetches it on its next open.
 */
export function useHideLibraryFile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (itemId: string) => filesApi.hide(itemId),
    onMutate: async (itemId) => {
      await qc.cancelQueries({ queryKey: fileKeys.list() });
      const prev = qc.getQueryData<LibraryFileListResponse>(fileKeys.list());
      if (prev) {
        const files = prev.files.filter((f) => f.item_id !== itemId);
        qc.setQueryData<LibraryFileListResponse>(fileKeys.list(), {
          ...prev,
          files,
          total: Math.max(0, prev.total - (prev.files.length - files.length)),
        });
      }
      return { prev };
    },
    onError: (_err, _itemId, ctx) => {
      if (ctx?.prev) qc.setQueryData(fileKeys.list(), ctx.prev);
    },
    onSuccess: (_data, itemId) => {
      qc.removeQueries({ queryKey: fileKeys.detail(itemId) });
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: fileKeys.list() });
    },
  });
}

/**
 * Phase 1b — asked by the app the moment a file is picked, before the upload
 * session opens. Not wired into the composer in this pass.
 */
export function useFileMatch() {
  return useMutation({
    mutationFn: (body: FileMatchRequest) => filesApi.match(body),
  });
}
