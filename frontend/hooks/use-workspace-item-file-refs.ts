import { useQuery } from "@tanstack/react-query";
import { ApiClientError, workspaceApi } from "@/lib/api";
import {
  orderFileRefs,
  type FileRefRow,
  type FileRefSectionResponse,
  type WorkspaceItemFileRefsResponse,
} from "@/lib/contracts";

/**
 * «من مستنداتك» (``.claude/plans/aggregator_file_refs.md`` §4.6): the rows of
 * ``workspace_item_file_refs`` for one ``agent_search`` report — what its
 * ``[F1·S7]`` / ``[F1]`` tags point at. Shaped like
 * ``use-workspace-item-references``: written once at publish, never edited, so
 * ``staleTime: Infinity`` and no refetch on focus / mount.
 *
 * Never gated: it is the user's own file. A 404 (the item is not the caller's,
 * or a backend without the route) is an answer, not a failure — no retries.
 */
export const workspaceItemFileRefKeys = {
  all: ["workspace-item-file-refs"] as const,
  byItem: (itemId: string) => [...workspaceItemFileRefKeys.all, itemId] as const,
  section: (itemId: string, ref: string) =>
    [...workspaceItemFileRefKeys.all, itemId, "section", ref] as const,
};

function noRetryOn(statuses: number[]) {
  return (failureCount: number, error: unknown) =>
    !(error instanceof ApiClientError && statuses.includes(error.status)) &&
    failureCount < 2;
}

/** The report's file refs in ``position`` order; ``[]`` while loading or on error. */
export function useWorkspaceItemFileRefs(itemId: string | undefined) {
  return useQuery<WorkspaceItemFileRefsResponse, Error, FileRefRow[]>({
    queryKey: workspaceItemFileRefKeys.byItem(itemId ?? ""),
    queryFn: () => workspaceApi.listFileRefs(itemId!),
    enabled: !!itemId,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnMount: false,
    retry: noRetryOn([403, 404]),
    select: (data) => orderFileRefs(data.file_refs ?? []),
  });
}

/**
 * One cited section's text, fetched when the popup opens (``ref`` set). A 410
 * («لم يعد هذا الملف متاحًا») and a 404 are final — no retries.
 */
export function useFileRefSection(itemId: string | undefined, ref: string | null) {
  return useQuery<FileRefSectionResponse, Error>({
    queryKey: workspaceItemFileRefKeys.section(itemId ?? "", ref ?? ""),
    queryFn: () => workspaceApi.getFileRefSection(itemId!, ref!),
    enabled: !!itemId && !!ref,
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    retry: noRetryOn([403, 404, 410]),
  });
}

/** True when the section route answered 410 — the attachment was deleted on purpose. */
export function isFileRefGone(error: unknown): boolean {
  return error instanceof ApiClientError && error.status === 410;
}
