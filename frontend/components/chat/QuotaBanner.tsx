"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { X } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/stores/chat-store";
import { QuotaUpgradeDialog } from "@/components/chat/QuotaUpgradeDialog";
import { formatReset } from "@/lib/quota-reset";
import type { SSEDeepSearchGated, SSEQuotaExceeded } from "@/types";

/** The two refusals that carry a countdown and an upgrade ladder. */
type OfferInfo = SSEQuotaExceeded | SSEDeepSearchGated;

/**
 * Whether this block has anything to sell — i.e. whether the SERVER found a
 * plan that would actually unblock this user.
 *
 * `upgrade_options` is computed on the block path from the window that tripped:
 * purchasable, priced above the current plan, and with a strictly higher limit
 * on that window. Reading it here instead of re-deriving a ladder from
 * `plan_id` keeps the offer honest in the cases a plan name cannot express —
 * `marketing_lawyer` blocked on the 5-hour session is offered `max` only,
 * because `pro`'s session limit ties theirs and would change nothing.
 *
 *   - non-empty  → show the button (and, for free, the modal — see below).
 *   - empty      → `max` is already at the top of the ladder, or the account is
 *                  not activated (`PlanInactive`, which a purchase does not
 *                  fix). Banner only: there is nothing to offer.
 *   - undefined  → backend predates the ladder (deploy skew). Treated as empty,
 *                  so the failure mode is a missed upsell rather than a pitch
 *                  that cannot help — the same "fail toward the quiet option"
 *                  choice this component made when `plan_id` was new.
 */
function shouldOfferUpgrade(info: OfferInfo | null): boolean {
  return (info?.upgrade_options?.length ?? 0) > 0;
}

/**
 * The modal opens BY ITSELF for free users only (locked decision 9).
 *
 * A paying subscriber who ran their window down has already bought; a
 * full-screen sales modal thrown at them mid-work reads as a shakedown, not as
 * help. They get the banner and a «ترقية الباقة» button, and the same dialog
 * opens the moment they ask for it. `plan_id` is EFFECTIVE, so an expired
 * subscription that fell back to `free` correctly gets the paywall again.
 */
function shouldAutoOpen(info: OfferInfo | null): boolean {
  return shouldOfferUpgrade(info) && info?.plan_id === "free";
}

interface QuotaBannerProps {
  /** The banner shows only refusals of sends made in THIS conversation. */
  conversationId: string;
}

/**
 * Refusal banner for the conversation's last blocked send. Four shapes
 * (parallel_conversations plan §2, deep_search_plan_gate plan §4):
 *
 * - `quota_exceeded` (limit) — the user ran a window down: reset countdown +
 *   the upgrade ladder, exactly as before.
 * - `quota_exceeded` with `reason: "parallel_reserve"` — the user is NOT out
 *   of points; the balance just cannot cover another run alongside the ones in
 *   flight. `detail` only — no countdown, no upgrade pitch.
 * - `parallel_limit` — the plan's cap on conversations answering at once. Its
 *   `detail`, plus a link to the pricing page when «القصوى» would lift the cap.
 * - `deep_search_gated` — the plan gate refused to start a deep search. The
 *   turn was answered (the refusal is the reply in the thread); this is the
 *   same countdown + ladder treatment as a spent window.
 */
