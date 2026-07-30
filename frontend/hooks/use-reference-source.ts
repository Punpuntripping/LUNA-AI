import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  publicBlogApi,
  workspaceApi,
  type ReferenceSourceResult,
} from "@/lib/api";
import {
  fetchLibraryBalance,
  type LibraryBalance,
} from "@/lib/library/full-content";
import type { ReferenceBalance } from "@/types";

/**
 * ACCESS-TIERS PHASE C — the on-demand reference source reveal (§6.2).
 *
 * The references list no longer ships source bodies. `[n]` and «عرض المصدر»
 * used to be pure client-side state changes over an already-downloaded
 * `source_view`; they now fetch ONE source at a time from
 * `GET /workspace/{item_id}/references/{n}/source`, which runs `resolve_access`
 * and is where the meter finally has a server call to sit on.
 *
 * ⚠ THE `enabled` GUARD IS THE ENTIRE POINT. This hook must never run for a
 * reference whose dialog is not open: a panel with twelve citations that
 * prefetched would spend twelve unlocks — potentially an entire free period —
 * for a user who clicked nothing. Pass `n = null` whenever the dialog is closed.
 *
 * `staleTime: Infinity` because a revealed source is immutable and the unlock is
 * permanent; a refetch would be a free but pointless round-trip. Re-opening the
 * same `[n]` in the same session is served straight from cache and touches
 * neither the network nor the ledger.
 */
export const referenceSourceKeys = {
  all: ["reference-source"] as const,
  byRef: (itemId: string, n: number) =>
    [...referenceSourceKeys.all, itemId, n] as const,
};

/**
 * Shared across every surface that shows the «فتح المصادر» balance, so N open
 * reference panels cost ONE `/usage` read rather than N.
 */
export const libraryBalanceKeys = {
  all: ["library-balance"] as const,
};

/** Project the reveal response's post-charge numbers onto the chip's shape. */
function toLibraryBalance(balance: ReferenceBalance): LibraryBalance {
  const limit = typeof balance.limit === "number" ? balance.limit : null;
  const used = typeof balance.used === "number" ? balance.used : 0;
  return {
    used,
    limit,
    remaining: limit === null ? null : Math.max(limit - used, 0),
    resets_at: balance.resets_at ?? null,
  };
}

/**
 * Fetch one reference's original source. Enabled ONLY while its dialog is open.
 *
 * Every outcome — including a 402 refusal and a 429 — arrives as `data`, never
 * as a thrown error, so React Query never treats a deliberate policy answer as
 * a failure worth retrying. `retry: false` covers the genuine transport
 * failures: a retry could not help a 404, and the user has an explicit
 * «إعادة المحاولة» affordance for the ones where it could.
 */
export function useReferenceSource(
  itemId: string | undefined,
  n: number | null,
  opts?: { enabled?: boolean; blogToken?: string },
) {
  const queryClient = useQueryClient();
  // Two ways to address a source: an owned workspace item (in-app) or a public
  // blog token (a reader on someone else's published post). Exactly one is set.
  const blogToken = opts?.blogToken;
  const addressable = !!itemId || !!blogToken;
  const enabled = (opts?.enabled ?? true) && addressable && n !== null;

  const query = useQuery<ReferenceSourceResult>({
    queryKey: referenceSourceKeys.byRef(itemId ?? `blog:${blogToken ?? ""}`, n ?? -1),
    queryFn: () =>
      blogToken
        ? publicBlogApi.getReferenceSource(blogToken, n!)
        : workspaceApi.getReferenceSource(itemId!, n!),
    enabled,
    staleTime: Infinity,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnMount: false,
  });

  // An unlock may have just been spent. The response already carries the
  // post-charge allowance (a `granted` decision reports `used + cost`), so the
  // chip resyncs with no extra round-trip — «no prompt, but never a silent
  // meter» (§5.1). `balance` is null for a policy-open item, where the quota
  // was never consulted and there is nothing to correct.
  const revealed = query.data;
  useEffect(() => {
    if (!revealed?.ok) return;
    const balance = revealed.data.balance;
    if (!balance) return;
    const next = toLibraryBalance(balance);
    queryClient.setQueryData<LibraryBalance | null>(
      libraryBalanceKeys.all,
      (current) => {
        // This effect re-fires when a CACHED reveal is re-opened (staleTime is
        // Infinity, so reopening `[3]` replays its original response). Consumption
        // only ever moves forward inside a period, so refuse to walk the meter
        // backwards — otherwise re-reading an earlier citation would repaint a
        // balance the user has already spent past.
        if (current && next.used < current.used) return current;
        return next;
      },
    );
  }, [revealed, queryClient]);

  return query;
}

/**
 * The passive «فتح المصادر» allowance for the chip beside the reveal action.
 *
 * Reading the balance costs nothing and charges nothing (it is a plain
 * `/usage` read), so unlike the reveal itself this one IS safe on mount — but
 * it stays `enabled`-gated anyway so a panel with no revealable reference (an
 * anonymous blog snapshot, a stub-only list) issues no request at all.
 *
 * Returns `null` for anonymous readers, locked accounts and every failure: a
 * missing chip is strictly better than a wrong number beside a spend action.
 */
export function useLibraryBalance(opts?: { enabled?: boolean }) {
  return useQuery<LibraryBalance | null>({
    queryKey: libraryBalanceKeys.all,
    queryFn: () => fetchLibraryBalance(),
    enabled: opts?.enabled ?? true,
    staleTime: 60_000,
    retry: false,
    refetchOnWindowFocus: false,
  });
}
