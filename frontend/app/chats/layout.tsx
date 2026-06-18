import { ChatLayoutClient } from "@/components/chat/ChatLayoutClient";

// Next.js App Router requires default export for layout files. Reuse the chat
// shell so /chats renders the same sidebar; the workspace pane only mounts on
// /chat/[id], so /chats shows sidebar + page cleanly.
// eslint-disable-next-line import/no-default-export
export default function ChatsLayout({ children }: { children: React.ReactNode }) {
  return <ChatLayoutClient>{children}</ChatLayoutClient>;
}
