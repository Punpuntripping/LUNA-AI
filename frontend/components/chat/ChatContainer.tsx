"use client";

import { useCallback } from "react";
import { X, FileText } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useSendMessage } from "@/hooks/use-chat";
import { useChatStore } from "@/stores/chat-store";
import { useConversationDetail } from "@/hooks/use-conversations";
import { MessageList } from "@/components/chat/MessageList";
import { ChatInput } from "@/components/chat/ChatInput";

interface ChatContainerProps {
  conversationId: string;
  className?: string;
}

export function ChatContainer({ conversationId, className }: ChatContainerProps) {
  const { sendMessage, stopStreaming, regenerateMessage, editAndResend, retryMessage } = useSendMessage();
  const error = useChatStore((s) => s.error);
  const setError = useChatStore((s) => s.setError);
  const isWorkspaceOpen = useChatStore((s) => s.workspace.isOpen);
  const toggleWorkspace = useChatStore((s) => s.toggleWorkspace);
  const streamingCitations = useChatStore((s) => s.streamingCitations);

  const { data: convData } = useConversationDetail(conversationId);
  const caseId = convData?.conversation?.case_id ?? null;

  const handleSend = useCallback(
    (content: string) => {
      void sendMessage({ conversationId, content, caseId });
    },
    [sendMessage, conversationId, caseId]
  );

  const handleDismissError = useCallback(() => {
    setError(null);
  }, [setError]);

  const handleRegenerate = useCallback(
    (messageId: string) => {
      void regenerateMessage({ conversationId, messageId, caseId });
    },
    [regenerateMessage, conversationId, caseId]
  );

  const handleEditResend = useCallback(
    (messageId: string, newContent: string) => {
      void editAndResend({ conversationId, messageId, newContent, caseId });
    },
    [editAndResend, conversationId, caseId]
  );

  const handleRetry = useCallback(
    (messageId: string) => {
      void retryMessage({ conversationId, messageId, caseId });
    },
    [retryMessage, conversationId, caseId]
  );

  return (
    <div className={cn("flex flex-col h-full", className)}>
      <div
        dir="rtl"
        lang="ar"
        className="flex items-center justify-between border-b px-4 py-2 shrink-0"
      >
        <h2 className="text-sm font-medium text-muted-foreground">
          المحادثة
        </h2>
        <Button
          variant={isWorkspaceOpen ? "secondary" : "ghost"}
          size="sm"
          onClick={toggleWorkspace}
          className="gap-1.5 text-xs"
          aria-label={isWorkspaceOpen ? "إغلاق لوحة المخرجات" : "فتح لوحة المخرجات"}
        >
          <FileText className="h-4 w-4" />
          <span className="hidden sm:inline">المخرجات</span>
        </Button>
      </div>

      {error && (
        <div
          dir="rtl"
          lang="ar"
          className="flex items-center justify-between gap-2 border-b border-destructive/20 bg-destructive/10 px-4 py-2"
        >
          <p className="text-sm text-destructive">{error}</p>
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6 shrink-0 text-destructive hover:text-destructive"
            onClick={handleDismissError}
            aria-label="إغلاق"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      )}

      <MessageList
        conversationId={conversationId}
        className="flex-1 min-h-0"
        streamingCitations={streamingCitations}
        onRegenerate={handleRegenerate}
        onEditResend={handleEditResend}
        onRetry={handleRetry}
      />

      <ChatInput onSend={handleSend} onStop={stopStreaming} caseId={caseId} />
    </div>
  );
}
