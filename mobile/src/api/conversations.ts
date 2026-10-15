import type { ConversationDetail, ConversationListResponse } from "@contracts";

import { apiFetch } from "./client";

// /conversations — backend/app/api/conversations.py. Server order: starred
// first (starred_at DESC), then most recent. `q` searches titles + message
// text (message matches carry `snippet`, `match_type: "message"`).

/** `{conversation}` envelope of create / get / patch (ConversationResponse). */
interface ConversationEnvelope {
  conversation: ConversationDetail;
}

export interface ListConversationsParams {
  q?: string;
  starred?: boolean;
  /** 1–100 (server default 50). */
  limit?: number;
  offset?: number;
}

export function listConversations(params: ListConversationsParams = {}): Promise<ConversationListResponse> {
  const qs = new URLSearchParams();
  if (params.limit) qs.set("limit", String(params.limit));
  if (params.offset) qs.set("offset", String(params.offset));
  const q = params.q?.trim();
  if (q) qs.set("q", q);
  if (params.starred) qs.set("starred", "true");
  const query = qs.toString();
  return apiFetch<ConversationListResponse>(`/conversations${query ? `?${query}` : ""}`);
}

export async function getConversation(conversationId: string): Promise<ConversationDetail> {
  const res = await apiFetch<ConversationEnvelope>(`/conversations/${encodeURIComponent(conversationId)}`);
  return res.conversation;
}

/** General (non-case) conversation — the app has no Cases. */
export async function createConversation(): Promise<ConversationDetail> {
  const res = await apiFetch<ConversationEnvelope>("/conversations", { method: "POST", body: { case_id: null } });
  return res.conversation;
}

/** Rename (`title_ar`, 1–500 chars) and/or star. At least one field. */
export async function updateConversation(
  conversationId: string,
  patch: { title_ar?: string; starred?: boolean },
): Promise<ConversationDetail> {
  const res = await apiFetch<ConversationEnvelope>(`/conversations/${encodeURIComponent(conversationId)}`, {
    method: "PATCH",
    body: patch,
  });
  return res.conversation;
}

/** Soft delete. On the shared demo conversation the backend hides it for
 *  this user only (the UI says «إخفاء» there). */
export async function deleteConversation(conversationId: string): Promise<void> {
  await apiFetch<{ success: boolean }>(`/conversations/${encodeURIComponent(conversationId)}`, {
    method: "DELETE",
  });
}
