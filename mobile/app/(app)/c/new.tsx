import { ChatScreen } from "@/components/chat/ChatScreen";

// «محادثة جديدة» — nothing exists server-side until the first send.
export default function NewConversationScreen() {
  return <ChatScreen conversationId={null} />;
}
