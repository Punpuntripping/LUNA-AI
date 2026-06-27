import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { messagesApi } from "@/lib/api";

const MESSAGE_PAGE_SIZE = 30;

/**
 * Background-recovery window for an empty assistant placeholder.
 *
 * While the newest message is a blank assistant row the run is either still
 * streaming or finishing in the background (the backend detaches a dropped
 * run, it never cancels it), so the poll below keeps refetching until it
 * fills. Past this age the run is considered dead — the poll stops AND the
 * UI flips the perpetual "thinking" spinner to a failed bubble (see
 * ``MessageList``). Both consumers share this constant so the spinner shows
 * for exactly as long as we are willing to recover, then fails.
 */
export const PLACEHOLDER_MAX_AGE_MS = 7 * 60 * 1000;

export const messageKeys = {
  all: ["messages"] as const,
  list: (conversationId: string) => [...messageKeys.all, "list", conversationId] as const,
};

export function useMessages(conversationId: string | undefined) {
  return useInfiniteQuery({
    queryKey: messageKeys.list(conversationId!),
    queryFn: ({ pageParam }) =>
      messagesApi.list(conversationId!, {
        limit: MESSAGE_PAGE_SIZE,
        before: pageParam as string | undefined,
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => {
      if (!lastPage.has_more || lastPage.messages.length === 0) return undefined;
      // Use the oldest message's ID as the cursor for the next page
      return lastPage.messages[lastPage.messages.length - 1].message_id;
    },
    enabled: !!conversationId,
    // Layer 2 (read-only recovery): while the newest message is an empty
    // assistant placeholder, a run is in flight (or was interrupted and is
    // finishing in the background — the backend detaches it, never cancels).
    // Poll until it fills so the card never stays blank after a dropped stream,
    // a dedup-rejected resend, or a page refresh mid-run. Read-only — it never
    // touches the running pipeline. Stops once content lands or the placeholder
    // is too old to still be running.
    refetchInterval: (query) => {
      const newest = query.state.data?.pages?.[0]?.messages?.[0];
      if (!newest || newest.role !== "assistant") return false;
      if ((newest.content ?? "").trim() !== "") return false;
      const ageMs = Date.now() - new Date(newest.created_at).getTime();
      return ageMs < PLACEHOLDER_MAX_AGE_MS ? 4000 : false;
    },
    // Messages are ordered newest-first from the API; we reverse in the UI
  });
}

export function useInvalidateMessages() {
  const qc = useQueryClient();
  return (conversationId: string) =>
    qc.invalidateQueries({ queryKey: messageKeys.list(conversationId) });
}
