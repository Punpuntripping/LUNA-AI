import { useQuery } from "@tanstack/react-query";

import { getReferenceSource, type ReferenceSourceResult } from "@/api/workspace";

const TEN_MINUTES = 10 * 60 * 1000;

export const referenceSourceKey = (itemId: string, n: number) => ["reference-source", itemId, n] as const;

/**
 * The METERED reveal of one source. Mounted only by the reader, and
 * `enabled` only once the free list says a source exists — opening the
 * reader IS the user gesture (plan 05 §R2). Never prefetched.
 *
 * - No automatic retries, no refetch on focus/reconnect: every request is a
 *   potential charge, so only the reader's own buttons fire again.
 * - A success stays fresh for the session's cache lifetime (re-opening the
 *   same `[n]` costs nothing anyway — `already_unlocked` — but saves the
 *   trip); a failure is stale at once, so re-opening asks again.
 * - Deliberately NOT under `["workspace"]`: a workspace invalidation from the
 *   chat must never re-fire a metered request. Logout's `queryClient.clear()`
 *   still drops it.
 */
export function useReferenceSource(itemId: string, n: number, enabled: boolean) {
  return useQuery<ReferenceSourceResult>({
    queryKey: referenceSourceKey(itemId, n),
    queryFn: () => getReferenceSource(itemId, n),
    enabled,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    staleTime: (query) => (query.state.data?.ok ? Infinity : 0),
    gcTime: TEN_MINUTES,
  });
}
