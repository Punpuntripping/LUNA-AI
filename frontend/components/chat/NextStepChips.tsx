"use client";

import { memo, useCallback } from "react";
import { BookOpen, PenLine, Scale, Search, type LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/stores/chat-store";
import { trackNextStepClicked } from "@/components/analytics/run-tracker";
import type { NextStep, NextStepKind } from "@/types";

/** One small icon per kind (next_step_suggestions plan §3.7). */
const KIND_ICON: Record<NextStepKind, LucideIcon> = {
  narrow_search: Search,
  draft: PenLine,
  apply: Scale,
  open: BookOpen,
};

interface NextStepChipsProps {
  steps: NextStep[];
  conversationId: string;
  messageId: string;
  /** Agent family that produced the answer, when the row carries it. */
  family?: string | null;
  className?: string;
}

/**
 * Clickable next-step chips under the LATEST finished assistant answer
 * (next_step_suggestions plan D2–D4, §3.7).
 *
 * Click = PASTE the chip's full prompt into the composer (replacing whatever
 * is there), focused and editable. It NEVER sends — the user may edit, and a
 * sent chip is an ordinary user message the router classifies as usual.
 *
 * Visual language matches the bubble's other outline pills
 * (``ReferencedItemChip``): rounded-full, h-7, muted text that lifts on hover.
 */
export const NextStepChips = memo(function NextStepChips({
  steps,
  conversationId,
  messageId,
  family,
  className,
}: NextStepChipsProps) {
  const pasteNextStep = useChatStore((s) => s.pasteNextStep);

  const handleClick = useCallback(
    (step: NextStep) => {
      pasteNextStep(step);
      trackNextStepClicked({
        kind: step.kind,
        family: family ?? null,
        conversationId,
        messageId,
      });
    },
    [pasteNextStep, family, conversationId, messageId],
  );

  if (steps.length === 0) return null;

  return (
    <div
      dir="rtl"
      role="group"
      aria-label="خطوات مقترحة"
      className={cn("flex flex-wrap items-center gap-1.5", className)}
    >
      {steps.map((step) => {
        const Icon = KIND_ICON[step.kind];
        return (
          <Button
            key={step.kind}
            type="button"
            variant="outline"
            size="sm"
            className={cn(
              "h-7 max-w-full gap-1.5 px-3 text-xs",
              // Touch target below `md`, same as the bubble's action bar.
              "max-md:h-9",
              "rounded-full border-border/70 text-muted-foreground hover:text-foreground",
              "hover:bg-accent/40 transition-colors",
            )}
            onClick={() => handleClick(step)}
            aria-label={step.prompt}
            title={step.prompt}
          >
            <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span className="truncate">{step.label}</span>
          </Button>
        );
      })}
    </div>
  );
});
