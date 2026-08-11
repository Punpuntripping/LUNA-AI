"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useChatStore } from "@/stores/chat-store";
import { QuotaUpgradeDialog } from "@/components/chat/QuotaUpgradeDialog";
import { formatReset } from "@/lib/quota-reset";
import type { SSEQuotaExceeded } from "@/types";

/**
 * A quota block offers plans only when the user is actually on the free plan.
 *
 *   - `"free"`        → the paywall: nothing resets soon enough to matter, and
 *                       buying is the only way forward. Opens the dialog.
 *   - `null`          → `PlanInactive`. The account is not activated; a purchase
 *                       does NOT unlock it, so selling here would take money for
 *                       nothing. Banner only.
 *   - a paid plan     → they already bought. They need the reset time, not a
 *                       pitch. Banner only.
 *   - `undefined`     → backend predates the `plan_id` field (deploy skew).
 *                       Falls through to banner-only: showing no dialog to a
 *                       free user is a missed upsell; showing one to a paying
 *                       user is a bad experience. Fail toward the quiet option.
 */
function shouldOfferUpgrade(info: SSEQuotaExceeded | null): boolean {
  return info?.plan_id === "free";
}

export function QuotaBanner() {
  const quotaInfo = useChatStore((s) => s.quotaInfo);
  const setQuotaInfo = useChatStore((s) => s.setQuotaInfo);
  const [now, setNow] = useState<number>(() => Date.now());
  const [dialogOpen, setDialogOpen] = useState(false);

  useEffect(() => {
    if (!quotaInfo) return;
    const id = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(id);
  }, [quotaInfo]);

  // Auto-open on each NEW block, but never re-open the one the user just
  // closed. `quotaInfo` is a fresh object per SSE event, so identity is the
  // signal: same object → the user dismissed it, leave it shut; new object →
  // they hit the wall again, show the plans.
  const shownFor = useRef<SSEQuotaExceeded | null>(null);
  useEffect(() => {
    if (!quotaInfo || !shouldOfferUpgrade(quotaInfo)) return;
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
          {/* The way back to the plans after the modal is closed — without it,
              dismissing the dialog strands the user with no route to buy. */}
          {offersUpgrade && (
            <Button
              variant="outline"
              size="sm"
              className="h-7 border-warning-fg/30 bg-transparent text-xs text-warning-fg hover:bg-warning-fg/10 hover:text-warning-fg"
              onClick={handleReopen}
            >
              عرض الباقات
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
