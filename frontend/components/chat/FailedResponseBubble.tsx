"use client";

import { AlertCircle, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { getRelativeTimeAr } from "@/lib/utils";

interface FailedResponseBubbleProps {
  /** ISO timestamp of the placeholder — shown so the user sees when it failed. */
  createdAt: string;
  /** Re-run the turn (regenerate the preceding user message). Hidden if absent. */
  onRetry?: () => void;
}

/**
 * Terminal state for an assistant turn whose run never produced a response and
 * is no longer recoverable — e.g. the request died on logout or a server
 * restart, leaving an empty placeholder that the background-recovery poll
 * (``useMessages``) has given up on. Replaces the perpetual "ريحان يحلّل"
 * thinking indicator so the card never spins forever, and offers a retry that
 * re-runs the question.
 *
 * Styled to match a failed assistant ``MessageBubble`` (destructive border,
 * start-aligned card) so it reads as "the assistant's response failed".
 */
export function FailedResponseBubble({
  createdAt,
  onRetry,
}: FailedResponseBubbleProps) {
  return (
    <div
      dir="rtl"
      lang="ar"
      className="flex w-full mb-3.5 justify-start"
    >
      <div className="relative max-w-[85%] rounded-2xl border-2 border-destructive bg-card px-4 py-3 shadow-sm text-sm leading-[1.75]">
        <div className="flex items-center gap-2">
          <AlertCircle className="h-4 w-4 text-destructive shrink-0" />
          <span className="text-destructive">
            تعذّر إكمال الرد. قد يكون الاتصال قد انقطع أثناء المعالجة.
          </span>
        </div>

        {onRetry && (
          <div className="flex items-center mt-2.5">
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-xs px-2.5 gap-1 border-destructive/50 text-destructive hover:text-destructive hover:bg-destructive/10"
              onClick={onRetry}
            >
              <RefreshCw className="h-3 w-3" />
              إعادة المحاولة
            </Button>
          </div>
        )}

        <div className="flex items-center mt-2">
          <span className="text-[10px] text-muted-foreground select-none">
            {getRelativeTimeAr(createdAt)}
          </span>
        </div>
      </div>
    </div>
  );
}
