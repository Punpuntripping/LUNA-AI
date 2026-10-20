import { planBlogQueue } from "@contracts";

import { createBlogItem, deleteBlogChipItem } from "@/api/blog-items";
import { errorMessage } from "@/api/errors";
import { conversationKeys, workspaceKeys } from "@/chat/keys";
import { queryClient } from "@/lib/query-client";

import { useComposerExtrasStore } from "./pending-store";
import type { PendingBlogChip } from "./send-parts";

// «مدونة» chips — the web ChatInput importBlogTokens / queueBlogTokens /
// handleRemoveBlog. The import fires at pick time (pre-send); the chip tracks
// it; the send collects the ready item ids.

const IMPORT_FAILED = "تعذّر استيراد المدونة";
export const NEED_CONVERSATION = "ابدأ محادثة أولاً قبل إضافة المدونات";

let seq = 0;
function chipId(): string {
  seq += 1;
  return `blog-${Date.now()}-${seq}`;
}

/** POST each token as a blog item of `conversationId`, one chip per token. */
export function importBlogTokens(conversationId: string, tokens: readonly string[]): void {
  const store = useComposerExtrasStore.getState();
  for (const token of tokens) {
    const id = chipId();
    const chip: PendingBlogChip = {
      id,
      token,
      title: null,
      status: "loading",
      itemId: null,
      createdByChip: false,
      errorMessage: null,
    };
    store.addBlog(conversationId, chip);
    createBlogItem(conversationId, token)
      .then((res) => {
        useComposerExtrasStore.getState().updateBlog(conversationId, id, {
          status: "ready",
          itemId: res.item.item_id,
          title: res.item.title,
          createdByChip: !res.already_attached,
        });
        void queryClient.invalidateQueries({ queryKey: workspaceKeys.byConversation(conversationId) });
        // The import may retitle a fresh «محادثة جديدة» after the blog.
        void queryClient.invalidateQueries({ queryKey: conversationKeys.all });
      })
      .catch((err: unknown) => {
        useComposerExtrasStore.getState().updateBlog(conversationId, id, {
          status: "failed",
          errorMessage: errorMessage(err, IMPORT_FAILED),
        });
      });
  }
}

/**
 * Queue picked tokens for the composer keyed `key`: dedup against the live
 * chips, cap at 3, and — on `c/new` — create the conversation first through
 * `ensureConversation` (which also navigates) and import there. Resolves to an
 * Arabic error to show, or null when queued / nothing new.
 */
export async function queueBlogTokens(opts: {
  conversationId: string | null;
  draftKey: string;
  tokens: readonly string[];
  ensureConversation?: () => Promise<string | null>;
}): Promise<string | null> {
  const { conversationId, draftKey, tokens, ensureConversation } = opts;
  const held = (useComposerExtrasStore.getState().blogs[draftKey] ?? []).map((b) => b.token);
  const plan = planBlogQueue(held, tokens);
  if (!plan.ok) return plan.error;

  if (conversationId) {
    importBlogTokens(conversationId, plan.tokens);
    return null;
  }
  if (!ensureConversation) return NEED_CONVERSATION;
  let id: string | null;
  try {
    id = await ensureConversation();
  } catch (err) {
    return errorMessage(err);
  }
  if (!id) return null; // the caller already reported why
  useComposerExtrasStore.getState().carryTemplate(draftKey, id);
  importBlogTokens(id, plan.tokens);
  return null;
}

/** Remove a chip; delete its item only if THIS chip's import created it.
 *  Best-effort — a failed delete leaves the item in the workspace. */
export function removeBlogChip(key: string, chip: PendingBlogChip): void {
  useComposerExtrasStore.getState().removeBlogs(key, [chip.id]);
  if (!chip.itemId || !chip.createdByChip) return;
  const conversationId = key;
  deleteBlogChipItem(chip.itemId)
    .then(() => queryClient.invalidateQueries({ queryKey: workspaceKeys.byConversation(conversationId) }))
    .catch(() => undefined);
}
