import { ChatLayoutClient } from "@/components/chat/ChatLayoutClient";

// Next.js App Router requires default export for layout files
// eslint-disable-next-line import/no-default-export
export default function ChatLayout({ children }: { children: React.ReactNode }) {
  return <ChatLayoutClient>{children}</ChatLayoutClient>;
}
