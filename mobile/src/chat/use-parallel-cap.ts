import { useQuery } from "@tanstack/react-query";

import { getUsage, maxParallelRuns } from "@/api/usage";

import { useIsConversationRunning, useStreamCount } from "./stream-store";

// Composer pre-gate on the plan's parallel cap (web ChatInput.tsx:138-150).
// The server stays authoritative (an over-cap send is refused with
// `parallel_limit`); this only spares the round-trip. /usage is fetched only
// once ANOTHER conversation is running here, and an unknown cap never gates.

export const usageKeys = {
  all: ["usage"] as const,
  current: () => [...usageKeys.all, "current"] as const,
};

export interface ParallelGate {
  atCap: boolean;
  cap: number | null;
}

export function useParallelGate(conversationId: string | null): ParallelGate {
  const running = useStreamCount();
  const self = useIsConversationRunning(conversationId ?? "") ? 1 : 0;
  const others = running - self;
  const { data } = useQuery({
    queryKey: usageKeys.current(),
    queryFn: getUsage,
    enabled: others > 0,
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  });
  const cap = data ? maxParallelRuns(data) : null;
  return { atCap: self === 0 && cap !== null && others >= cap, cap };
}
