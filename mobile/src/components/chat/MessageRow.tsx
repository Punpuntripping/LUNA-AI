import { memo, useMemo, type ReactNode } from "react";
import { StyleSheet, View } from "react-native";

import type { Message } from "@contracts";

import type { ChatItem } from "@/chat/chat-items";
import { useStreamContent } from "@/chat/stream-store";

import { AssistantBody } from "./AssistantBody";
import { useChatRow } from "./ChatContext";
import { FailedBubble } from "./FailedBubble";
import { Thinking } from "./Thinking";
import { UserBubble } from "./UserBubble";

// Renders one item of `buildChatItems` (src/chat/chat-items.ts). Only the
// `live` row subscribes to the token stream, so settled rows never re-render
// while an answer streams (X4).

export const MessageRow = memo(function MessageRow({ item }: { item: ChatItem }) {
  const { regenerate } = useChatRow();
  let body: ReactNode;
  switch (item.kind) {
    case "user":
      body = <UserBubble message={item.message} />;
      break;
    case "assistant":
      body = <AssistantBody message={item.message} isLatest={item.isLatest} />;
      break;
    case "live":
      body = <LiveAssistantRow message={item.message} />;
      break;
    case "thinking":
      body = <ThinkingRow />;
      break;
    case "failed":
      body = <FailedBubble createdAt={item.message.created_at} onRetry={() => regenerate(item.message.message_id)} />;
      break;
  }
  return <View style={styles.row}>{body}</View>;
});

function ThinkingRow() {
  const { conversationId } = useChatRow();
  return (
    <View style={styles.thinking}>
      <Thinking conversationId={conversationId} />
    </View>
  );
}

/** The sole subscriber of the token stream (re-renders every ~80 ms flush). */
function LiveAssistantRow({ message }: { message: Message }) {
  const { conversationId } = useChatRow();
  const content = useStreamContent(conversationId);
  const live = useMemo(() => ({ ...message, isStreaming: true }), [message]);
  if (!content) return <ThinkingRow />;
  return <AssistantBody message={live} streamingContent={content} />;
}

const styles = StyleSheet.create({
  row: { paddingHorizontal: 16 },
  thinking: { marginBottom: 28 },
});
