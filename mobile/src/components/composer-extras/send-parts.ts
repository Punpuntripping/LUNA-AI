import { withTemplateDirective, type Attachment, type PendingTemplate } from "@contracts";

// Pure: what a send carries from the composer extras (web ChatInput.handleSend
// + use-chat.ts send). No React / RN here so it runs under `npm test`.

/** A blog chip in the composer (web `PendingBlog`). Client-only UI state. */
export interface PendingBlogChip {
  /** Chip id (client-generated). */
  id: string;
  /** The 32-hex share token. */
  token: string;
  /** The post's title once the import resolves. */
  title: string | null;
  /** `loading` blocks send; `failed` never does (and carries nothing). */
  status: "loading" | "ready" | "failed";
  /** workspace_items.item_id of the imported copy; null until ready. */
  itemId: string | null;
  /** THIS chip's import created the item (vs. a server-dedup reuse) — only
   *  then does removing the chip delete it. */
  createdByChip: boolean;
  errorMessage: string | null;
}

/** Chip label (web BlogChips). */
export function blogChipLabel(chip: PendingBlogChip): string {
  if (chip.status === "failed") return chip.errorMessage || "رابط مدونة غير صالح";
  return (chip.title ?? "").trim() || "مدونة";
}

export function readyBlogChips(blogs: readonly PendingBlogChip[]): PendingBlogChip[] {
  return blogs.filter((b) => b.status === "ready" && !!b.itemId);
}

/** Web `canSend`: a template chip or a ready blog makes an empty text sendable. */
export function extrasMakeSendable(template: PendingTemplate | null, blogs: readonly PendingBlogChip[]): boolean {
  return template !== null || readyBlogChips(blogs).length > 0;
}

/** Web `hasInFlightUpload` (blog part): an import still running blocks send. */
export function extrasBlockSend(blogs: readonly PendingBlogChip[]): boolean {
  return blogs.some((b) => b.status === "loading");
}

export interface ExtrasSendParts {
  /** The text to POST — the directive line appended when a template is picked. */
  content: string;
  /** Ready blog items, to join the message's `attachment_ids`. */
  attachmentIds: string[];
  /** What the optimistic bubble shows for them (web use-chat). */
  optimisticAttachments: Attachment[];
  /** Chip ids this send carries — released once the server takes the send. */
  sentBlogChipIds: string[];
}

export type ExtrasSendResult = { ok: true; parts: ExtrasSendParts } | { ok: false; error: string };

/**
 * `text` + the picked template + the blog chips → the send's parts. Refuses
 * (Arabic) when the directive pushes the text past `maxChars`, like the web.
 * `maxCharsLabel` is the already-formatted (Latin-digit) limit.
 */
export function buildExtrasSend(
  text: string,
  template: PendingTemplate | null,
  blogs: readonly PendingBlogChip[],
  maxChars: number,
  maxCharsLabel: string,
): ExtrasSendResult {
  const content = withTemplateDirective(text, template);
  if (content.length > maxChars) return { ok: false, error: `الحد الأقصى ${maxCharsLabel} حرف` };
  const ready = readyBlogChips(blogs);
  return {
    ok: true,
    parts: {
      content,
      attachmentIds: ready.map((b) => b.itemId as string),
      optimisticAttachments: ready.map((b) => ({
        id: b.id,
        document_id: b.itemId as string,
        attachment_type: "file" as const,
        filename: (b.title ?? "").trim() || "مدونة",
        file_size: 0,
        kind: "agent_search" as const,
      })),
      sentBlogChipIds: blogs.map((b) => b.id),
    },
  };
}
