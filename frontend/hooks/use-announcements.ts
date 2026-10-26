import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { announcementsApi } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import type { AnnouncementsResponse } from "@/lib/contracts";

export const announcementKeys = {
  all: ["announcements"] as const,
  // Keyed on the account so a sign-out/sign-in on the same tab never shows
  // the previous user's list or unread count.
  list: (userId: string | null) => [...announcementKeys.all, userId] as const,
};

/**
 * «رسائل ريحان» — ONE query shared by the sidebar bell, the popup and
 * /messages, so the unread count stays in step everywhere. A failed read is
 * silent: no badge, no popup — announcements must never block the app.
 */
export function useAnnouncements() {
  const userId = useAuthStore((s) => s.user?.user_id ?? null);
  return useQuery<AnnouncementsResponse>({
    queryKey: announcementKeys.list(userId),
    queryFn: announcementsApi.list,
    enabled: userId !== null,
    staleTime: 5 * 60_000,
    // Picks up a message published while the tab sat in the background.
    refetchOnWindowFocus: true,
    retry: 1,
  });
}

/**
 * Mark ids read. Optimistic: the cache flips `read` and drops the count at
 * once, so the bell and the popup queue move on without waiting for the POST.
 * On failure the list is refetched, which restores the server's truth.
 */
export function useMarkAnnouncementsRead() {
  const queryClient = useQueryClient();
  const userId = useAuthStore((s) => s.user?.user_id ?? null);
  const key = announcementKeys.list(userId);

  return useMutation({
    mutationFn: (ids: string[]) => announcementsApi.markRead(ids),
    onMutate: (ids) => {
      queryClient.setQueryData<AnnouncementsResponse>(key, (prev) => {
        if (!prev) return prev;
        const marked = new Set(ids);
        const items = prev.items.map((item) =>
          marked.has(item.announcement_id) ? { ...item, read: true } : item,
        );
        return { items, unread_count: items.filter((i) => !i.read).length };
      });
    },
    onError: () => {
      void queryClient.invalidateQueries({ queryKey: key });
    },
  });
}
