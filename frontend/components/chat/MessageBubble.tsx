"use client";

import {
  Copy,
  Check,
  BookOpen,
  BookText,
  FileText,
  FileSearch,
  ImageIcon,
  AlertCircle,
  PenLine,
  RefreshCw,
  Pencil,
  StickyNote,
  ThumbsUp,
  ThumbsDown,
  HelpCircle,
  CornerUpLeft,
} from "lucide-react";
import { memo, useState, useCallback, useRef, useEffect, type KeyboardEvent } from "react";
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
import { useAuthStore } from "@/stores/auth-store";
import { StreamingText } from "@/components/chat/StreamingText";
import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";
import { TemplateSaveOfferChip } from "@/components/chat/TemplateSaveOfferChip";
import { WiBadge } from "@/components/workspace/WiBadge";
import type { Attachment, Message, WorkspaceItemKind } from "@/types";

type FeedbackState = "none" | "up" | "down";

/** What the chips need to name a workspace_item they link to. */
export interface ArtifactLookupEntry {
  kind: WorkspaceItemKind;
  title: string;
  /** ``metadata.subtype`` — names the artifact far better than kind does
   *  («التحليل القانوني» rather than «المسودة»). */
  subtype?: string | null;
  /** ``wi_seq`` — the «WI-3» alias the reply text itself may cite. */
  wi_seq?: number | null;
}

export type ArtifactLookup = Record<string, ArtifactLookupEntry>;

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
  artifactLookup?: ArtifactLookup;
  /** Open a workspace item in the pane (used by chip click). */
  onOpenArtifact?: (itemId: string) => void;
  /**
   * First ``agent_search`` artifact of this message — the target of ``[n]``
   * citation clicks. A plain string rather than a per-render closure so
   * ``memo(MessageBubble)`` and the memoized ``MarkdownRenderer`` under it
   * stay cache hits while another message streams.
   */
  citationArtifactId?: string;
  /** Navigate to reference ``n`` inside ``artifactId`` (identity-stable). */
  onCitationNavigate?: (artifactId: string, n: number) => void;
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

