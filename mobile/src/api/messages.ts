import type { MessageListResponse } from "@contracts";

import { apiFetch } from "./client";

// GET /conversations/{id}/messages — newest first, `before` = the oldest
// message id already loaded (cursor). `run_active` is the server's snapshot of
// a live run (backend/app/api/messages.py). Sending is SSE: see stream.ts.

export const MESSAGE_PAGE_SIZE = 30;

export function listMessages(
  conversationId: string,
  params: { limit?: number; before?: string } = {},
): Promise<MessageListResponse> {
  const qs = new URLSearchParams();
  qs.set("limit", String(params.limit ?? MESSAGE_PAGE_SIZE));
  if (params.before) qs.set("before", params.before);
  return apiFetch<MessageListResponse>(
    `/conversations/${encodeURIComponent(conversationId)}/messages?${qs.toString()}`,
  );
}
