"use client";

import { useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useCreateConversation } from "@/hooks/use-conversations";
import { useSidebarStore } from "@/stores/sidebar-store";
import { useChatStore } from "@/stores/chat-store";
import { ChatInput } from "@/components/chat/ChatInput";
import { TemplateCards } from "@/components/chat/TemplateCards";
import { AgentSelector } from "@/components/chat/AgentSelector";

// Next.js App Router requires default export for page files
// eslint-disable-next-line import/no-default-export
export default function ChatEmptyPage() {
  const router = useRouter();
  const createConversation = useCreateConversation();
  const [isCreating, setIsCreating] = useState(false);

  const handleSend = useCallback(
    (content: string) => {
      if (isCreating) return;
      setIsCreating(true);

      // Store the message so the conversation page can auto-send it
      useChatStore.getState().setPendingMessage(content);

      createConversation.mutate(
        { case_id: null },
        {
          onSuccess: (data) => {
            const newId = data.conversation.conversation_id;
            useSidebarStore.getState().setSelectedConversation(newId);
            router.replace(`/chat/${newId}`);
          },
          onError: () => {
            useChatStore.getState().clearPendingMessage();
            setIsCreating(false);
          },
        }
      );
    },
    [isCreating, createConversation, router]
  );

  return (
    <div className="flex flex-1 flex-col h-full">
      {/* Welcome area - centered vertically in remaining space */}
      <div className="flex flex-1 flex-col items-center justify-center px-4 text-center">
        {/* Luna Logo */}
        <div className="mx-auto flex h-20 w-20 items-center justify-center rounded-2xl bg-primary text-primary-foreground text-3xl font-bold mb-6">
          لونا
        </div>

        {/* Welcome text */}
        <h1 className="text-2xl font-bold text-foreground mb-2">
          مرحبا بك في لونا
        </h1>
        <p className="text-muted-foreground text-sm mb-8 max-w-md">
          المساعد القانوني الذكي المتخصص في الأنظمة السعودية. اطرح أسئلتك
          القانونية واحصل على إجابات دقيقة مدعومة بالمصادر.
        </p>

        {/* Template cards */}
        <div className="max-w-2xl mx-auto w-full px-4 mb-6">
          <TemplateCards onSelect={handleSend} />
        </div>
      </div>

      {/* Agent selector + Chat input - sticky at bottom */}
      <div className="max-w-3xl mx-auto w-full px-4 pt-2">
        <AgentSelector />
      </div>
      <ChatInput onSend={handleSend} disabled={isCreating} />
    </div>
  );
}