export const MessageBubble = memo(function MessageBubble({
  message,
  streamingContent,
  onRegenerate,
  onEditResend,
  onRetry,
  artifactIds,
  artifactLookup,
  onOpenArtifact,
  citationArtifactId,
  onCitationNavigate,
  referencedItemIds,
  onJumpToReferencedItem,
  templateOffer,
}: MessageBubbleProps) {
  const [copied, setCopied] = useState(false);
  const [feedback, setFeedback] = useState<FeedbackState>("none");
  const [isEditing, setIsEditing] = useState(false);
  const [editContent, setEditContent] = useState("");
  const editTextareaRef = useRef<HTMLTextAreaElement>(null);
  // Narrow selectors (strings) so settled bubbles never re-render on other
  // auth-store changes. `call_name` wins — see lib/user-name.ts.
  const callName = useAuthStore((s) => s.user?.call_name);
  const fullName = useAuthStore((s) => s.user?.full_name_ar);
  const userName = callName || fullName || "أنت";

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

  // Rebuilt only when the target artifact changes; the stable identity keeps
  // the memoized MarkdownRenderer from re-parsing on unrelated re-renders.
  const handleCitationClick = useCallback(
    (n: number) => {
      if (citationArtifactId) onCitationNavigate?.(citationArtifactId, n);
    },
    [citationArtifactId, onCitationNavigate]
  );

  const displayContent = isCurrentlyStreaming ? streamingContent : message.content;

  // ==========================================================================
  // USER MESSAGE — compact tinted bubble at the inline-end. The shrink-wrapped
  // shape (vs the assistant's full-column prose) is what marks it as the
  // question; no visible sender chrome — the label is screen-reader-only.
  // ==========================================================================
  if (isUser) {
    return (
      <TooltipProvider delayDuration={300}>
        <div
          dir="rtl"
          lang="ar"
          className={cn(
            "flex flex-col items-start gap-1.5 mb-3 group/bubble",
            message.isOptimistic && !message.isFailed && "opacity-70"
          )}
        >
          {/* Sender name — small caption on the same right rail the bubble
              starts from */}
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-medium text-muted-foreground select-none">
              {userName}
            </span>
            {isAgentAnswer && (
              <span className="text-[10px] font-medium text-muted-foreground bg-muted/60 rounded px-1.5 py-0.5 select-none">
                (جواب)
              </span>
            )}
          </div>

          {/* Body / edit mode */}
          {isEditing ? (
            <div className="w-full space-y-2">
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
          ) : (
            <div className="flex w-full items-center justify-start gap-1">
              {isCurrentlyStreaming ? (
                <div
                  dir="auto"
                  className="w-fit max-w-[85%] sm:max-w-[80%] rounded-2xl bg-muted px-4 py-2.5 text-start text-[15px] leading-[1.75] text-foreground"
                >
                  <StreamingText content={streamingContent ?? ""} />
                </div>
              ) : (
                <div
                  dir="auto"
                  className="w-fit max-w-[85%] sm:max-w-[80%] rounded-2xl bg-muted px-4 py-2.5 text-start text-[15px] leading-[1.75] text-foreground whitespace-pre-wrap"
                >
                  {displayContent}
                </div>
              )}

              {/* Gutter actions — beside the bubble on its outer (inline-end)
                  side so they consume no vertical space; the question→answer
                  gap must stay tighter than the between-turns gap */}
              {isCompleted && !message.isFailed && (
                <div
                  className={cn(
                    "flex items-center gap-0.5",
                    "opacity-0 group-hover/bubble:opacity-100 group-focus-within/bubble:opacity-100 transition-opacity duration-200",
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

                  <span className="ps-1 text-[11px] text-muted-foreground select-none max-sm:hidden">
                    {getRelativeTimeAr(message.created_at)}
                  </span>
                </div>
              )}
            </div>
          )}

          {/* Failed indicator + retry */}
          {message.isFailed && (
            <div className="flex items-center gap-2">
              <AlertCircle className="h-3.5 w-3.5 text-destructive shrink-0" />
              <span className="text-xs text-destructive">فشل إرسال الرسالة</span>
              <Button
                variant="outline"
                size="sm"
                className="h-6 text-xs px-2 gap-1 border-destructive/50 text-destructive hover:text-destructive hover:bg-destructive/10"
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
            className="justify-end"
          />

        </div>
      </TooltipProvider>
    );
  }

  // ==========================================================================
  // ASSISTANT MESSAGE — unframed full-column prose on the page background;
  // the shape asymmetry against the user's compact bubble is what pairs a
  // question with its answer. Only the agent-question callout and the failed
  // state keep a card frame.
  // ==========================================================================
  const isFramed = isAgentQuestion || message.isFailed;

  return (
    <TooltipProvider delayDuration={300}>
      <div
        dir="rtl"
        lang="ar"
        // Tour anchor (Act 1, step 1) — «محادثتك والردود، تمامًا كما تتوقع».
        // Inert: an attribute the tour engine resolves with
        // `[data-tour="chat-thread"]`; it changes no behaviour and no layout.
        data-tour="chat-thread"
        className={cn(
          "w-full group/bubble",
          // An agent question binds tightly to the user's upcoming reply;
          // a settled answer closes the turn with a wide gap before the next.
          isAgentQuestion ? "mb-3" : "mb-10",
          message.isOptimistic && !message.isFailed && "opacity-70"
        )}
      >
        {/* Sender name — mirrors the user caption, same rail */}
        <div className="mb-1.5 flex items-center">
          <span className="text-[11px] font-medium text-muted-foreground select-none">
            ريحان
          </span>
        </div>
        <div
          className={cn(
            "text-foreground text-sm leading-[1.75]",
            // Justified paragraphs (flush at both edges, like a legal document)
            // + Arabic-comfortable line-height. On the container rather than
            // MarkdownRenderer so the streaming path gets the same treatment.
            !isAgentQuestion &&
              "[text-justify:inter-word] [&_li]:text-justify [&_li]:leading-[1.85] [&_p]:text-justify [&_p]:leading-[1.85]",
            isFramed &&
              "relative w-fit max-w-full rounded-2xl border bg-card px-4 py-3 shadow-sm",
            message.isFailed && "border-destructive border-2",
            isAgentQuestion &&
              "border-primary/40 bg-primary/[0.04] border-s-4 border-s-primary/70"
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

          {/* Content */}
          {isCurrentlyStreaming ? (
            <StreamingText content={streamingContent ?? ""} />
          ) : (
            <MarkdownRenderer
              content={displayContent ?? ""}
              onCitationClick={
                hasArtifacts && citationArtifactId
                  ? handleCitationClick
                  : undefined
              }
            />
          )}

          {/* Sources + referenced prior cards + template offer — one always-
              visible row: for a legal answer, source presence is content, not
              chrome, so it never hides behind hover. All three stay hidden
              during streaming — their SSE events attach to the assistant
              message_id and the bubble re-renders with them once settled. */}
          {!isCurrentlyStreaming &&
            (hasArtifacts || hasReferencedItems || hasTemplateOffer) && (
              <div className="flex flex-wrap items-center gap-1.5 mt-3">
                {hasArtifacts && (
                  <ArtifactChip
                    artifactIds={artifactIds!}
                    artifactLookup={artifactLookup}
                    onOpenArtifact={onOpenArtifact}
                  />
                )}
                {hasReferencedItems &&
                  referencedItemIds!.map((id) => (
                    <ReferencedItemChip
                      key={id}
                      itemId={id}
                      label={artifactLookup?.[id]?.title}
                      seq={artifactLookup?.[id]?.wi_seq}
                      onJump={onJumpToReferencedItem}
                    />
                  ))}
                {hasTemplateOffer && (
                  <TemplateSaveOfferChip
                    itemId={templateOffer!.itemId}
                    titleHint={templateOffer!.titleHint}
                  />
                )}
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

          {/* Action bar — actions at the start, model + timestamp meta at the
              end; height reserved so the hover reveal never shifts layout */}
          {isCompleted && !message.isFailed && (
            <div
              className={cn(
                "flex h-8 items-center gap-0.5 mt-1.5",
                "opacity-0 group-hover/bubble:opacity-100 group-focus-within/bubble:opacity-100 transition-opacity duration-200",
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

              <span className="ms-auto flex items-center gap-1.5 text-[11px] text-muted-foreground select-none">
                {message.model && (
                  <>
                    <span dir="ltr" className="[unicode-bidi:isolate]">
                      {message.model}
                    </span>
                    <span aria-hidden="true">·</span>
                  </>
                )}
                <span>{getRelativeTimeAr(message.created_at)}</span>
              </span>
            </div>
          )}

          {/* Failed state keeps its timestamp inside the card */}
          {message.isFailed && (
            <div className="flex items-center mt-2">
              <span className="text-[10px] text-muted-foreground select-none">
                {getRelativeTimeAr(message.created_at)}
              </span>
            </div>
          )}
        </div>
      </div>
    </TooltipProvider>
  );
});

// ============================================================================
// Artifact chip (Window C)
// ============================================================================

interface ArtifactChipProps {
  artifactIds: string[];
  artifactLookup?: ArtifactLookup;
  onOpenArtifact?: (itemId: string) => void;
}

/**
 * Arabic name of the artifact WITH the definite article, for «افتح ال…».
 *
 * A lookup, not morphology — same reasoning as ReferencePanel's
 * DEFINITE_DOC_TYPE: «ملخص المحادثة» and «نتيجة البحث» are already definite by
 * إضافة and take no «ال» at all, so prefixing programmatically would mangle
 * them. Subtype wins over kind because it is what the reader recognises: the
 * card they are about to open says «تحليل قانوني», not «مسودة».
 */
const DEFINITE_SUBTYPE: Record<string, string> = {
  report: "التقرير",
  contract: "العقد",
  memo: "المذكرة",
  summary: "الملخص",
  memory_file: "الذاكرة",
  legal_opinion: "الرأي القانوني",
  legal_synthesis: "التحليل القانوني",
};

const DEFINITE_KIND: Record<WorkspaceItemKind, string> = {
  attachment: "المرفق",
  note: "الملاحظة",
  agent_search: "نتيجة البحث",
  agent_writing: "المسودة",
  convo_context: "ملخص المحادثة",
  references: "المراجع",
};

/** «المصدر» is the honest fallback while the workspace list is still loading. */
function definiteArtifactName(entry: ArtifactLookupEntry | undefined): string {
  if (!entry) return "المصدر";
  const bySubtype = entry.subtype ? DEFINITE_SUBTYPE[entry.subtype] : undefined;
  return bySubtype ?? DEFINITE_KIND[entry.kind] ?? "المصدر";
}

/**
 * «افتح التحليل القانوني» — the link to this turn's workspace_item(s),
 * rendered in the chips row under an assistant message.
 *
 * - One artifact → a solid primary button naming what it opens. Deliberately
 *   the loudest thing in the row and styled like the library's «فتح النظام في
 *   ريحان» CTA: for a legal answer the source IS the answer's backing, and the
 *   old 10px grey «المصدر» pill read as chrome people scrolled past.
 * - Multiple artifacts → the same button as a DropdownMenu trigger; clicking a
 *   row opens that id.
 */
function ArtifactChip({
  artifactIds,
  artifactLookup,
  onOpenArtifact,
}: ArtifactChipProps) {
  if (artifactIds.length === 0) return null;

  const baseButtonClass = "h-8 gap-1.5 px-3 text-xs";

  if (artifactIds.length === 1) {
    const id = artifactIds[0];
    const seq = artifactLookup?.[id]?.wi_seq;
    return (
      <Button
        size="sm"
        // Tour anchor (Act 1, step 2) — the «افتح التحليل القانوني WI-1» CTA
        // the user is asked to click. Inert attribute; both branches of this
        // chip carry it, and only one ever renders per message.
        data-tour="artifact-chip"
        className={baseButtonClass}
        onClick={() => onOpenArtifact?.(id)}
      >
        <BookOpen className="h-3.5 w-3.5" />
        افتح {definiteArtifactName(artifactLookup?.[id])}
        {/* The alias the reply body may have named («… (WI-1)»). Rendered as
            plain dimmed text rather than a WiBadge — inside a solid primary
            CTA a bordered grey pill reads as a second button. */}
        {seq !== null && seq !== undefined && (
          <span dir="ltr" className="font-mono text-[10px] opacity-70">
            WI-{seq}
          </span>
        )}
      </Button>
    );
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          size="sm"
          data-tour="artifact-chip"
          className={baseButtonClass}
          aria-label="فتح المصادر"
        >
          <BookOpen className="h-3.5 w-3.5" />
          افتح المصادر ({artifactIds.length})
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-[220px]">
        {artifactIds.map((id) => {
          const entry = artifactLookup?.[id];
          const label = entry?.title || definiteArtifactName(entry);
          return (
            <DropdownMenuItem
              key={id}
              onClick={() => onOpenArtifact?.(id)}
              className="text-xs"
            >
              <FileSearch className="h-3 w-3 me-1.5 shrink-0 text-muted-foreground" />
              <span className="truncate">{label}</span>
              {/* Alias at the row's end: with several cards on one turn this is
                  what tells «WI-2» from «WI-3» before opening either. */}
              <WiBadge seq={entry?.wi_seq} className="ms-auto" />
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
  /** ``wi_seq`` of the referenced card — the alias the reply may have cited. */
  seq?: number | null;
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
function ReferencedItemChip({
  itemId,
  label,
  seq,
  onJump,
}: ReferencedItemChipProps) {
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
      <WiBadge seq={seq} />
    </Button>
  );
}

// ============================================================================
// Attachment chips
// ============================================================================

interface AttachmentListProps {
  attachments: Attachment[];
  /** Resolves ``attachment.document_id`` → workspace_item {kind, title}. */
  artifactLookup?: ArtifactLookup;
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
          // Kind from the API row (migration 088 embeds it) with the
          // workspace-list cache as fallback for legacy cached rows.
          resolvedKind={att.kind ?? artifactLookup?.[att.document_id]?.kind}
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
  resolvedKind?: WorkspaceItemKind;
  onOpen?: (itemId: string) => void;
}

/**
 * Per-kind chip presentation so the user can tell an *uploaded file* apart
 * from an item *attached from within the conversation* (blog import, note,
 * draft). Mirrors the composer chips: blogs keep the BookText/text-primary
 * look of ``BlogChip``. ``hint`` doubles as the hover tooltip (native title).
 */
const CHIP_STYLE_BY_KIND: Partial<
  Record<WorkspaceItemKind, { icon: typeof FileText; iconClass: string; hint: string }>
> = {
  agent_search: {
    icon: BookText,
    iconClass: "text-primary",
    hint: "مدونة/تحليل مرفق من المحادثة",
  },
  note: {
    icon: StickyNote,
    iconClass: "text-primary",
    hint: "ملاحظة مرفقة من المحادثة",
  },
  agent_writing: {
    icon: PenLine,
    iconClass: "text-primary",
    hint: "مسودة مرفقة من المحادثة",
  },
};

function AttachmentChip({
  attachment,
  resolvedTitle,
  resolvedKind,
  onOpen,
}: AttachmentChipProps) {
  const kindStyle = resolvedKind ? CHIP_STYLE_BY_KIND[resolvedKind] : undefined;
  const Icon =
    kindStyle?.icon ??
    (attachment.attachment_type === "image" ? ImageIcon : FileText);
  const hint = kindStyle?.hint ?? "ملف مرفوع";
  const name = resolvedTitle || attachment.filename || "مرفق";
  const inner = (
    <>
      <Icon
        className={cn(
          "h-3.5 w-3.5 shrink-0",
          kindStyle?.iconClass ?? "text-muted-foreground",
        )}
      />
      <span className="text-[11px] text-muted-foreground truncate max-w-[160px]">
        {name}
      </span>
    </>
  );

  if (!onOpen || !attachment.document_id) {
    return (
      <div
        title={hint}
        className="flex items-center gap-1.5 rounded-md bg-muted/50 px-2 py-1"
      >
        {inner}
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={() => onOpen(attachment.document_id)}
      title={hint}
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
