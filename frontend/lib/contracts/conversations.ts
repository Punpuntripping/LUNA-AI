// Conversation list/detail shapes — moved verbatim from frontend/types/index.ts
// (the web re-exports them from `@/types`).

// ==========================================
// CONVERSATIONS
// ==========================================

export interface ConversationSummary {
  conversation_id: string;
  case_id?: string | null;
  title_ar?: string | null;
  message_count: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  /** True when the conversation is starred (pinned to the top of lists). */
  is_starred: boolean;
  /** ISO timestamp the conversation was starred, or null when not starred. */
  starred_at?: string | null;
  /**
   * Trimmed excerpt of the matching message content — set ONLY on search
   * results where the match was in a message body (``match_type === 'message'``).
   * Null for title matches and for non-search listings.
   */
  snippet?: string | null;
  /**
   * Where the search term matched: ``'title'`` (in title_ar), ``'message'``
   * (in a message body, with ``snippet`` populated), or null for non-search
   * listings.
   */
  match_type?: 'title' | 'message' | null;
  /**
   * DERIVED, server-side: this row is the ONE shared «محادثة تجريبية» every
   * account sees (demo_conversation_product_tour §3.2). The id itself never
   * reaches the client — components branch on this flag alone, so the fixture
   * can be repointed backend-side without a frontend release.
   *
   * Read-only for everyone (D2): the composer becomes a hint bar, the WI's
   * 👍/👎 are hidden (`workspace_items.feedback` is ONE shared column),
   * مشاركة / حفظ كمدونة / «+» render disabled, and «حذف» becomes «إخفاء» —
   * a per-user preference flag, never a shared soft-delete (D8).
   *
   * Absent on every ordinary conversation, hence optional.
   */
  is_demo?: boolean;
}

export interface ConversationDetail extends ConversationSummary {
  model_name?: string | null;
}

export interface ConversationListResponse {
  conversations: ConversationSummary[];
  total: number;
  has_more: boolean;
}

export interface CreateConversationRequest {
  case_id?: string | null;
}
