"use client";

import { useConversationDetail } from "@/hooks/use-conversations";
import type { ConversationSummary } from "@/types";

/**
 * Hover hint on every affordance the demo conversation renders but refuses.
 *
 * The plan is explicit that these buttons stay VISIBLE (§4.2): the tour's Act 4
 * and Act 5 explain «مشاركة»، «حفظ كمدونة» and «+» — it explains them, it never
 * invokes them. Hiding them would delete the thing the step points at; enabling
 * them would hand one user a write path into everybody's row.
 */
export const DEMO_DISABLED_HINT = "متاح في محادثاتك";

/** Copy for the composer replacement (D7). */
export const DEMO_COMPOSER_HINT = "هذه محادثة تجريبية للاطّلاع";
export const DEMO_COMPOSER_CTA = "ابدأ محادثة جديدة";

/**
 * Is this conversation row THE shared demo conversation?
 *
 * Pure predicate for callers that already hold the row (sidebar list, /chats
 * index, search results) — no query, no subscription.
 *
 * The hardcoded fixture id lives in `backend/app/services/demo_service.py` and
 * NOWHERE else. `is_demo` is derived there and rides the conversation payload,
 * so repointing the demo is a backend change with no frontend release.
 */
export function isDemoConversation(
  conversation: Pick<ConversationSummary, "is_demo"> | null | undefined,
): boolean {
  return conversation?.is_demo === true;
}

/**
 * `is_demo` for the conversation currently on screen.
 *
 * Reads it off `useConversationDetail`, which every host of this hook
 * (ChatContainer, ChatInput, WorkspacePane) is already subscribed to — React
 * Query dedupes on the shared key, so this costs one selector, not one request.
 *
 * Returns `false` for `undefined` (the brand-new-chat composer at `/chat`, where
 * no conversation exists yet) and while the detail query is still in flight —
 * the safe direction: a demo conversation briefly rendering as writable is one
 * refused POST, whereas a real conversation briefly rendering as read-only
 * would eat the user's first message.
 */
export function useIsDemoConversation(
  conversationId: string | null | undefined,
): boolean {
  const { data } = useConversationDetail(conversationId ?? undefined);
  return isDemoConversation(data?.conversation);
}