export function QuotaBanner({ conversationId }: QuotaBannerProps) {
  const notice = useChatStore(
    (s) => s.noticeByConversation[conversationId] ?? null,
  );
  const setNotice = useChatStore((s) => s.setNotice);
  // The "you've hit your limit" refusal. Null for the parallel shapes.
  const quotaInfo =
    notice?.kind === "quota" && notice.info.reason !== "parallel_reserve"
      ? notice.info
      : null;
  const gateInfo = notice?.kind === "deep_search_gated" ? notice.info : null;
  // The shapes with a countdown and an upgrade ladder.
  const offerInfo: OfferInfo | null = quotaInfo ?? gateInfo;
  const [now, setNow] = useState<number>(() => Date.now());
  const [dialogOpen, setDialogOpen] = useState(false);

  // Re-stamp on every new block, then tick. The banner mounts with the chat
  // and only *renders* once `offerInfo` arrives, so the lazy initialiser above
  // holds page-load time — minutes or hours stale by the time a block fires.
  // Same defect as `UsageLimitsDialog`: it inflates the countdown past the
  // window length instead of shrinking it, so it reads as a backend bug.
  useEffect(() => {
    if (!offerInfo) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(id);
  }, [offerInfo]);

  // Auto-open on each NEW block, but never re-open the one the user just
  // closed. `offerInfo` is a fresh object per SSE event, so identity is the
  // signal: same object → the user dismissed it, leave it shut; new object →
  // they hit the wall again, show the plans.
  const shownFor = useRef<OfferInfo | null>(null);
  useEffect(() => {
    if (!offerInfo || !shouldAutoOpen(offerInfo)) return;
    if (shownFor.current === offerInfo) return;
    shownFor.current = offerInfo;
    setDialogOpen(true);
  }, [offerInfo]);

  const resetText = useMemo(
    () => (offerInfo?.resets_at ? formatReset(offerInfo.resets_at, now) : ""),
    [offerInfo, now],
  );

  const handleDismiss = useCallback(
    () => setNotice(conversationId, null),
    [setNotice, conversationId],
  );
  const handleReopen = useCallback(() => setDialogOpen(true), []);

  if (!notice) return null;

  const offersUpgrade = offerInfo !== null && shouldOfferUpgrade(offerInfo);
  const message =
    notice.kind === "parallel_limit"
      ? notice.info.detail
      : notice.kind === "deep_search_gated"
        ? notice.info.message_ar
        : quotaInfo
          ? quotaInfo.message_ar
          : (notice.info.detail ?? notice.info.message_ar);
  // Stop does NOT free a slot (the run keeps going in the background), so the
  // only route past the cap besides waiting is a plan with a higher one.
  const offersMaxLink =
    notice.kind === "parallel_limit" && notice.info.upgrade_plan === "max";

  return (
    <>
      <div
        dir="rtl"
        lang="ar"
        role="alert"
        className="flex items-center justify-between gap-2 border-b border-warning-fg/25 bg-warning px-4 py-2"
      >
        <div className="flex flex-col gap-0.5">
          <p className="text-sm text-warning-fg">{message}</p>
          {resetText && (
            <p className="text-xs text-warning-fg/80">
              يُعاد الاحتساب {resetText}.
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {/* For a free user this is the way back to the plans after the modal
              is closed — without it, dismissing strands them with no route to
              buy. For a paying user it is the ONLY route, by design: the offer
              is available on request and never pushed. */}
          {offersMaxLink && (
            <Link
              href="/pricing"
              data-testid="parallel-limit-upgrade"
              className={cn(
                buttonVariants({ variant: "outline", size: "sm" }),
                "h-7 border-warning-fg/30 bg-transparent text-xs text-warning-fg hover:bg-warning-fg/10 hover:text-warning-fg",
              )}
            >
              عرض باقة «القصوى»
            </Link>
          )}
          {offersUpgrade && offerInfo && (
            <Button
              variant="outline"
              size="sm"
              className="h-7 border-warning-fg/30 bg-transparent text-xs text-warning-fg hover:bg-warning-fg/10 hover:text-warning-fg"
              onClick={handleReopen}
              data-testid="quota-upgrade-open"
            >
              {offerInfo.plan_id === "free" ? "عرض الباقات" : "ترقية الباقة"}
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6 text-warning-fg hover:text-warning-fg"
            onClick={handleDismiss}
            aria-label="إغلاق"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {offersUpgrade && quotaInfo && (
        <QuotaUpgradeDialog
          open={dialogOpen}
          onOpenChange={setDialogOpen}
          info={quotaInfo}
        />
      )}
      {offersUpgrade && gateInfo && (
        <QuotaUpgradeDialog
          open={dialogOpen}
          onOpenChange={setDialogOpen}
          deepSearchGate={gateInfo}
        />
      )}
    </>
  );
}
