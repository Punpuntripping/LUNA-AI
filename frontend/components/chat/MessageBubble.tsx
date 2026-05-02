"use client";

import {
  Copy,
  Check,
  Bot,
  FileText,
  ImageIcon,
  AlertCircle,
  RefreshCw,
  Pencil,
  ThumbsUp,
  ThumbsDown,
  HelpCircle,
} from "lucide-react";
import { useState, useCallback, useRef, useEffect, type KeyboardEvent } from "react";
import TextareaAutosize from "react-textarea-autosize";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
  TooltipProvider,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { getRelativeTimeAr } from "@/lib/utils";
import { StreamingText } from "@/components/chat/StreamingText";
import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";
import { CitationPills } from "@/components/chat/CitationPills";
import type { Message, Citation } from "@/types";

type FeedbackState = "none" | "up" | "down";

interface MessageBubbleProps {
  message: Message;
  streamingContent?: string;
  citations?: Citation[];
  /** Called when user clicks Regenerate on an assistant message */
  onRegenerate?: (messageId: string) => void;
  /** Called when user edits their own message and clicks Save & Send */
  onEditResend?: (messageId: string, newContent: string) => void;
  /** Called when user clicks Retry on a failed message */
  onRetry?: (messageId: string) => void;
}

export function MessageBubble({
  message,
  streamingContent,
  citations,
  onRegenerate,
  onEditResend,
  onRetry,
}: MessageBubbleProps) {
  const [copied, setCopied] = useState(false);
  const [feedback, setFeedback] = useState<FeedbackState>("none");
  const [isEditing, setIsEditing] = useState(false);
  const [editContent, setEditContent] = useState("");
  const editTextareaRef = useRef<HTMLTextAreaElement>(null);

  const isUser = message.role === "user";
  const isCurrentlyStreaming = message.isStreaming && streamingContent !== undefined;
  const isCompleted = !isCurrentlyStreaming && !message.isOptimistic;
  const metadataKind = message.metadata?.kind;
  const isAgentQuestion = metadataKind === "agent_question";
  const isAgentAnswer = metadataKind === "agent_answer";
  const agentSuggestions = isAgentQuestion ? message.metadata?.suggestions : undefined;

  // Focus the textarea when entering edit mode
  useEffect(() => {
    if (isEditing && editTextareaRef.current) {
      editTextareaRef.current.focus();
      const len = editTextareaRef.current.value.length;
      editTextareaRef.current.setSelectionRange(len, len);
    }
  }, [isEditing]);

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

  const handleRegenerate = useCallback(() => {
    onRegenerate?.(message.message_id);
  }, [onRegenerate, message.message_id]);

  const handleRetry = useCallback(() => {
    onRetry?.(message.message_id);
  }, [onRetry, message.message_id]);

  const handleFeedback = useCallback((type: "up" | "down") => {
    setFeedback((prev) => (prev === type ? "none" : type));
  }, []);

  const handleStartEdit = useCallback(() => {
    setEditContent(message.content);
    setIsEditing(true);
  }, [message.content]);

  const handleCancelEdit = useCallback(() => {
    setIsEditing(false);
    setEditContent("");
  }, []);

  const handleSaveEdit = useCallback(() => {
    const trimmed = editContent.trim();
    if (!trimmed || trimmed === message.content) {
      handleCancelEdit();
      return;
    }
    onEditResend?.(message.message_id, trimmed);
    setIsEditing(false);
    setEditContent("");
  }, [editContent, message.content, message.message_id, onEditResend, handleCancelEdit]);

  const handleEditKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSaveEdit();
      }
      if (e.key === "Escape") {
        handleCancelEdit();
      }
    },
    [handleSaveEdit, handleCancelEdit]
  );

  const displayContent = isCurrentlyStreaming ? streamingContent : message.content;

  // ==========================================================================
  // USER MESSAGE — prose style, no bubble
  // ==========================================================================
  if (isUser) {
    // TODO: wire actual user display name when available
    const userName = "أنت";
    const avatarLetter = userName.charAt(0) || "أ";

    return (
      <TooltipProvider delayDuration={300}>
        <div
          dir="rtl"
          lang="ar"
          className={cn(
            "flex flex-col gap-1.5 mb-5 group/bubble",
            message.isOptimistic && !message.isFailed && "opacity-70"
          )}
        >
          {/* Header row: avatar + name + timestamp */}
          <div className="flex items-center gap-2.5">
            <div className="h-7 w-7 bg-muted text-muted-foreground rounded-full flex items-center justify-center text-xs font-semibold shrink-0">
              {avatarLetter}
            </div>
            <span className="text-[13px] font-semibold text-foreground">
              {userName}
            </span>
            {isAgentAnswer && (
              <span className="text-[10px] font-medium text-muted-foreground bg-muted/60 rounded px-1.5 py-0.5 select-none">
                (جواب)
              </span>
            )}
            <span className="text-[11px] text-muted-foreground ms-auto">
              {getRelativeTimeAr(message.created_at)}
            </span>
          </div>

          {/* Body / edit mode — indented to align under the name */}
          {isEditing ? (
            <div className="ps-[38px] space-y-2">
              <TextareaAutosize
                ref={editTextareaRef}
                dir="rtl"
                lang="ar"
                value={editContent}
                onChange={(e) => setEditContent(e.target.value)}
                onKeyDown={handleEditKeyDown}
                minRows={1}
                maxRows={6}
                className={cn(
                  "w-full resize-none bg-background rounded-lg border px-3 py-2 text-sm",
                  "placeholder:text-muted-foreground",
                  "focus:outline-none focus:ring-2 focus:ring-primary/40"
                )}
              />
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  className="h-7 text-xs px-3"
                  onClick={handleSaveEdit}
                  disabled={!editContent.trim()}
                >
                  حفظ وإرسال
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 text-xs px-3"
                  onClick={handleCancelEdit}
                >
                  إلغاء
                </Button>
              </div>
            </div>
          ) : isCurrentlyStreaming ? (
            <div className="ps-[38px] text-sm leading-[1.75] text-foreground">
              <StreamingText content={streamingContent ?? ""} />
            </div>
          ) : (
            <div className="ps-[38px] text-sm leading-[1.75] text-foreground whitespace-pre-wrap">
              {displayContent}
            </div>
          )}

          {/* Failed indicator + retry */}
          {message.isFailed && (
            <div className="ps-[38px] flex items-center gap-2">
              <AlertCircle className="h-3.5 w-3.5 text-destructive shrink-0" />
              <span className="text-xs text-destructive">فشل إرسال الرسالة</span>
              <Button
                variant="outline"
                size="sm"
                className="h-6 text-xs px-2 gap-1 border-destructive/50 text-destructive hover:text-destructive hover:bg-destructive/10 ms-auto"
                onClick={handleRetry}
              >
                <RefreshCw className="h-3 w-3" />
                إعادة المحاولة
              </Button>
            </div>
          )}

          {/* Attachments */}
          {message.attachments.length > 0 && (
            <div className="ps-[38px] flex flex-wrap gap-2">
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

          {/* Action bar */}
          {isCompleted && !message.isFailed && !isEditing && (
            <div
              className={cn(
                "ps-[38px] flex items-center gap-0.5",
                "opacity-0 group-hover/bubble:opacity-100 transition-opacity duration-200",
                "max-sm:opacity-100"
              )}
            >
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 text-muted-foreground hover:text-foreground"
                    onClick={handleCopy}
                    aria-label="نسخ"
                  >
                    {copied ? (
                      <Check className="h-3.5 w-3.5 text-green-600" />
                    ) : (
                      <Copy className="h-3.5 w-3.5" />
                    )}
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="bottom">
                  <p className="text-xs">{copied ? "تم النسخ" : "نسخ"}</p>
                </TooltipContent>
              </Tooltip>

              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 text-muted-foreground hover:text-foreground"
                    onClick={handleStartEdit}
                    aria-label="تعديل"
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="bottom">
                  <p className="text-xs">تعديل</p>
                </TooltipContent>
              </Tooltip>
            </div>
          )}
        </div>
      </TooltipProvider>
    );
  }

  // ==========================================================================
  // ASSISTANT MESSAGE — bubble, RTL start-aligned (right edge)
  // ==========================================================================
  return (
    <TooltipProvider delayDuration={300}>
      <div
        dir="rtl"
        lang="ar"
        className="flex w-full mb-3.5 justify-start group/bubble"
      >
        <div
          className={cn(
            "relative max-w-[85%] rounded-2xl border bg-card px-4 py-3 shadow-sm text-foreground text-sm leading-[1.75]",
            message.isFailed && "border-destructive border-2",
            isAgentQuestion &&
              "border-primary/40 bg-primary/[0.04] border-r-4 border-r-primary/70",
            message.isOptimistic && !message.isFailed && "opacity-70"
          )}
        >
          {/* Agent question header */}
          {isAgentQuestion && (
            <div className="flex items-center gap-1.5 mb-1.5">
              <HelpCircle className="h-3.5 w-3.5 text-primary" />
              <span className="text-[11px] font-semibold text-primary">
                السؤال
              </span>
            </div>
          )}

          {/* Model badge */}
          {!isCurrentlyStreaming && !isAgentQuestion && message.model && (
            <div className="flex items-center gap-1 mb-1.5">
              <Bot className="h-3 w-3 text-muted-foreground" />
              <span className="text-[10px] text-muted-foreground">
                {message.model}
              </span>
            </div>
          )}

          {/* Content */}
          {isCurrentlyStreaming ? (
            <StreamingText content={streamingContent ?? ""} />
          ) : (
            <MarkdownRenderer content={displayContent ?? ""} />
          )}

          {/* Agent question suggestions (read-only chips — the user types their reply
              into the normal chat input; clicking a chip is a future enhancement) */}
          {isAgentQuestion && agentSuggestions && agentSuggestions.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2.5">
              {agentSuggestions.map((s, i) => (
                <span
                  key={i}
                  className="text-[11px] text-muted-foreground bg-muted/60 rounded-full px-2.5 py-1"
                >
                  {s}
                </span>
              ))}
            </div>
          )}

          {/* Failed indicator + retry */}
          {message.isFailed && (
            <div className="flex items-center gap-2 mt-2">
              <AlertCircle className="h-3.5 w-3.5 text-destructive shrink-0" />
              <span className="text-xs text-destructive">فشل إرسال الرسالة</span>
              <Button
                variant="outline"
                size="sm"
                className="h-6 text-xs px-2 gap-1 border-destructive/50 text-destructive hover:text-destructive hover:bg-destructive/10 ms-auto"
                onClick={handleRetry}
              >
                <RefreshCw className="h-3 w-3" />
                إعادة المحاولة
              </Button>
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

          {/* Citations */}
          {!isCurrentlyStreaming && citations && citations.length > 0 && (
            <CitationPills citations={citations} />
          )}

          {/* Action bar */}
          {isCompleted && !message.isFailed && (
            <div
              className={cn(
                "flex items-center gap-0.5 mt-2",
                "opacity-0 group-hover/bubble:opacity-100 transition-opacity duration-200",
                "max-sm:opacity-100"
              )}
            >
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 text-muted-foreground hover:text-foreground"
                    onClick={handleCopy}
                    aria-label="نسخ"
                  >
                    {copied ? (
                      <Check className="h-3.5 w-3.5 text-green-600" />
                    ) : (
                      <Copy className="h-3.5 w-3.5" />
                    )}
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="bottom">
                  <p className="text-xs">{copied ? "تم النسخ" : "نسخ"}</p>
                </TooltipContent>
              </Tooltip>

              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 text-muted-foreground hover:text-foreground"
                    onClick={handleRegenerate}
                    aria-label="إعادة التوليد"
                  >
                    <RefreshCw className="h-3.5 w-3.5" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="bottom">
                  <p className="text-xs">إعادة التوليد</p>
                </TooltipContent>
              </Tooltip>

              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className={cn(
                      "h-7 w-7",
                      feedback === "up"
                        ? "text-primary"
                        : "text-muted-foreground hover:text-foreground"
                    )}
                    onClick={() => handleFeedback("up")}
                    aria-label="إعجاب"
                  >
                    <ThumbsUp
                      className={cn(
                        "h-3.5 w-3.5",
                        feedback === "up" && "fill-primary"
                      )}
                    />
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="bottom">
                  <p className="text-xs">إعجاب</p>
                </TooltipContent>
              </Tooltip>

              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className={cn(
                      "h-7 w-7",
                      feedback === "down"
                        ? "text-destructive"
                        : "text-muted-foreground hover:text-foreground"
                    )}
                    onClick={() => handleFeedback("down")}
                    aria-label="عدم إعجاب"
                  >
                    <ThumbsDown
                      className={cn(
                        "h-3.5 w-3.5",
                        feedback === "down" && "fill-destructive"
                      )}
                    />
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="bottom">
                  <p className="text-xs">عدم إعجاب</p>
                </TooltipContent>
              </Tooltip>
            </div>
          )}

          {/* Timestamp row */}
          {(isCurrentlyStreaming || message.isFailed) && (
            <div className="flex items-center mt-2">
              <span className="text-[10px] text-muted-foreground select-none">
                {getRelativeTimeAr(message.created_at)}
              </span>
            </div>
          )}

          {isCompleted && !message.isFailed && (
            <div className="flex items-center mt-1">
              <span className="text-[10px] text-muted-foreground select-none">
                {getRelativeTimeAr(message.created_at)}
              </span>
            </div>
          )}
        </div>
      </div>
    </TooltipProvider>
  );
}
