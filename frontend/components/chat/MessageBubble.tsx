"use client";

import { Copy, Check, Bot, FileText, ImageIcon, AlertCircle } from "lucide-react";
import { useState, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { getRelativeTimeAr } from "@/lib/utils";
import { StreamingText } from "@/components/chat/StreamingText";
import { CitationPills } from "@/components/chat/CitationPills";
import type { Message, Citation } from "@/types";

interface MessageBubbleProps {
  message: Message;
  streamingContent?: string;
  citations?: Citation[];
}

export function MessageBubble({
  message,
  streamingContent,
  citations,
}: MessageBubbleProps) {
  const [copied, setCopied] = useState(false);

  const isUser = message.role === "user";
  const isAssistant = message.role === "assistant";
  const isCurrentlyStreaming = message.isStreaming && streamingContent !== undefined;

  const handleCopy = useCallback(async () => {
    const textToCopy = isCurrentlyStreaming
      ? (streamingContent ?? "")
      : message.content;
    try {
      await navigator.clipboard.writeText(textToCopy);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard API may not be available
    }
  }, [isCurrentlyStreaming, streamingContent, message.content]);

  const displayContent = isCurrentlyStreaming ? streamingContent : message.content;

  return (
    <div
      dir="rtl"
      lang="ar"
      className={cn(
        "flex w-full mb-4 group",
        isUser ? "justify-start" : "justify-end"
      )}
    >
      <div
        className={cn(
          "relative max-w-[85%] sm:max-w-[75%] rounded-2xl px-4 py-3",
          isUser && "bg-primary/10 text-foreground",
          isAssistant && "bg-card border shadow-sm text-foreground",
          message.isFailed && "border-destructive border-2",
          message.isOptimistic && !message.isFailed && "opacity-70"
        )}
      >
        {/* Assistant model badge */}
        {isAssistant && !isCurrentlyStreaming && message.model && (
          <div className="flex items-center gap-1 mb-1.5">
            <Bot className="h-3 w-3 text-muted-foreground" />
            <span className="text-[10px] text-muted-foreground">
              {message.model}
            </span>
          </div>
        )}

        {/* Message content */}
        {isCurrentlyStreaming ? (
          <StreamingText content={streamingContent ?? ""} />
        ) : (
          <div className="whitespace-pre-wrap text-sm leading-relaxed">
            {displayContent}
          </div>
        )}

        {/* Failed message indicator */}
        {message.isFailed && (
          <div className="flex items-center gap-1.5 mt-2 text-destructive">
            <AlertCircle className="h-3.5 w-3.5" />
            <span className="text-xs">فشل إرسال الرسالة</span>
          </div>
        )}

        {/* Attachments */}
        {message.attachments.length > 0 && (
          <div className="flex flex-wrap gap-2 mt-2">
            {message.attachments.map((att) => (
              <div
                key={att.id}
                className="flex items-center gap-1.5 rounded-md bg-muted/50 px-2 py-1"
              >
                {att.attachment_type === "image" ? (
                  <ImageIcon className="h-3.5 w-3.5 text-muted-foreground" />
                ) : (
                  <FileText className="h-3.5 w-3.5 text-muted-foreground" />
                )}
                <span className="text-[11px] text-muted-foreground truncate max-w-[120px]">
                  {att.filename}
                </span>
              </div>
            ))}
          </div>
        )}

        {/* Citations (assistant only) */}
        {isAssistant && !isCurrentlyStreaming && citations && citations.length > 0 && (
          <CitationPills citations={citations} />
        )}

        {/* Timestamp + copy button row */}
        <div className="flex items-center justify-between mt-2 gap-2">
          <span className="text-[10px] text-muted-foreground select-none">
            {getRelativeTimeAr(message.created_at)}
          </span>

          {!isCurrentlyStreaming && (
            <Button
              variant="ghost"
              size="icon"
              className="h-6 w-6 opacity-0 group-hover:opacity-100 transition-opacity"
              onClick={handleCopy}
              aria-label="نسخ"
            >
              {copied ? (
                <Check className="h-3 w-3 text-green-600" />
              ) : (
                <Copy className="h-3 w-3 text-muted-foreground" />
              )}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
