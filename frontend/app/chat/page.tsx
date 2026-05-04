"use client";

import { useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useCreateConversation } from "@/hooks/use-conversations";
import { useSidebarStore } from "@/stores/sidebar-store";
import { useChatStore } from "@/stores/chat-store";
import { ChatInput } from "@/components/chat/ChatInput";

// eslint-disable-next-line import/no-default-export
export default function ChatEmptyPage() {
  const router = useRouter();
  const createConversation = useCreateConversation();
  const [isCreating, setIsCreating] = useState(false);

  const handleSend = useCallback(
    (content: string) => {
      if (isCreating) return;
      setIsCreating(true);

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
      <div className="flex flex-1 flex-col items-center justify-center px-4 text-center">
        <div className="mx-auto flex h-20 w-20 items-center justify-center rounded-2xl bg-primary text-primary-foreground text-3xl font-bold mb-6">
          لونا
        </div>

        <h1 className="text-2xl font-bold text-foreground mb-2">
          مرحبا بك في لونا
        </h1>
        <p className="text-muted-foreground text-sm mb-8 max-w-md">
          المساعد القانوني الذكي المتخصص في الأنظمة السعودية. اطرح أسئلتك
          القانونية واحصل على إجابات دقيقة مدعومة بالمصادر.
        </p>
      </div>

      <ChatInput onSend={handleSend} disabled={isCreating} />
    </div>
  );
}
