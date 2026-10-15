import { isDeadPlaceholder, type Message } from "@contracts";

import type { StreamPhase } from "./stream-store";

// One FlashList item per message (plan M2), oldest → newest, NOT inverted.
// `buildChatItems` turns the cached messages + the live stream into items;
// `components/chat/MessageRow` renders one. Pure (no React) — unit-tested.

export type ChatItem =
  | { kind: "user"; key: string; message: Message }
  | { kind: "assistant"; key: string; message: Message; isLatest: boolean }
  /** The assistant row the stream is feeding right now (cached or not). */
  | { kind: "live"; key: string; message: Message }
  /** «ريحان يفكّر» — a placeholder still answering (or a send before `message_start`). */
  | { kind: "thinking"; key: string }
  /** A dead placeholder (newest row only) — retry = regenerate. */
  | { kind: "failed"; key: string; message: Message };

export interface LiveStreamInfo {
  phase: StreamPhase | null;
  assistantMessageId: string | null;
}

function isEmptyAssistant(m: Message): boolean {
  return (
    m.role === "assistant" &&
    !m.isOptimistic &&
    (m.content ?? "").trim() === "" &&
    !(m.artifact_ids && m.artifact_ids.length > 0)
  );
}

/** Port of the web MessageList render rules (MessageList.tsx:460-610). */
export function buildChatItems(
  messages: Message[],
  stream: LiveStreamInfo,
  runActive: boolean | undefined,
  nowMs: number,
): ChatItem[] {
  const items: ChatItem[] = [];
  const last = messages.length - 1;
  let hasThinking = false;
  let liveInCache = false;

  messages.forEach((m, idx) => {
    if (m.role === "system") return;
    if (m.role === "user") {
      items.push({ kind: "user", key: m.message_id, message: m });
      return;
    }
    if (stream.assistantMessageId !== null && m.message_id === stream.assistantMessageId) {
      liveInCache = true;
      items.push({ kind: "live", key: m.message_id, message: m });
      return;
    }
    if (isEmptyAssistant(m)) {
      const dead = isDeadPlaceholder({
        isStreaming: false,
        runActive,
        isNewest: idx === last,
        ageMs: nowMs - new Date(m.created_at).getTime(),
      });
      if (dead) {
        // A dead placeholder superseded by a newer turn is noise — drop it.
        if (idx === last) items.push({ kind: "failed", key: m.message_id, message: m });
        return;
      }
      hasThinking = true;
      items.push({ kind: "thinking", key: m.message_id });
      return;
    }
    items.push({ kind: "assistant", key: m.message_id, message: m, isLatest: idx === last });
  });

  if (stream.phase !== null && !liveInCache) {
    if (stream.assistantMessageId !== null) {
      items.push({
        kind: "live",
        key: stream.assistantMessageId,
        message: {
          message_id: stream.assistantMessageId,
          conversation_id: messages[0]?.conversation_id ?? "",
          role: "assistant",
          content: "",
          attachments: [],
          created_at: new Date(nowMs).toISOString(),
          isStreaming: true,
        },
      });
    } else if (!hasThinking) {
      items.push({ kind: "thinking", key: "__pending__" });
    }
  }
  return items;
}
