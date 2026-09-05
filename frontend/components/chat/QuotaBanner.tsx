"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useChatStore } from "@/stores/chat-store";
import { QuotaUpgradeDialog } from "@/components/chat/QuotaUpgradeDialog";
import { formatReset } from "@/lib/quota-reset";
import type { SSEQuotaExceeded } from "@/types";

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
function shouldOfferUpgrade(info: SSEQuotaExceeded | null): boolean {
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
function shouldAutoOpen(info: SSEQuotaExceeded | null): boolean {
  return shouldOfferUpgrade(info) && info?.plan_id === "free";
}

export function QuotaBanner() {
  const quotaInfo = useChatStore((s) => s.quotaInfo);
  const setQuotaInfo = useChatStore((s) => s.setQuotaInfo);
  const [now, setNow] = useState<number>(() => Date.now());
  const [dialogOpen, setDialogOpen] = useState(false);

  // Re-stamp on every new block, then tick. The banner mounts with the chat
  // and only *renders* once `quotaInfo` arrives, so the lazy initialiser above
  // holds page-load time — minutes or hours stale by the time a block fires.
  // Same defect as `UsageLimitsDialog`: it inflates the countdown past the
  // window length instead of shrinking it, so it reads as a backend bug.
  useEffect(() => {
    if (!quotaInfo) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(id);
  }, [quotaInfo]);

  // Auto-open on each NEW block, but never re-open the one the user just
  // closed. `quotaInfo` is a fresh object per SSE event, so identity is the
  // signal: same object → the user dismissed it, leave it shut; new object →
  // they hit the wall again, show the plans.
  const shownFor = useRef<SSEQuotaExceeded | null>(null);
  useEffect(() => {
    if (!quotaInfo || !shouldAutoOpen(quotaInfo)) return;
    if (shownFor.current === quotaInfo) return;
    shownFor.current = quotaInfo;
    setDialogOpen(true);
  }, [quotaInfo]);

  const resetText = useMemo(
    () => (quotaInfo?.resets_at ? formatReset(quotaInfo.resets_at, now) : ""),
    [quotaInfo, now],
  );

  const handleDismiss = useCallback(() => setQuotaInfo(null), [setQuotaInfo]);
  const handleReopen = useCallback(() => setDialogOpen(true), []);

  if (!quotaInfo) return null;

  const offersUpgrade = shouldOfferUpgrade(quotaInfo);

  return (
    <>
      <div
        dir="rtl"
        lang="ar"
        role="alert"
        className="flex items-center justify-between gap-2 border-b border-warning-fg/25 bg-warning px-4 py-2"
      >
        <div className="flex flex-col gap-0.5">
          <p className="text-sm text-warning-fg">{quotaInfo.message_ar}</p>
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
          {offersUpgrade && (
            <Button
              variant="outline"
              size="sm"
              className="h-7 border-warning-fg/30 bg-transparent text-xs text-warning-fg hover:bg-warning-fg/10 hover:text-warning-fg"
              onClick={handleReopen}
              data-testid="quota-upgrade-open"
            >
              {quotaInfo.plan_id === "free" ? "عرض الباقات" : "ترقية الباقة"}
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

      {offersUpgrade && (
        <QuotaUpgradeDialog
          open={dialogOpen}
          onOpenChange={setDialogOpen}
          info={quotaInfo}
        />
      )}
    </>
  );
}
