"use client";

import {
  Copy,
  Check,
  Bot,
  FileText,
  FileSearch,
  ImageIcon,
  AlertCircle,
  RefreshCw,
  Pencil,
  ThumbsUp,
  ThumbsDown,
  HelpCircle,
  CornerUpLeft,
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { getRelativeTimeAr } from "@/lib/utils";
import { StreamingText } from "@/components/chat/StreamingText";
import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";
import { TemplateSaveOfferChip } from "@/components/chat/TemplateSaveOfferChip";
import type { Attachment, Message, WorkspaceItemKind } from "@/types";

type FeedbackState = "none" | "up" | "down";

interface MessageBubbleProps {
  message: Message;
  streamingContent?: string;
  /** Called when user clicks Regenerate on an assistant message */
  onRegenerate?: (messageId: string) => void;
  /** Called when user edits their own message and clicks Save & Send */
  onEditResend?: (messageId: string, newContent: string) => void;
  /** Called when user clicks Retry on a failed message */
  onRetry?: (messageId: string) => void;
  /**
   * Workspace item ids associated with this assistant message (Window C).
   * When non-empty an inline "المصدر" chip renders next to the model badge.
   * Passed through unchanged for user / streaming bubbles where it is
   * always undefined.
   */
  artifactIds?: string[] | null;
  /** Resolve ``artifactIds[i]`` to its workspace_item kind + title. */
  artifactLookup?: Record<string, { kind: WorkspaceItemKind; title: string }>;
  /** Open a workspace item in the pane (used by chip click). */
  onOpenArtifact?: (itemId: string) => void;
  /** Open the message's first ``agent_search`` artifact at reference ``n``. */
  onCitationClick?: (n: number) => void;
  /**
   * Phase E (full_redesign §9 O5): workspace_item ids the planner flagged
   * as "already covers this question" for this assistant message. When
   * non-empty a chip renders below the model badge ("راجع البطاقة
   * السابقة") that jumps to the existing card in the workspace pane.
   * Sourced from ``chat-store.referencedItemsByMessage`` so it survives
   * the messages-cache invalidate at stream completion.
   */
  referencedItemIds?: string[];
  /** Open + highlight a referenced workspace_item (chip click). */
  onJumpToReferencedItem?: (itemId: string) => void;
  /**
   * Wave E (writer_planner_user_templates §D6): the "save attachment as
   * template" offer the writer pipeline emitted at the end of this assistant
   * turn. When present an inline «احفظ المرفق كقالب؟ [نعم]» chip renders below
   * the bubble body. Sourced from ``chat-store.templateOffersByMessage`` so it
   * survives the post-stream messages-cache invalidate. Undefined for user /
   * streaming / non-writing bubbles.
   */
  templateOffer?: { itemId: string; titleHint: string };
}

export function MessageBubble({
  message,
  streamingContent,
  onRegenerate,
  onEditResend,
  onRetry,
  artifactIds,
  artifactLookup,
  onOpenArtifact,
  onCitationClick,
  referencedItemIds,
  onJumpToReferencedItem,
  templateOffer,
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
  // Window C: assistant messages whose agent run produced one or more
  // workspace_items get an inline source chip + clickable citations.
  // Defensive check — backend may not yet populate this field.
  const hasArtifacts =
    !isUser &&
    !isAgentQuestion &&
    Array.isArray(artifactIds) &&
    artifactIds.length > 0;
  // Phase E (§9 O5): planner referenced a prior artifact instead of
  // publishing a new card; render a "go to prior card" chip below the
  // bubble body.
  const hasReferencedItems =
    !isUser &&
    !isAgentQuestion &&
    Array.isArray(referencedItemIds) &&
    referencedItemIds.length > 0;
  // Wave E: writer pipeline offered to save an attached doc as a قوالبي
  // template. Assistant bubbles only, and never on the agent-question bubble.
  const hasTemplateOffer =
    !isUser &&
    !isAgentQuestion &&
    templateOffer !== undefined &&
    !!templateOffer.itemId;

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
          <AttachmentList
            attachments={message.attachments}
            artifactLookup={artifactLookup}
            onOpenArtifact={onOpenArtifact}
            className="ps-[38px]"
          />

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
                      <Check className="h-3.5 w-3.5 text-success-fg" />
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

          {/* Model badge + optional artifact chip */}
          {!isCurrentlyStreaming && !isAgentQuestion && (message.model || hasArtifacts) && (
            <div className="flex items-center gap-2 mb-1.5">
              {message.model && (
                <div className="flex items-center gap-1">
                  <Bot className="h-3 w-3 text-muted-foreground" />
                  <span className="text-[10px] text-muted-foreground">
                    {message.model}
                  </span>
                </div>
              )}
              {hasArtifacts && (
                <ArtifactChip
                  artifactIds={artifactIds!}
                  artifactLookup={artifactLookup}
                  onOpenArtifact={onOpenArtifact}
                />
              )}
            </div>
          )}

          {/* Content */}
          {isCurrentlyStreaming ? (
            <StreamingText content={streamingContent ?? ""} />
          ) : (
            <MarkdownRenderer
              content={displayContent ?? ""}
              onCitationClick={hasArtifacts ? onCitationClick : undefined}
            />
          )}

          {/* Phase E (§9 O5): chip(s) for prior cards the planner referenced
              instead of publishing a new one. Stays hidden during streaming
              — the SSE handler attaches ids to the assistant message_id, and
              once `done` fires the bubble re-renders with the chip visible. */}
          {!isCurrentlyStreaming && hasReferencedItems && (
            <div className="flex flex-wrap gap-1.5 mt-2.5">
              {referencedItemIds!.map((id) => (
                <ReferencedItemChip
                  key={id}
                  itemId={id}
                  label={artifactLookup?.[id]?.title}
                  onJump={onJumpToReferencedItem}
                />
              ))}
            </div>
          )}

          {/* Wave E (writer_planner_user_templates §D6): "save attachment as
              template" chip. Like the referenced-items chip it stays hidden
              during streaming and appears once the turn settles — the SSE
              ``template_save_offer`` event attaches the offer to the assistant
              message_id and the bubble re-renders with the chip visible. */}
          {!isCurrentlyStreaming && hasTemplateOffer && (
            <div className="flex flex-wrap gap-1.5 mt-2.5">
              <TemplateSaveOfferChip
                itemId={templateOffer!.itemId}
                titleHint={templateOffer!.titleHint}
              />
            </div>
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
          <AttachmentList
            attachments={message.attachments}
            artifactLookup={artifactLookup}
            onOpenArtifact={onOpenArtifact}
            className="mt-2"
          />

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
                      <Check className="h-3.5 w-3.5 text-success-fg" />
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

// ============================================================================
// Artifact chip (Window C)
// ============================================================================

interface ArtifactChipProps {
  artifactIds: string[];
  artifactLookup?: Record<string, { kind: WorkspaceItemKind; title: string }>;
  onOpenArtifact?: (itemId: string) => void;
}

/**
 * Inline "المصدر" chip rendered next to the model badge on assistant
 * bubbles that produced at least one workspace_item.
 *
 * - One artifact → a single ghost button. Click opens it in the pane.
 * - Multiple artifacts → a DropdownMenu trigger; clicking a row opens that id.
 *
 * If ``artifactLookup`` is missing or doesn't yet include an id (race with
 * the workspace list query), the row falls back to a generic "مصدر" label.
 */
function ArtifactChip({
  artifactIds,
  artifactLookup,
  onOpenArtifact,
}: ArtifactChipProps) {
  if (artifactIds.length === 0) return null;

  const baseButtonClass = cn(
    "h-6 gap-1 px-2 text-[10px] text-muted-foreground hover:text-foreground",
    "rounded-full bg-muted/40 hover:bg-muted/70 transition-colors",
  );

  if (artifactIds.length === 1) {
    const id = artifactIds[0];
    return (
      <Button
        variant="ghost"
        size="sm"
        className={baseButtonClass}
        onClick={() => onOpenArtifact?.(id)}
        aria-label="فتح المصدر"
      >
        <FileSearch className="h-3 w-3" />
        المصدر
      </Button>
    );
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className={baseButtonClass}
          aria-label="فتح المصادر"
        >
          <FileSearch className="h-3 w-3" />
          المصدر ({artifactIds.length})
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-[220px]">
        {artifactIds.map((id) => {
          const entry = artifactLookup?.[id];
          const label = entry?.title || "مصدر";
          return (
            <DropdownMenuItem
              key={id}
              onClick={() => onOpenArtifact?.(id)}
              className="text-xs"
            >
              <FileSearch className="h-3 w-3 me-1.5 shrink-0 text-muted-foreground" />
              <span className="truncate">{label}</span>
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// ============================================================================
// Referenced item chip (Phase E — full_redesign §9 O5)
// ============================================================================

interface ReferencedItemChipProps {
  itemId: string;
  /** Optional resolved title from the workspace list cache. */
  label?: string;
  onJump?: (itemId: string) => void;
}

/**
 * "راجع البطاقة السابقة" chip — rendered on an assistant bubble when the
 * planner's responder set ``build_artifact=False`` + ``referenced_item_id``
 * (no new card published). Clicking jumps to and highlights the referenced
 * card in the workspace pane.
 *
 * Subtle outline style — distinct from the inline ``ArtifactChip`` so the
 * user can tell at a glance that "this conversation re-used a prior card"
 * vs "this turn produced its own card".
 */
function ReferencedItemChip({ itemId, label, onJump }: ReferencedItemChipProps) {
  const labelText = label ? `راجع: ${label}` : "راجع البطاقة السابقة";
  return (
    <Button
      variant="outline"
      size="sm"
      className={cn(
        "h-7 gap-1.5 px-2.5 text-[11px]",
        "rounded-full border-border/70 text-muted-foreground hover:text-foreground",
        "hover:bg-accent/40 transition-colors",
      )}
      onClick={() => onJump?.(itemId)}
      aria-label="فتح البطاقة السابقة"
    >
      <CornerUpLeft className="h-3 w-3" />
      <span className="truncate max-w-[280px]">{labelText}</span>
    </Button>
  );
}

// ============================================================================
// Attachment chips
// ============================================================================

interface AttachmentListProps {
  attachments: Attachment[];
  /** Resolves ``attachment.document_id`` → workspace_item {kind, title}. */
  artifactLookup?: Record<string, { kind: WorkspaceItemKind; title: string }>;
  /** Open the attachment's workspace_item in the pane (chip click). */
  onOpenArtifact?: (itemId: string) => void;
  className?: string;
}

/**
 * Renders a message's attachments as chips.
 *
 * A chat attachment is a ``workspace_items`` row (``kind='attachment'``); the
 * message's ``attachment.document_id`` is that item's ``item_id``. When the
 * id resolves in ``artifactLookup`` (i.e. it's a live workspace item) the chip
 * becomes a button that opens it in the pane via ``onOpenArtifact`` — the same
 * AttachmentRenderer the workspace list uses. The lookup also supplies the
 * title, which fixes the empty filename the messages API returns for
 * workspace-item attachments (its join targets ``case_documents``, which a
 * chat attachment has no row in).
 */
function AttachmentList({
  attachments,
  artifactLookup,
  onOpenArtifact,
  className,
}: AttachmentListProps) {
  if (attachments.length === 0) return null;
  return (
    <div className={cn("flex flex-wrap gap-2", className)}>
      {attachments.map((att) => (
        <AttachmentChip
          key={att.id}
          attachment={att}
          // Title from the workspace-list cache when present (fixes the empty
          // filename the messages API returns for workspace-item attachments);
          // a fresh same-session attachment falls back to its own filename.
          resolvedTitle={artifactLookup?.[att.document_id]?.title}
          // NOT gated on artifactLookup: opening fetches the item by id, so a
          // just-uploaded attachment is clickable immediately — not only after
          // the list cache refreshes on a full reload (the "per new sign-in"
          // bug). Every message attachment is a workspace item.
          onOpen={onOpenArtifact}
        />
      ))}
    </div>
  );
}

interface AttachmentChipProps {
  attachment: Attachment;
  resolvedTitle?: string;
  onOpen?: (itemId: string) => void;
}

function AttachmentChip({
  attachment,
  resolvedTitle,
  onOpen,
}: AttachmentChipProps) {
  const Icon = attachment.attachment_type === "image" ? ImageIcon : FileText;
  const name = resolvedTitle || attachment.filename || "مرفق";
  const inner = (
    <>
      <Icon className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
      <span className="text-[11px] text-muted-foreground truncate max-w-[160px]">
        {name}
      </span>
    </>
  );

  if (!onOpen || !attachment.document_id) {
    return (
      <div className="flex items-center gap-1.5 rounded-md bg-muted/50 px-2 py-1">
        {inner}
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={() => onOpen(attachment.document_id)}
      className={cn(
        "flex items-center gap-1.5 rounded-md bg-muted/50 px-2 py-1",
        "hover:bg-muted transition-colors cursor-pointer",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
      )}
      aria-label={`فتح المرفق ${name}`}
    >
      {inner}
    </button>
  );
}
