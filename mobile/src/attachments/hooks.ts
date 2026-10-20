import { useStore } from "zustand";

import type { PendingAttachment } from "./upload-manager";
import { attachmentUploads } from "./uploader";

export { attachmentGate, type AttachmentGate } from "./upload-manager";

const EMPTY: PendingAttachment[] = [];

/** The composer chips of one conversation (`null` = c/new: none yet). */
export function useConversationAttachments(conversationId: string | null): PendingAttachment[] {
  return useStore(attachmentUploads.store, (s) => (conversationId ? s.byConversation[conversationId] ?? EMPTY : EMPTY));
}
