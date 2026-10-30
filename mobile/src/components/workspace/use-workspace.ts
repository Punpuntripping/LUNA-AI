import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { isWiPreviewLocked, type WorkspaceItem, type WorkspaceItemListResponse } from "@contracts";

import {
  createNote,
  createReferenceItem,
  deleteWorkspaceItem,
  getWorkspaceFileUrl,
  listConversationWorkspace,
  setWorkspaceVisibility,
} from "@/api/workspace";
import { workspaceKeys } from "@/chat/keys";

import { NEW_NOTE_TITLE, NEW_REFERENCES_TITLE, visibleItems } from "./workspace-model";

// Server state of the workspace screen — the web's hooks/use-workspace.ts on
// the chat's query keys (src/chat/keys.ts), so the stream's
// `workspace_item_*` invalidations (chat-engine `invalidateWorkspace`) refresh
// this list too (W4).

/** The conversation's items. Same key + fetcher as the chat's artifact
 *  lookup; this observer always refetches on mount (the screen just opened). */
export function useConversationWorkspace(conversationId: string | undefined) {
  return useQuery({
    queryKey: workspaceKeys.byConversation(conversationId ?? ""),
    queryFn: () => listConversationWorkspace(conversationId as string),
    enabled: !!conversationId,
    staleTime: 0,
  });
}

/** Signed URL of an attachment's original (the web caches it 50 min). */
export function useWorkspaceFileUrl(itemId: string, enabled: boolean) {
  return useQuery({
    queryKey: [...workspaceKeys.detail(itemId), "file"] as const,
    queryFn: () => getWorkspaceFileUrl(itemId),
    enabled,
    staleTime: 50 * 60 * 1000,
    retry: false,
  });
}

function patchListItem(qc: QueryClient, conversationId: string | null, updated: WorkspaceItem): void {
  if (!conversationId) return;
  qc.setQueryData<WorkspaceItemListResponse>(workspaceKeys.byConversation(conversationId), (old) =>
    old ? { ...old, items: old.items.map((i) => (i.item_id === updated.item_id ? updated : i)) } : old,
  );
}

/** «إضافة عنصر» → ملاحظة / مراجع (web WorkspaceAddMenu titles). */
export function useCreateWorkspaceItem(conversationId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (kind: "note" | "references") =>
      kind === "note"
        ? createNote(conversationId, { title: NEW_NOTE_TITLE, content_md: "" })
        : createReferenceItem(conversationId, { title: NEW_REFERENCES_TITLE, content_md: "" }),
    onSuccess: (created) => {
      qc.setQueryData(workspaceKeys.detail(created.item_id), created);
      void qc.invalidateQueries({ queryKey: workspaceKeys.byConversation(conversationId) });
    },
  });
}

/** إخفاء / إظهار — the detail and the list row flip at once. */
export function useSetVisibility() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ item, isVisible }: { item: WorkspaceItem; isVisible: boolean }) =>
      setWorkspaceVisibility(item.item_id, isVisible),
    onSuccess: (updated, { item }) => {
      qc.setQueryData(workspaceKeys.detail(updated.item_id), updated);
      const conv = updated.conversation_id ?? item.conversation_id;
      patchListItem(qc, conv, updated);
      if (conv) void qc.invalidateQueries({ queryKey: workspaceKeys.byConversation(conv) });
    },
  });
}

/** حذف — the row leaves the list right away; the item's caches are dropped. */
export function useDeleteWorkspaceItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (item: WorkspaceItem) => deleteWorkspaceItem(item.item_id),
    onSuccess: (_res, item) => {
      const conv = item.conversation_id;
      if (conv) {
        qc.setQueryData<WorkspaceItemListResponse>(workspaceKeys.byConversation(conv), (old) =>
          old
            ? { items: old.items.filter((i) => i.item_id !== item.item_id), total: Math.max(0, old.total - 1) }
            : old,
        );
        void qc.invalidateQueries({ queryKey: workspaceKeys.byConversation(conv) });
      }
      qc.removeQueries({ queryKey: workspaceKeys.detail(item.item_id) });
      qc.removeQueries({ queryKey: workspaceKeys.references(item.item_id, false) });
      qc.removeQueries({ queryKey: workspaceKeys.references(item.item_id, true) });
    },
  });
}

/**
 * W4 for an OPEN item: the stream invalidates only the conversation's list on
 * `workspace_item_updated / locked / unlocked`, so when the list's copy of
 * this item is newer than the detail on screen (the agent still writing a
 * draft, a lock taken or released, the WI preview lock on or off), refetch
 * the detail.
 */
export function useFollowListUpdates(item: WorkspaceItem | undefined) {
  const qc = useQueryClient();
  const conv = item?.conversation_id ?? null;
  const list = useQuery({
    queryKey: workspaceKeys.byConversation(conv ?? ""),
    queryFn: () => listConversationWorkspace(conv as string),
    enabled: !!conv,
    staleTime: 30_000,
  });
  const listRow = item ? list.data?.items.find((i) => i.item_id === item.item_id) : undefined;
  const stamp = (i: WorkspaceItem) => `${i.updated_at}|${String(i.is_visible)}|${i.title}|${String(isWiPreviewLocked(i))}`;
  const listStamp = listRow ? stamp(listRow) : null;
  const itemStamp = item ? stamp(item) : null;
  const itemId = item?.item_id;
  useEffect(() => {
    if (!itemId || !listStamp || listStamp === itemStamp) return;
    void qc.invalidateQueries({ queryKey: workspaceKeys.detail(itemId), exact: true });
  }, [qc, itemId, listStamp, itemStamp]);
}

/** «مساحة العمل (n)» in the conversation menu: what the list shows by default
 *  (user-hidden items left out); `null` until known. Only fetched while asked. */
export function useWorkspaceCount(conversationId: string | undefined, enabled: boolean): number | null {
  const { data } = useQuery({
    queryKey: workspaceKeys.byConversation(conversationId ?? ""),
    queryFn: () => listConversationWorkspace(conversationId as string),
    enabled: !!conversationId && enabled,
    staleTime: 30_000,
  });
  return data ? visibleItems(data.items, false).length : null;
}
