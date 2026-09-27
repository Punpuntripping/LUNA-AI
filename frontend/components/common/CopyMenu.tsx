"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { markdownToPlainText } from "@/lib/markdown/plain-text";
import { cn } from "@/lib/utils";

/**
 * Trigger look. Each one is the EXACT button the call site rendered before the
 * menu existed, so swapping a single «نسخ» button for this menu changes nothing
 * visually until it is clicked:
 *
 * - ``bar``     — WorkspaceItemActionBar (ghost, icon + label).
 * - ``toolbar`` — ArtifactPreview floating toolbar (secondary, blurred pill).
 * - ``icon``    — MessageBubble gutter / action row (ghost icon + tooltip).
 */
export type CopyMenuVariant = "bar" | "toolbar" | "icon";

interface CopyMenuProps {
  /**
   * Markdown to copy (body + any «المراجع» block the host appends). «نسخ»
   * writes it verbatim; «النسخ لناجز» writes ``markdownToPlainText(text)``.
   */
  text: string;
  variant: CopyMenuVariant;
  /** Trigger label / aria-label. Default «نسخ». */
  label?: string;
  /** Disables the trigger. Blank ``text`` disables it regardless. */
  disabled?: boolean;
  /** Fired after a successful clipboard write, with the mode that was used. */
  onCopied?: (mode: "markdown" | "plain") => void;
  className?: string;
}

const COPIED_RESET_MS = 1500;

/**
 * «نسخ» as a two-item dropdown (plan: najiz_plain_copy.md).
 *
 * - «نسخ» — the raw markdown, headings and formatting intact.
 * - «النسخ لناجز» — plain text with every markdown marker stripped, for Najiz
 *   fields that accept no formatting at all. ``[n]`` markers and the
 *   «المراجع» lines survive (see ``lib/markdown/plain-text.ts``).
 *
 * Owns the «تم النسخ» flip (1.5s) and fails silently when the Clipboard API is
 * unavailable (insecure context / denied permission) — the user can still
 * select & copy by hand, and copy is a convenience, not a primary action.
 *
 * The menu portals at z-[70] (``ui/dropdown-menu``), so it opens above the
 * mobile workspace overlay and the docked action bar.
 */
export function CopyMenu({
  text,
  variant,
  label = "نسخ",
  disabled = false,
  onCopied,
  className,
}: CopyMenuProps) {
  const [copied, setCopied] = useState(false);
  const resetTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (resetTimer.current !== null) clearTimeout(resetTimer.current);
    },
    [],
  );

  const canCopy = !disabled && text.trim().length > 0;

  const writeClipboard = async (mode: "markdown" | "plain") => {
    // Converted at click time, not per render — chat bubbles re-render on
    // every streamed token and the plain pass is never needed until now.
    const payload = mode === "plain" ? markdownToPlainText(text) : text;
    try {
      await navigator.clipboard.writeText(payload);
      setCopied(true);
      if (resetTimer.current !== null) clearTimeout(resetTimer.current);
      resetTimer.current = setTimeout(() => setCopied(false), COPIED_RESET_MS);
      onCopied?.(mode);
    } catch {
      // Clipboard can fail on insecure contexts / denied permission — fail
      // silently (same contract the single «نسخ» buttons had).
    }
  };

  const ariaLabel = copied ? "تم النسخ" : label;

  const trigger =
    variant === "icon" ? (
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className={cn(
          "h-7 w-7 max-md:h-9 max-md:w-9 text-muted-foreground hover:text-foreground",
          className,
        )}
        disabled={!canCopy}
        aria-label={ariaLabel}
      >
        {copied ? (
          <Check className="h-3.5 w-3.5 text-success-fg" />
        ) : (
          <Copy className="h-3.5 w-3.5" />
        )}
      </Button>
    ) : variant === "toolbar" ? (
      <Button
        type="button"
        variant="secondary"
        size="sm"
        disabled={!canCopy}
        aria-label={ariaLabel}
        className={cn(
          "pointer-events-auto h-7 gap-1.5 px-2 text-xs shadow-sm",
          "bg-background/80 backdrop-blur supports-[backdrop-filter]:bg-background/60",
          className,
        )}
      >
        {copied ? (
          <>
            <Check className="h-3 w-3" />
            <span>تم النسخ</span>
          </>
        ) : (
          <>
            <Copy className="h-3 w-3" />
            <span>{label}</span>
          </>
        )}
      </Button>
    ) : (
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className={cn(
          "h-7 max-md:h-9 gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground",
          className,
        )}
        disabled={!canCopy}
        aria-label={ariaLabel}
      >
        {copied ? (
          <>
            <Check className="h-3.5 w-3.5 text-success-fg" />
            تم النسخ
          </>
        ) : (
          <>
            <Copy className="h-3.5 w-3.5" />
            {label}
          </>
        )}
      </Button>
    );

  return (
    <DropdownMenu dir="rtl">
      {variant === "icon" ? (
        // Icon-only trigger keeps its hover tooltip. Own provider so the menu
        // works under any host; nesting inside a host provider is harmless.
        <TooltipProvider delayDuration={300}>
          <Tooltip>
            <TooltipTrigger asChild>
              <DropdownMenuTrigger asChild>{trigger}</DropdownMenuTrigger>
            </TooltipTrigger>
            <TooltipContent side="bottom">
              <p className="text-xs">{ariaLabel}</p>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      ) : (
        <DropdownMenuTrigger asChild>{trigger}</DropdownMenuTrigger>
      )}
      {/* `dir` comes from the Root above — Radix stamps it on the content. */}
      <DropdownMenuContent align="start" className="w-64">
        <CopyMenuItem
          title="نسخ"
          hint="يحتفظ بالعناوين والتنسيق"
          onSelect={() => void writeClipboard("markdown")}
        />
        <CopyMenuItem
          title="النسخ لناجز"
          hint="نص عادي بدون أي تنسيق — جاهز للصق في ناجز"
          onSelect={() => void writeClipboard("plain")}
        />
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function CopyMenuItem({
  title,
  hint,
  onSelect,
}: {
  title: string;
  hint: string;
  onSelect: () => void;
}) {
  return (
    <DropdownMenuItem
      onSelect={onSelect}
      // `max-md:min-h-9` — 36px touch target below `md` (3.4).
      className="flex-col items-start gap-0.5 text-start max-md:min-h-9"
    >
      <span className="text-sm">{title}</span>
      <span className="text-xs text-muted-foreground">{hint}</span>
    </DropdownMenuItem>
  );
}
