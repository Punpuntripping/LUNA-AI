import { ChatLayoutClient } from "@/components/chat/ChatLayoutClient";

// «رسائل ريحان» — same chat shell as /chats (sidebar + page, no workspace pane).
// eslint-disable-next-line import/no-default-export
export default function MessagesLayout({ children }: { children: React.ReactNode }) {
  return <ChatLayoutClient>{children}</ChatLayoutClient>;
}
