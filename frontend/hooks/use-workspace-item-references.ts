import { useQuery } from "@tanstack/react-query";
import { workspaceApi } from "@/lib/api";
import type { WorkspaceItemReferencesResponse } from "@/lib/contracts/wi-preview";
import type { Reference } from "@/types";

/**
 * Migration 049: references for an ``agent_search`` workspace item are
 * fetched from the relational ``workspace_item_references`` table, not from
 * ``metadata.references`` JSONB. Backend reconstructs the ``Reference``
 * payload by joining to chunks_v2 / cases / services.
 *
 * ACCESS-TIERS PHASE C (§6.2 step 1) — THIS IS THE CITATION MESH ONLY.
 * The response no longer carries source bodies. It used to embed a fully-built
 * ``source_view`` per entry (full case bodies, full chunk content, uncapped
 * circulars up to 168 KB) before the user clicked anything, which is both a
 * large payload and the reason metering was structurally impossible. Now:
 *
 * - ``source_view`` is present but ALWAYS ``null`` — kept on the wire only so
 *   an un-migrated client degrades to "no reveal button" instead of crashing.
 * - ``has_source`` (new) says whether a body CAN be built for that ``n``.
 *   Branch on it; it costs no request to learn.
 * - The body itself comes from ``useReferenceSource`` (hooks/use-reference-source),
 *   one item at a time, on the click, after ``resolve_access``.
 *
 * This endpoint stays FREE and unmetered: citation lists are in the never-gated
 * class (§1.3). Only the body moved.
 *
 * WI PREVIEW LOCK (wi_preview_lock.md §9.3) — the one exception: on a free
 * user's preview-locked item the server returns only the sources the VISIBLE
 * part cites, plus ``hidden_count`` for the rest. ``useWorkspaceItemReferences``
 * still yields the plain list (every caller unchanged);
 * ``useWorkspaceItemReferencesHiddenCount`` reads the count off the SAME cached
 * response — one request, two selectors.
 *
 * Refs for a given WI are immutable once the agent has published the item
 * (the publisher writes them, the user never edits them), so we set
 * ``staleTime: Infinity`` and skip refetching on focus / mount. Invalidate
 * the cache key on the rare write-side (e.g. future "manually add ref" flow).
 * The one thing that DOES change the answer is the preview lock flipping (an
 * upgrade unlocks the item on its next read), which is why ``locked`` is part
 * of the key: the full list is a different cache entry from the filtered one.
 */
export const workspaceItemReferenceKeys = {
  all: ["workspace-item-references"] as const,
  byItem: (itemId: string, usedOnly?: boolean, locked?: boolean) =>
    [
      ...workspaceItemReferenceKeys.all,
      itemId,
      usedOnly ?? false,
      locked ?? false,
    ] as const,
};

export interface WorkspaceItemReferencesOptions {
  usedOnly?: boolean;
  enabled?: boolean;
  /**
   * The item's preview-lock state (``isWiPreviewLocked(item)``). Keys the
   * cache so a lock flip refetches instead of reusing the other state's list.
   * Omit (= false) wherever the item can never be locked.
   */
  locked?: boolean;
}

function referencesQueryOptions(
  itemId: string | undefined,
  opts?: WorkspaceItemReferencesOptions,
) {
  const usedOnly = opts?.usedOnly ?? false;
  return {
    queryKey: workspaceItemReferenceKeys.byItem(
      itemId ?? "",
      usedOnly,
      opts?.locked ?? false,
    ),
    queryFn: () => workspaceApi.listReferences(itemId!, { usedOnly }),
    enabled: (opts?.enabled ?? true) && !!itemId,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnMount: false,
  } as const;
}

export function useWorkspaceItemReferences(
  itemId: string | undefined,
  opts?: WorkspaceItemReferencesOptions,
) {
  return useQuery<WorkspaceItemReferencesResponse, Error, Reference[]>({
    ...referencesQueryOptions(itemId, opts),
    select: (data) => data.references,
  });
}

/**
 * How many of the item's sources sit in the hidden part of a preview-locked
 * item (``hidden_count``); ``0`` for an open item, while loading, or when the
 * server omits the field. Shares the query (and the request) with
 * ``useWorkspaceItemReferences`` — pass the same ``itemId`` + options.
 */
export function useWorkspaceItemReferencesHiddenCount(
  itemId: string | undefined,
  opts?: WorkspaceItemReferencesOptions,
): number {
  const { data } = useQuery<WorkspaceItemReferencesResponse, Error, number>({
    ...referencesQueryOptions(itemId, opts),
    select: (response) => response.hidden_count ?? 0,
  });
  return data ?? 0;
}
