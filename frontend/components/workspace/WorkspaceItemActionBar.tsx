"use client";

import { useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import {
  Eye,
  Pencil,
  Share2,
  Copy,
  Check,
  ThumbsUp,
  ThumbsDown,
  GripVertical,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type { WorkspaceFeedback } from "@/types";

interface WorkspaceItemActionBarProps {
  /** Text the نسخ button writes to the clipboard. */
  copyText: string;
  /**
   * Current view mode. Pass together with ``onModeChange`` to render the
   * معاينة/تحرير toggle (editable kinds only — agent_writing / note /
   * templates). Omit on read-only viewers (agent_search, convo_context).
   */
  mode?: "edit" | "preview";
  onModeChange?: (mode: "edit" | "preview") => void;
  /**
   * When true the toggle cannot switch INTO edit (agent holds the lock).
   * معاينة stays reachable so a locked doc can still be read.
   */
  editDisabled?: boolean;
  /** Pass to render the مشاركة button (agent outputs only). */
  onShare?: () => void;
  /**
   * Current 👍/👎 rating. Pass with ``onFeedback`` to render the like/dislike
   * pair (agent outputs only). Clicking the active thumb clears the rating.
   */
  feedback?: WorkspaceFeedback;
  onFeedback?: (next: WorkspaceFeedback) => void;
  /** Disables the thumbs while a rating round-trip is in flight. */
  feedbackPending?: boolean;
  /**
   * When true the bar renders as a floating, draggable toolbar overlaying the
   * viewer (grab the grip handle to move it; default anchor bottom-left). The
   * PARENT must be ``position: relative``. When false it renders in-flow as a
   * bottom divider row.
   */
  floating?: boolean;
  className?: string;
}

/**
 * Unified action bar for the chat-pane workspace viewers. One standard:
 *
 * - **agent_writing** (تحليل قانوني): معاينة/تحرير · مشاركة · نسخ · 👍 · 👎
 * - **agent_search**  (بحث قانوني): مشاركة · نسخ · 👍 · 👎  (no toggle — read-only)
 * - **note / templates**: معاينة/تحرير · نسخ
 * - **convo_context / references**: نسخ only
 *
 * Each affordance is opt-in via its prop. In ``floating`` mode the whole bar is
 * a draggable pill the user can reposition anywhere over the viewer; buttons
 * are laid out right-to-left (RTL), with the drag grip at the start (right).
 */
export function WorkspaceItemActionBar({
  copyText,
  mode,
  onModeChange,
  editDisabled = false,
  onShare,
  feedback,
  onFeedback,
  feedbackPending = false,
  floating = false,
  className,
}: WorkspaceItemActionBarProps) {
  const [copied, setCopied] = useState(false);

  // Drag state for floating mode. ``pos`` is null until the first drag, while
  // the bar uses its CSS anchor (bottom-right); after a drag it switches to
  // absolute left/top (and the inline style neutralises the anchor classes).
  const barRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{
    startX: number;
    startY: number;
    originX: number;
    originY: number;
  } | null>(null);
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);

  const showToggle = mode !== undefined && onModeChange !== undefined;
  const showFeedback = onFeedback !== undefined;
  const canCopy = copyText.trim().length > 0;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(copyText);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard can fail on insecure contexts / denied permission — the user
      // can still select & copy by hand. Fail silently (same as ArtifactPreview).
    }
  };

  const handleThumb = (thumb: "up" | "down") => {
    if (!onFeedback) return;
    // Toggle off when the active thumb is clicked again.
    onFeedback(feedback === thumb ? null : thumb);
  };

  // --- drag (floating mode only) -------------------------------------------
  const handleDragStart = (e: ReactPointerEvent<HTMLButtonElement>) => {
    const bar = barRef.current;
    if (!bar) return;
    const parent = bar.offsetParent as HTMLElement | null;
    const barRect = bar.getBoundingClientRect();
    const parentRect = parent?.getBoundingClientRect();
    const originX = parentRect ? barRect.left - parentRect.left : bar.offsetLeft;
    const originY = parentRect ? barRect.top - parentRect.top : bar.offsetTop;
    drag.current = { startX: e.clientX, startY: e.clientY, originX, originY };
    setPos({ x: originX, y: originY });
    e.currentTarget.setPointerCapture(e.pointerId);
    e.preventDefault();
  };

  const handleDragMove = (e: ReactPointerEvent<HTMLButtonElement>) => {
    const d = drag.current;
    const bar = barRef.current;
    if (!d || !bar) return;
    const parent = bar.offsetParent as HTMLElement | null;
    let x = d.originX + (e.clientX - d.startX);
    let y = d.originY + (e.clientY - d.startY);
    if (parent) {
      const maxX = parent.clientWidth - bar.offsetWidth;
      const maxY = parent.clientHeight - bar.offsetHeight;
      x = Math.min(Math.max(0, x), Math.max(0, maxX));
      y = Math.min(Math.max(0, y), Math.max(0, maxY));
    }
    setPos({ x, y });
  };

  const handleDragEnd = (e: ReactPointerEvent<HTMLButtonElement>) => {
    drag.current = null;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      // capture may already be gone (e.g. pointercancel) — ignore.
    }
  };

  return (
    <TooltipProvider delayDuration={300}>
      <div
        ref={barRef}
        dir="rtl"
        className={cn(
          floating
            ? "absolute z-20 flex select-none items-center gap-1 rounded-lg border bg-background/95 px-1.5 py-1 shadow-md backdrop-blur supports-[backdrop-filter]:bg-background/80"
            : "mt-6 flex items-center gap-1.5 border-t pt-3",
          // Default anchor: flush to the bottom-left corner until first drag.
          floating && pos === null && "bottom-0 left-0",
          className,
        )}
        style={
          floating && pos
            ? { left: pos.x, top: pos.y, right: "auto", bottom: "auto" }
            : undefined
        }
      >
        {floating && (
          <button
            type="button"
            onPointerDown={handleDragStart}
            onPointerMove={handleDragMove}
            onPointerUp={handleDragEnd}
            onPointerCancel={handleDragEnd}
            className="flex h-7 w-5 shrink-0 cursor-grab touch-none items-center justify-center rounded text-muted-foreground/60 hover:text-foreground active:cursor-grabbing"
            aria-label="تحريك الشريط"
          >
            <GripVertical className="h-4 w-4" />
          </button>
        )}

        {showToggle && (
          <div
            className="flex shrink-0 items-center gap-0.5 rounded-md border border-border bg-muted/30 p-0.5"
            role="tablist"
            aria-label="وضع العرض"
          >
            <Button
              type="button"
              variant={mode === "edit" ? "default" : "ghost"}
              size="sm"
              className="h-6 gap-1 px-2 text-[11px]"
              role="tab"
              aria-selected={mode === "edit"}
              onClick={() => onModeChange?.("edit")}
              disabled={editDisabled}
            >
              <Pencil className="h-3 w-3" />
              تحرير
            </Button>
            <Button
              type="button"
              variant={mode === "preview" ? "default" : "ghost"}
              size="sm"
              className="h-6 gap-1 px-2 text-[11px]"
              role="tab"
              aria-selected={mode === "preview"}
              onClick={() => onModeChange?.("preview")}
            >
              <Eye className="h-3 w-3" />
              معاينة
            </Button>
          </div>
        )}

        {onShare && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 gap-1.5 px-2 text-[11px] text-muted-foreground hover:text-foreground"
            onClick={onShare}
            aria-label="مشاركة عبر رابط"
          >
            <Share2 className="h-3.5 w-3.5" />
            مشاركة
          </Button>
        )}

        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-7 gap-1.5 px-2 text-[11px] text-muted-foreground hover:text-foreground"
          onClick={handleCopy}
          disabled={!canCopy}
          aria-label={copied ? "تم النسخ" : "نسخ"}
        >
          {copied ? (
            <>
              <Check className="h-3.5 w-3.5 text-green-600" />
              تم النسخ
            </>
          ) : (
            <>
              <Copy className="h-3.5 w-3.5" />
              نسخ
            </>
          )}
        </Button>

        {showFeedback && (
          <>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className={cn(
                    "h-7 w-7",
                    feedback === "up"
                      ? "text-primary"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                  onClick={() => handleThumb("up")}
                  disabled={feedbackPending}
                  aria-pressed={feedback === "up"}
                  aria-label="إعجاب"
                >
                  <ThumbsUp
                    className={cn(
                      "h-3.5 w-3.5",
                      feedback === "up" && "fill-primary",
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
                  type="button"
                  variant="ghost"
                  size="icon"
                  className={cn(
                    "h-7 w-7",
                    feedback === "down"
                      ? "text-destructive"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                  onClick={() => handleThumb("down")}
                  disabled={feedbackPending}
                  aria-pressed={feedback === "down"}
                  aria-label="عدم إعجاب"
                >
                  <ThumbsDown
                    className={cn(
                      "h-3.5 w-3.5",
                      feedback === "down" && "fill-destructive",
                    )}
                  />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="bottom">
                <p className="text-xs">عدم إعجاب</p>
              </TooltipContent>
            </Tooltip>
          </>
        )}
      </div>
    </TooltipProvider>
  );
}
