"use client";

import { memo, useCallback, useEffect, useId, useRef, useState } from "react";
import { AlertCircle, Check, ListChecks } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useIsConversationRunning } from "@/stores/chat-store";
import {
  PLAN_CARD_AR,
  planButtonLabelAr,
  planCardView,
  planCheaperLineAr,
  planCostLineAr,
  planPickRefused,
  planPointsAr,
  planSearchLineAr,
  planSearches,
  planStatusLineAr,
  type ExtendedPlanCard,
  type ExtendedPlanOption,
  type PlanChoicePayload,
  type PlanOptionId,
} from "@/lib/contracts/extended-plan";

/** Sends the button's choice as a normal message; resolves when that send ends. */
export type PlanChoiceHandler = (
  choice: PlanChoicePayload,
  label: string,
) => Promise<void> | void;

interface PlanCardProps {
  card: ExtendedPlanCard;
  conversationId: string;
  /** The card row is the conversation's last message. */
  isLatest: boolean;
  /** Absent (e.g. the read-only demo conversation) → the buttons never go live. */
  onChoose?: PlanChoiceHandler;
}

/**
 * «بحث موسّع» plan card (extended_search.md §4.4) — rendered by MessageBubble
 * inside the agent-question frame, in place of the row's text.
 *
 * Unlike the next-step chips (which paste), a button here SENDS: the stored
 * option runs exactly as priced. It locks at once (local pick), then the
 * server's `outcome` on the refetched row drives what it shows.
 * State rules + every Arabic line live in `lib/contracts/extended-plan.ts`
 * (shared with the mobile card).
 */
export const PlanCard = memo(function PlanCard({
  card,
  conversationId,
  isLatest,
  onChoose,
}: PlanCardProps) {
  const running = useIsConversationRunning(conversationId);
  const [pick, setPick] = useState<PlanOptionId | null>(null);
  const [sending, setSending] = useState(false);
  // Synchronous guard: two clicks inside one frame both see stale state.
  const sendingRef = useRef(false);
  const titleId = useId();

  const ui = { isLatest, busy: running || sending, pick };
  const view = planCardView(card, ui);
  const live = view.state === "active" && onChoose !== undefined;
  const chosen = view.state === "chosen" ? view.option : null;

  // The send ended but the user's row did not survive (refused / dropped):
  // nothing ran, so the card forgets the pick and its buttons come back clean.
  const refused = planPickRefused(card, ui);
  useEffect(() => {
    if (refused) setPick(null);
  }, [refused]);

  const handleChoose = useCallback(
    async (option: ExtendedPlanOption) => {
      if (!onChoose || sendingRef.current) return;
      sendingRef.current = true;
      setPick(option.id);
      setSending(true);
      try {
        await onChoose({ run_id: card.run_id, option: option.id }, option.label);
      } finally {
        sendingRef.current = false;
        setSending(false);
      }
    },
    [onChoose, card.run_id],
  );

  const searches = planSearches(card);
  const cheaperLine = planCheaperLineAr(card);
  const statusLine = planStatusLineAr(card, view);

  return (
    <div dir="rtl" lang="ar" className="space-y-2">
      <div className="flex items-center gap-1.5">
        <ListChecks className="h-3.5 w-3.5 shrink-0 text-primary" aria-hidden="true" />
        <span id={titleId} className="text-xs font-semibold text-primary">
          {PLAN_CARD_AR.title}
        </span>
      </div>

      {searches.length > 0 && (
        <ol className="space-y-1">
          {searches.map((search, i) => (
            <li key={i} className="flex gap-1.5">
              <span className="shrink-0 tabular-nums text-muted-foreground">{i + 1}.</span>
              <span className="min-w-0">{planSearchLineAr(search)}</span>
            </li>
          ))}
        </ol>
      )}

      <p className="text-muted-foreground">{PLAN_CARD_AR.merge}</p>

      <div className="space-y-0.5 text-xs tabular-nums">
        <p className="font-medium text-foreground">{planCostLineAr(card)}</p>
        {cheaperLine && <p className="text-muted-foreground">{cheaperLine}</p>}
      </div>

      <div
        role="group"
        aria-labelledby={titleId}
        className="flex flex-wrap items-center gap-1.5 pt-0.5"
      >
        {card.options.map((option) => {
          const isChosen = chosen === option.id;
          return (
            <Button
              key={option.id}
              type="button"
              size="sm"
              variant={option.id === "plan" ? "default" : "outline"}
              disabled={!live}
              aria-pressed={chosen !== null ? isChosen : undefined}
              aria-label={`${option.label}، نحو ${planPointsAr(option.points)}`}
              onClick={() => void handleChoose(option)}
              className={cn(
                "h-8 max-w-full gap-1.5 px-3 text-xs max-md:h-9",
                // The picked button stays at full strength; the rest fade.
                isChosen && "disabled:opacity-100",
                isChosen && option.id !== "plan" && "border-primary/60 text-primary",
              )}
            >
              {isChosen && <Check className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />}
              <span className="truncate tabular-nums">{planButtonLabelAr(option)}</span>
            </Button>
          );
        })}
      </div>

      {statusLine && (
        <p
          className={cn(
            "flex items-center gap-1 text-xs",
            view.state === "chosen" ? "text-primary" : "text-muted-foreground",
          )}
          // Announce the lock/outcome, not the standing hint.
          role={view.state === "active" ? undefined : "status"}
        >
          {view.state === "chosen" && (
            <Check className="h-3 w-3 shrink-0" aria-hidden="true" />
          )}
          {view.state === "insufficient" && (
            <AlertCircle className="h-3 w-3 shrink-0" aria-hidden="true" />
          )}
          <span>{statusLine}</span>
        </p>
      )}
    </div>
  );
});
