import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
  type QueryClient,
  type QueryKey,
} from "@tanstack/react-query";

import type { ConversationListResponse, ConversationSummary } from "@contracts";

import {
  createConversation,
  deleteConversation,
  getConversation,
  listConversations,
  updateConversation,
} from "@/api/conversations";

import { conversationKeys, messageKeys } from "./keys";

// Conversation list (L1–L4) — mirrors frontend/hooks/use-conversations.ts:
// offset pages of 30, server order starred-first then most recent. Star /
// rename / delete patch every cached list optimistically, roll back on error,
// and refetch on settle so the server's order wins.

export const CHATS_PAGE_SIZE = 30;

type ConversationPages = InfiniteData<ConversationListResponse, number>;

const nextOffset = (lastPage: ConversationListResponse, allPages: ConversationListResponse[]) =>
  lastPage.has_more ? allPages.length * CHATS_PAGE_SIZE : undefined;

/** «الكل» / «المميّزة» index (no search query). */
export function useConversationsIndex(opts: { starred: boolean }) {
  const { starred } = opts;
  return useInfiniteQuery({
    queryKey: conversationKeys.index(starred),
    queryFn: ({ pageParam }) => listConversations({ limit: CHATS_PAGE_SIZE, offset: pageParam, starred }),
    initialPageParam: 0,
    getNextPageParam: nextOffset,
  });
}

/** Server search over titles + message text. Disabled for a blank query —
 *  the caller debounces (300 ms) and shows the index meanwhile. */
export function useSearchConversations(q: string, opts: { starred: boolean }) {
  const trimmed = q.trim();
  const { starred } = opts;
  return useInfiniteQuery({
    queryKey: conversationKeys.search(trimmed, starred),
    queryFn: ({ pageParam }) =>
      listConversations({ q: trimmed, starred, limit: CHATS_PAGE_SIZE, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: nextOffset,
    enabled: trimmed.length > 0,
  });
}

export function useConversationDetail(conversationId: string | undefined) {
  return useQuery({
    queryKey: conversationKeys.detail(conversationId ?? ""),
    queryFn: () => getConversation(conversationId as string),
    enabled: !!conversationId,
  });
}

/** Lazy creation on the first send (`c/new` → POST → replace route → send). */
export function useCreateConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => createConversation(),
    onSuccess: (conversation) => {
      qc.setQueryData(conversationKeys.detail(conversation.conversation_id), conversation);
      void qc.invalidateQueries({ queryKey: conversationKeys.lists() });
    },
  });
}

// ---- optimistic patches over every cached list page ----

type Snapshot = [QueryKey, ConversationPages | undefined][];

async function patchLists(
  qc: QueryClient,
  fn: (rows: ConversationSummary[]) => ConversationSummary[],
): Promise<Snapshot> {
  await qc.cancelQueries({ queryKey: conversationKeys.lists() });
  const snapshot = qc.getQueriesData<ConversationPages>({ queryKey: conversationKeys.lists() });
  for (const [key, data] of snapshot) {
    if (!data?.pages) continue;
    qc.setQueryData<ConversationPages>(key, {
      ...data,
      pages: data.pages.map((page) => ({ ...page, conversations: fn(page.conversations) })),
    });
  }
  return snapshot;
}

function rollback(qc: QueryClient, snapshot: Snapshot | undefined): void {
  for (const [key, data] of snapshot ?? []) qc.setQueryData(key, data);
}

function patchDetail(qc: QueryClient, id: string, patch: Partial<ConversationSummary>): void {
  qc.setQueryData<ConversationSummary>(conversationKeys.detail(id), (old) => (old ? { ...old, ...patch } : old));
}

export function useStarConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, starred }: { id: string; starred: boolean }) => updateConversation(id, { starred }),
    onMutate: async ({ id, starred }) => {
      const starredAt = starred ? new Date().toISOString() : null;
      const snapshot = await patchLists(qc, (rows) =>
        rows.map((c) => (c.conversation_id === id ? { ...c, is_starred: starred, starred_at: starredAt } : c)),
      );
      patchDetail(qc, id, { is_starred: starred, starred_at: starredAt });
      return { snapshot };
    },
    onError: (_err, _vars, ctx) => rollback(qc, ctx?.snapshot),
    onSettled: (_data, _err, { id }) => {
      void qc.invalidateQueries({ queryKey: conversationKeys.lists() });
      void qc.invalidateQueries({ queryKey: conversationKeys.detail(id) });
    },
  });
}

export function useRenameConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) => updateConversation(id, { title_ar: title.trim() }),
    onMutate: async ({ id, title }) => {
      const title_ar = title.trim();
      const snapshot = await patchLists(qc, (rows) =>
        rows.map((c) => (c.conversation_id === id ? { ...c, title_ar } : c)),
      );
      patchDetail(qc, id, { title_ar });
      return { snapshot };
    },
    onError: (_err, _vars, ctx) => rollback(qc, ctx?.snapshot),
    onSettled: (_data, _err, { id }) => {
      void qc.invalidateQueries({ queryKey: conversationKeys.lists() });
      void qc.invalidateQueries({ queryKey: conversationKeys.detail(id) });
    },
  });
}

/** Delete (or, on the shared demo conversation, hide for this user). */
export function useDeleteConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteConversation(id),
    onMutate: async (id) => {
      const snapshot = await patchLists(qc, (rows) => rows.filter((c) => c.conversation_id !== id));
      return { snapshot };
    },
    onError: (_err, _id, ctx) => rollback(qc, ctx?.snapshot),
    onSuccess: (_data, id) => {
      qc.removeQueries({ queryKey: conversationKeys.detail(id) });
      qc.removeQueries({ queryKey: messageKeys.list(id) });
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: conversationKeys.lists() });
    },
  });
}
