import { useQuery } from "@tanstack/react-query";
import { workspaceApi } from "@/lib/api";
import type { Reference } from "@/types";

/**
 * Migration 049: references for an ``agent_search`` workspace item are
 * fetched from the relational ``workspace_item_references`` table, not from
 * ``metadata.references`` JSONB. Backend reconstructs the full ``Reference``
 * payload by joining to chunks_v2 / cases / services, so the response shape
 * is byte-for-byte identical to the pre-049 metadata field.
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
