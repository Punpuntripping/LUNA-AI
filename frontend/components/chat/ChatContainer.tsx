"use client";

import { useCallback, useState } from "react";
import { X, FileText } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useSendMessage } from "@/hooks/use-chat";
import { useChatStore } from "@/stores/chat-store";
import { useMessages } from "@/hooks/use-messages";
import { useConversationDetail } from "@/hooks/use-conversations";
import { MessageList } from "@/components/chat/MessageList";
import { ChatInput } from "@/components/chat/ChatInput";
import { TemplateCards } from "@/components/chat/TemplateCards";

interface ChatContainerProps {
  conversationId: string;
  className?: string;
}

export function ChatContainer({ conversationId, className }: ChatContainerProps) {
  const { sendMessage, stopStreaming } = useSendMessage();
  const error = useChatStore((s) => s.error);
  const setError = useChatStore((s) => s.setError);
  const isArtifactPanelOpen = useChatStore((s) => s.isArtifactPanelOpen);
  const toggleArtifactPanel = useChatStore((s) => s.toggleArtifactPanel);

  // Fetch conversation detail to get case_id for file uploads
  const { data: convData } = useConversationDetail(conversationId);
  const caseId = convData?.conversation?.case_id ?? null;

  // Check if the conversation has messages (for empty state)
  const { data: messagesData } = useMessages(conversationId);
  const hasMessages =
    (messagesData?.pages?.[0]?.messages?.length ?? 0) > 0;

  // Local state for populating input from template selection
  const [pendingPrompt, setPendingPrompt] = useState<string | null>(null);
  const [showTemplates, setShowTemplates] = useState(false);

  const handleSend = useCallback(
    (content: string) => {
      setPendingPrompt(null);
      void sendMessage({ conversationId, content, caseId });
    },
    [sendMessage, conversationId, caseId]
  );

  const handleTemplateSelect = useCallback((prompt: string) => {
    setPendingPrompt(prompt);
  }, []);

  const handleDismissError = useCallback(() => {
    setError(null);
  }, [setError]);

  // If a template was selected and we have a pending prompt, send it directly
  // We use a callback ref pattern instead: template sets prompt, ChatInput sends
  const handleSendWithTemplate = useCallback(
    (content: string) => {
      handleSend(content);
    },
    [handleSend]
  );

  return (
    <div className={cn("flex flex-col h-full", className)}>
      {/* Header bar with artifact panel toggle */}
      <div
        dir="rtl"
        lang="ar"
        className="flex items-center justify-between border-b px-4 py-2 shrink-0"
      >
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-medium text-muted-foreground">
            المحادثة
          </h2>
        </div>
        <Button
          variant={isArtifactPanelOpen ? "secondary" : "ghost"}
          size="sm"
          onClick={toggleArtifactPanel}
          className="gap-1.5 text-xs"
          aria-label={isArtifactPanelOpen ? "إغلاق لوحة المخرجات" : "فتح لوحة المخرجات"}
        >
          <FileText className="h-4 w-4" />
          <span className="hidden sm:inline">المخرجات</span>
        </Button>
      </div>

      {/* Error banner */}
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

      {/* Message list - grows to fill available space */}
      <MessageList conversationId={conversationId} className="flex-1 min-h-0" />

      {/* Template cards (shown when no messages OR user clicks "قوالبي" from + menu) */}
      {(!hasMessages || showTemplates) && (
        <div dir="rtl" lang="ar" className="px-4 pb-2">
          <div className="flex items-center justify-between mb-3">
            <p className="text-sm text-muted-foreground">
              {!hasMessages ? "ابدأ محادثة جديدة أو اختر من القوالب:" : "اختر من القوالب:"}
            </p>
            {showTemplates && hasMessages && (
              <Button
                variant="ghost"
                size="icon"
                className="h-6 w-6"
                onClick={() => setShowTemplates(false)}
                aria-label="إغلاق"
              >
                <X className="h-3.5 w-3.5" />
              </Button>
            )}
          </div>
          <TemplateCards onSelect={(prompt) => {
            handleTemplateSelect(prompt);
            setShowTemplates(false);
          }} />
        </div>
      )}

      {/* Chat input - sticky at bottom */}
      <ChatInputWithTemplate
        onSend={handleSendWithTemplate}
        onStop={stopStreaming}
        pendingPrompt={pendingPrompt}
        onPromptConsumed={() => setPendingPrompt(null)}
        caseId={caseId}
        onOpenTemplates={() => setShowTemplates((v) => !v)}
      />
    </div>
  );
}

// ==========================================
// ChatInput wrapper that handles pending template prompts
// ==========================================

interface ChatInputWithTemplateProps {
  onSend: (content: string) => void;
  onStop: () => void;
  pendingPrompt: string | null;
  onPromptConsumed: () => void;
  caseId?: string | null;
  onOpenTemplates?: () => void;
}

/**
 * Thin wrapper around ChatInput that auto-sends a pending template prompt.
 * When a user clicks a template card, we set pendingPrompt which triggers
 * an immediate send.
 */
function ChatInputWithTemplate({
  onSend,
  onStop,
  pendingPrompt,
  onPromptConsumed,
  caseId,
  onOpenTemplates,
}: ChatInputWithTemplateProps) {
  // If there's a pending prompt from template selection, send it immediately
  const handleSend = useCallback(
    (content: string) => {
      onSend(content);
    },
    [onSend]
  );

  // Auto-send pending prompt on next render cycle
  const handleRef = useCallback(
    (node: HTMLDivElement | null) => {
      if (node && pendingPrompt) {
        // Use microtask to ensure state updates are flushed
        queueMicrotask(() => {
          onSend(pendingPrompt);
          onPromptConsumed();
        });
      }
    },
    [pendingPrompt, onSend, onPromptConsumed]
  );

  return (
    <div ref={handleRef}>
      <ChatInput onSend={handleSend} onStop={onStop} caseId={caseId} onOpenTemplates={onOpenTemplates} />
    </div>
  );
}
