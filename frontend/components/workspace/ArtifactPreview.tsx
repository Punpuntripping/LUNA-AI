"use client";

import { useState, type ReactNode } from "react";
import { Check, Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";
import { cn } from "@/lib/utils";

interface ArtifactPreviewProps {
  /** Raw markdown body. Copy button copies this string verbatim. */
  content: string;
  /**
   * Extra action(s) rendered to the start (RTL: left) of the copy button —
   * e.g. a preview/edit toggle on NoteEditor.
   */
  headerActions?: ReactNode;
  /**
   * Additional content rendered after the markdown body, inside the same
   * scroll viewport. Used by AgentSearchViewer to append the ReferencePanel.
   */
  footer?: ReactNode;
  /** Hide the floating toolbar (used when the parent renders its own). */
  hideToolbar?: boolean;
  className?: string;
  /** Override the copy button label / aria-label. Default: "نسخ". */
  copyLabel?: string;
  /** Test hook. */
  "data-testid"?: string;
}

/**
 * Unified markdown preview for any workspace artifact (search result,
 * convo_context, note preview, references, source popup, future kinds).
 *
 * Renders ``content`` through the chat ``MarkdownRenderer`` — so ``#``,
 * ``##``, lists, bold, tables all render properly instead of leaking raw
 * markers — and exposes a "نسخ" button in the upper start corner that copies
 * the raw markdown string. The button does NOT copy rendered HTML or
 * rewritten citations, so the user always gets back what was authored /
 * stored.
 *
 * Layout: floating toolbar pinned to the top-start of the viewport (RTL
 * adjusted), markdown body in a scrollable padded column underneath, optional
 * ``footer`` slot for the reference panel.
 */
export function ArtifactPreview({
  content,
  headerActions,
  footer,
  hideToolbar = false,
  className,
  copyLabel = "نسخ",
  "data-testid": testId,
}: ArtifactPreviewProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(content ?? "");
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard API can fail on insecure contexts or denied permissions;
      // fail silently rather than throw — the user can still select & copy
      // by hand. We don't surface an error toast because the affordance is
      // a nice-to-have, not a primary action.
    }
  };

  const hasContent = (content ?? "").trim().length > 0;

  return (
    <div className={cn("relative flex flex-1 min-h-0 flex-col", className)} dir="rtl">
      {!hideToolbar && (
        <div
          className={cn(
            "pointer-events-none absolute end-3 top-3 z-10 flex items-center gap-1.5",
            "rtl:flex-row-reverse",
          )}
        >
          {headerActions ? (
            <div className="pointer-events-auto flex items-center gap-1.5">
              {headerActions}
            </div>
          ) : null}
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={handleCopy}
            disabled={!hasContent}
            aria-label={copied ? "تم النسخ" : copyLabel}
            className={cn(
              "pointer-events-auto h-7 gap-1.5 px-2 text-[11px] shadow-sm",
              "bg-background/80 backdrop-blur supports-[backdrop-filter]:bg-background/60",
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
                <span>{copyLabel}</span>
              </>
            )}
          </Button>
        </div>
      )}

      <ScrollArea className="flex-1" data-testid={testId}>
        <div className="p-6 pt-12" dir="rtl">
          {hasContent ? (
            <MarkdownRenderer content={content} />
          ) : (
            <p className="text-sm text-muted-foreground">لا يوجد محتوى للعرض.</p>
          )}
          {footer}
        </div>
      </ScrollArea>
    </div>
  );
}
