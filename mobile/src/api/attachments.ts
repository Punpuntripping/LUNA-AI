import type { UploadInitResponse, WorkspaceItem } from "@contracts";

import { apiFetch } from "./client";

// Chat attachments — the web's three-step resumable flow (frontend/lib/api.ts
// initAttachment / finalizeAttachment / cancelAttachment; backend
// app/api/workspace.py). NOT the deprecated multipart `/attachments/upload`.

export interface InitAttachmentBody {
  filename: string;
  mime_type: string;
  size_bytes: number;
  /** Client page estimate for the OCR quota gate (PDF byte scan; image → 1). */
  page_count?: number;
}

/** Opens the upload: a `workspace_items` row (`upload_status: uploading`) + the
 *  Supabase TUS `upload_url` to send the bytes to. */
export function initAttachment(conversationId: string, body: InitAttachmentBody): Promise<UploadInitResponse> {
  return apiFetch<UploadInitResponse>(
    `/conversations/${encodeURIComponent(conversationId)}/workspace/attachments/init`,
    { method: "POST", body },
  );
}

/** Bytes landed: the server checks size + magic bytes and returns the row. */
export function finalizeAttachment(itemId: string): Promise<WorkspaceItem> {
  return apiFetch<WorkspaceItem>(`/workspace/attachments/${encodeURIComponent(itemId)}/finalize`, {
    method: "POST",
    body: {},
  });
}

/** User cancelled (or gave up on a failed upload). Idempotent on the server. */
export function cancelAttachment(itemId: string): Promise<{ success: boolean }> {
  return apiFetch<{ success: boolean }>(`/workspace/attachments/${encodeURIComponent(itemId)}/cancel`, {
    method: "POST",
    body: {},
  });
}
