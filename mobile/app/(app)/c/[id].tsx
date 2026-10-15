import { Redirect, useLocalSearchParams } from "expo-router";

import { ChatScreen } from "@/components/chat/ChatScreen";

// A conversation (deep-linkable: rayhan://c/{id} — Phase 3 push taps land here).
export default function ConversationScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  if (typeof id !== "string" || !id) return <Redirect href="/" />;
  // key: a new id is a new screen state (composer draft, scroll, sheets).
  return <ChatScreen key={id} conversationId={id} />;
}
