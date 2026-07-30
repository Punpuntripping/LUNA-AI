import { useQuery } from "@tanstack/react-query";
import { workspaceApi } from "@/lib/api";
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
 * Refs for a given WI are immutable once the agent has published the item
 * (the publisher writes them, the user never edits them), so we set
 * ``staleTime: Infinity`` and skip refetching on focus / mount. Invalidate
 * the cache key on the rare write-side (e.g. future "manually add ref" flow).
 */
export const workspaceItemReferenceKeys = {
  all: ["workspace-item-references"] as const,
  byItem: (itemId: string, usedOnly?: boolean) =>
    [...workspaceItemReferenceKeys.all, itemId, usedOnly ?? false] as const,
};

export function useWorkspaceItemReferences(
  itemId: string | undefined,
  opts?: { usedOnly?: boolean; enabled?: boolean },
) {
  const usedOnly = opts?.usedOnly ?? false;
  const enabled = (opts?.enabled ?? true) && !!itemId;
  return useQuery<{ references: Reference[] }, Error, Reference[]>({
    queryKey: workspaceItemReferenceKeys.byItem(itemId ?? "", usedOnly),
    queryFn: () => workspaceApi.listReferences(itemId!, { usedOnly }),
    select: (data) => data.references,
    enabled,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnMount: false,
  });
}
