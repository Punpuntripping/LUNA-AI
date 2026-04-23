"use client";

import { useEffect, useRef } from "react";
import { useParams } from "next/navigation";
import { useSidebarStore } from "@/stores/sidebar-store";
import { useChatStore } from "@/stores/chat-store";
import { useSendMessage } from "@/hooks/use-chat";
import { ChatContainer } from "@/components/chat/ChatContainer";

// Next.js App Router requires default export for page files
// eslint-disable-next-line import/no-default-export
export default function ConversationPage() {
  const params = useParams<{ id: string }>();
  const conversationId = params.id;
  const { setSelectedConversation } = useSidebarStore();
  const { sendMessage } = useSendMessage();
  const pendingMessageConsumed = useRef(false);

  // Sync sidebar selection with route param
  useEffect(() => {
    if (conversationId) {
      setSelectedConversation(conversationId);
    }
  }, [conversationId, setSelectedConversation]);

  // Reset consumed flag and agent selector when conversation changes so the
  // ref doesn't stick from a previous conversation (React may reuse this component instance).
  useEffect(() => {
    pendingMessageConsumed.current = false;
    // Reset agent selector to auto on conversation switch
    useChatStore.getState().setSelectedAgent(null);
  }, [conversationId]);

  // Consume pending message from chat store (set by empty chat page)
  useEffect(() => {
    if (!conversationId || pendingMessageConsumed.current) return;

    const pendingMessage = useChatStore.getState().pendingMessage;
    if (pendingMessage) {
      pendingMessageConsumed.current = true;
      useChatStore.getState().clearPendingMessage();
      void sendMessage({ conversationId, content: pendingMessage });
    }
  }, [conversationId, sendMessage]);

  if (!conversationId) return null;

  return <ChatContainer conversationId={conversationId} />;
}
