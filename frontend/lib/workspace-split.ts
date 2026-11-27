// The chat | pane split when an agent's output opens (owner 2026-11-27).

/** The chat's share (%) of the split when a new output below opens on its own. */
export const CHAT_SHARE_FOR_OUTPUTS = 25;

/**
 * True for the new items that open with the chat at CHAT_SHARE_FOR_OUTPUTS:
 * «تحليل قانوني» (`agent_search` · `legal_synthesis` — a deep search report,
 * and each «بحث شامل» report), the «بحث شامل» merged answer (`agent_search` ·
 * `extended_synthesis`) and the writer's output (`agent_writing`). Everything
 * else (notes, attachments, «الإحالات»…) keeps the user's split.
 */
export function opensWithNarrowChat(
  kind: string | null | undefined,
  subtype: string | null | undefined,
): boolean {
  if (kind === "agent_writing") return true;
  return kind === "agent_search" && (subtype === "legal_synthesis" || subtype === "extended_synthesis");
}
