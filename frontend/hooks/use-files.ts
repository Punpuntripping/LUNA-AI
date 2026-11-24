import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { ApiClientError, filesApi } from "@/lib/api";
import { MY_FILES_ENABLED } from "@/lib/features";
import { useAuthStore } from "@/stores/auth-store";
import type {
  FileMatchRequest,
  FilesStatusResponse,
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
  status: (userId: string | null) => [...fileKeys.all, "status", userId] as const,
  list: () => [...fileKeys.all, "list"] as const,
  detail: (itemId: string) => [...fileKeys.all, "detail", itemId] as const,
};

/**
 * Is «الملفات» on for THIS user? The build flag (`MY_FILES_ENABLED`) only says
 * the UI was shipped; the backend may still limit the feature to some users
 * (`MY_FILES_USERS`). Every user-visible part of the feature keys off this.
 *
 * - Build flag off → `enabled: false` at once, and NO request is made.
 * - Otherwise `GET /files/status` once per session per account: never stale,
 *   no refetch on focus / reconnect / mount, one retry at most.
 * - `enabled` is `true` only on an explicit `{ enabled: true }`. Loading, an
 *   error, or a signed-out visitor (the request is refused) all read `false`,
 *   so the feature never flashes on and then away, and a failed call leaves
 *   the app exactly as it was before the feature.
 * - `settled` tells a page "the answer is in" (to choose between the page and
 *   not-found without flashing either).
 */
export function useFilesStatus(): { enabled: boolean; settled: boolean; angleMaps: boolean } {
  const userId = useAuthStore((s) => s.user?.user_id ?? null);
  const query = useQuery({
    queryKey: fileKeys.status(userId),
    queryFn: () => filesApi.status(),
    enabled: MY_FILES_ENABLED,
    staleTime: Infinity,
    gcTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: false,
    retry: 1,
  });
  if (!MY_FILES_ENABLED) return { enabled: false, settled: true, angleMaps: false };
  const enabled = query.data?.enabled === true;
  return {
    enabled,
    settled: query.isSuccess || query.isError,
    // «الأوجه» (angle_map_ui.md): an explicit true only, and never without «الملفات».
    angleMaps: enabled && query.data?.angle_maps === true,
  };
}

/** `useFilesStatus().enabled` — the one switch for every visible part. */
export function useFilesEnabled(): boolean {
  return useFilesStatus().enabled;
}

/**
 * The backend can turn the feature off for a user while their tab is open (a
 * rollback, or removal from `MY_FILES_USERS`). Every `/files` call then answers
 * `FEATURE_OFF`; on the first one the cached status flips to off, so the sidebar
 * row, the page and the pane tabs leave at once instead of showing an error
 * with a retry that can never work.
 */
function useFeatureOffGuard() {
  const qc = useQueryClient();
  const userId = useAuthStore((s) => s.user?.user_id ?? null);
  return useCallback(
    async <T,>(call: () => Promise<T>): Promise<T> => {
      try {
        return await call();
      } catch (err) {
        if (err instanceof ApiClientError && err.code === "FEATURE_OFF") {
          qc.setQueryData(fileKeys.status(userId), { enabled: false });
        }
        throw err;
      }
    },
    [qc, userId],
  );
}

export function useLibraryFiles(options: { enabled?: boolean } = {}) {
  const guard = useFeatureOffGuard();
  return useQuery({
    queryKey: fileKeys.list(),
    queryFn: () => guard(() => filesApi.list()),
    enabled: options.enabled ?? true,
  });
}

/**
 * Turns «الأوجه» off in the cached status when an angle-map call answers
 * FEATURE_OFF — the «الأوجه» switch alone, never the whole of «الملفات» (which
 * `useFeatureOffGuard` would do).
 */
function useAngleMapsOffGuard() {
  const qc = useQueryClient();
  const userId = useAuthStore((s) => s.user?.user_id ?? null);
  return useCallback(
    async <T,>(call: () => Promise<T>): Promise<T> => {
      try {
        return await call();
      } catch (err) {
        if (err instanceof ApiClientError && err.code === "FEATURE_OFF") {
          qc.setQueryData<FilesStatusResponse>(fileKeys.status(userId), (prev) =>
            prev ? { ...prev, angle_maps: false } : prev,
          );
        }
        throw err;
      }
    },
    [qc, userId],
  );
}

/**
 * «الأوجه» (.claude/plans/angle_map_ui.md): the user's angle maps as light rows,
 * newest first — one conversation's when `conversationId` is given (the chat
 * pane), all of them otherwise (/files). Asked only while «الأوجه» is on for
 * this user; off or still loading reads as no maps.
 */
export function useAngleMapList(conversationId?: string | null) {
  const { angleMaps } = useFilesStatus();
  const guard = useAngleMapsOffGuard();
  return useQuery({
    queryKey: [...fileKeys.all, "angle-maps", conversationId ?? "all"] as const,
    queryFn: () => guard(() => filesApi.angleMapList(conversationId ?? undefined)),
    enabled: angleMaps,
    staleTime: 60_000,
    retry: 1,
  });
}

/** One angle map in full. */
export function useAngleMap(mapId: string | undefined) {
  const { angleMaps } = useFilesStatus();
  const guard = useAngleMapsOffGuard();
  return useQuery({
    queryKey: [...fileKeys.all, "angle-map", mapId ?? ""] as const,
    queryFn: () => guard(() => filesApi.angleMap(mapId!)),
    enabled: angleMaps && !!mapId,
    staleTime: 60_000,
    retry: 1,
  });
}

/** Several angle maps in full (the pane stacks a conversation's maps). */
export function useAngleMaps(mapIds: readonly string[]) {
  const { angleMaps } = useFilesStatus();
  const guard = useAngleMapsOffGuard();
  return useQueries({
    queries: mapIds.map((mapId) => ({
      queryKey: [...fileKeys.all, "angle-map", mapId] as const,
      queryFn: () => guard(() => filesApi.angleMap(mapId)),
      enabled: angleMaps,
      staleTime: 60_000,
      retry: 1,
    })),
  });
}

/** Everything the opened file needs; also what the pane's file viewer reads. */
export function useLibraryFile(itemId: string | undefined) {
  const guard = useFeatureOffGuard();
  return useQuery({
    queryKey: fileKeys.detail(itemId ?? ""),
    queryFn: () => guard(() => filesApi.get(itemId!)),
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
  const guard = useFeatureOffGuard();
  return useMutation({
    mutationFn: (itemId: string) => guard(() => filesApi.hide(itemId)),
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
 * Phase 1b — asked by the app the moment a file is picked (the composer and the
 * pane call it through `use-file-match-queue`).
 */
export function useFileMatch() {
  return useMutation({
    mutationFn: (body: FileMatchRequest) => filesApi.match(body),
  });
}
