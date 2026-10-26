import type { AnnouncementsResponse } from "@contracts";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import { getAnnouncements, markAnnouncementsRead } from "@/api/announcements";
import { useSession } from "@/auth/session-store";

import { normalizeAnnouncements, readBatches, withRead } from "./logic";

// ONE query behind the bell, the messages screen and the popup, so the count
// stays in sync. Keyed per account (logout also clears the whole cache).
// Refetched whenever the app returns to the foreground. A failed fetch is
// quiet: no badge, no popup — the messages screen alone shows the error.

export const announcementKeys = {
  all: ["announcements"] as const,
  list: (userId: string) => [...announcementKeys.all, "list", userId] as const,
};

// Ids this account read in this app session. Every fetch re-applies them, so
// a response that raced the POST (or a POST that failed) can never pop a
// closed message up again. A failed POST is retried by the next launch's
// popup — the server is still the source of truth across sessions.
const sessionReads = new Map<string, Set<string>>();

function readsFor(userId: string): Set<string> {
  let set = sessionReads.get(userId);
  if (!set) {
    set = new Set();
    sessionReads.set(userId, set);
  }
  return set;
}

export function useAnnouncements() {
  const userId = useSession((s) => s.user?.user_id ?? null);
  return useQuery({
    queryKey: announcementKeys.list(userId ?? ""),
    queryFn: async (): Promise<AnnouncementsResponse> => {
      const data = normalizeAnnouncements(await getAnnouncements());
      return userId ? withRead(data, readsFor(userId)) : data;
    },
    enabled: userId !== null,
    staleTime: 30_000,
    refetchOnWindowFocus: "always",
  });
}

/** Marks ids read: the cached list flips at once (bell count drops), the POST
 *  follows in the background and never surfaces an error. */
export function useMarkAnnouncementsRead(): (ids: readonly string[]) => void {
  const qc = useQueryClient();
  const userId = useSession((s) => s.user?.user_id ?? null);
  return useCallback(
    (ids: readonly string[]) => {
      if (!userId || ids.length === 0) return;
      const reads = readsFor(userId);
      ids.forEach((id) => reads.add(id));
      qc.setQueryData<AnnouncementsResponse>(announcementKeys.list(userId), (old) => (old ? withRead(old, ids) : old));
      for (const batch of readBatches(ids)) markAnnouncementsRead(batch).catch(() => undefined);
    },
    [qc, userId],
  );
}
