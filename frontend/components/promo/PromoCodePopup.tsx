"use client";

import { useEffect, useState } from "react";
import { Ticket } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { RedeemCodeForm } from "@/components/Settings/RedeemCodeForm";
import { usePreferencesStore } from "@/stores/preferences-store";
import { PROMO_POPUP_COPY } from "@/components/promo/promo-campaign";
import { usePromoPopupOwed } from "@/components/promo/use-promo-popup";

/**
 * «عندك رمز تفعيل؟» — the first thing a free account sees on /chat while the
 * two-week code campaign is open. Self-gating: renders null for everyone else,
 * and for everyone once the window closes (`promo-campaign.ts`).
 *
 * It sits ABOVE «اتعرف على ريحان» in the first-run order on purpose. The
 * profession question is ours to ask and can wait a beat; the code is the thing
 * the user arrived holding, off a WhatsApp message, and burying it behind a
 * modal they have to dismiss first is how a redemption gets lost. The stand-down
 * lives in `OnboardingDialog`, which re-runs its own auto-open the moment this
 * one resolves — so the profession step opens immediately after, never beside.
 *
 * ⚠ SHOWN ONCE, WHATEVER HAPPENS. `promo_code_popup_seen` is written on EVERY
 * exit — redeemed, «ليس لدي رمز», X, or ESC. A user without a code must be able
 * to make this go away permanently in one gesture; a promo that re-nags every
 * visit for a fortnight is worse than one nobody redeems.
 */
export function PromoCodePopup() {
  const owed = usePromoPopupOwed();
  const [open, setOpen] = useState(false);
  const [redeemed, setRedeemed] = useState(false);

  // Open once the gate turns true. Not derived straight from `owed`: marking it
  // seen flips `owed` to false, and a dialog whose `open` is the gate itself
  // would be yanked off the screen mid-success-card.
  useEffect(() => {
    if (owed) setOpen(true);
  }, [owed]);

  /** Every exit path lands here — see the once-only contract above. */
  const dismiss = () => {
    setOpen(false);
    void usePreferencesStore.getState().markPromoCodePopupSeen();
  };

  if (!owed && !open) return null;

  return (
    <Dialog open={open} onOpenChange={(next) => !next && dismiss()}>
      <DialogContent
        className="max-w-sm"
        presentation="mobileSheet"
        dir="rtl"
        lang="ar"
        data-testid="promo-code-popup"
      >
        <DialogHeader>
          <DialogTitle>{PROMO_POPUP_COPY.title}</DialogTitle>
          <DialogDescription>
            {PROMO_POPUP_COPY.description}
          </DialogDescription>
        </DialogHeader>

        {!redeemed && (
          <div className="flex items-center gap-2 rounded-md border border-primary/25 bg-primary/5 px-3 py-2 text-xs font-medium text-primary">
            <Ticket aria-hidden="true" className="h-4 w-4 shrink-0" />
            {PROMO_POPUP_COPY.scarcity}
          </div>
        )}

        <RedeemCodeForm
          onRedeemed={() => {
            setRedeemed(true);
            // Mark it here too, not only on close: the plan is already applied
            // server-side, so a user who redeems and then closes the tab must
            // never be asked again.
            void usePreferencesStore.getState().markPromoCodePopupSeen();
          }}
          footer={
            <Button
              variant="ghost"
              size="sm"
              className="w-full text-muted-foreground"
              onClick={dismiss}
              data-testid="promo-code-dismiss"
            >
              {PROMO_POPUP_COPY.dismiss}
            </Button>
          }
          successFooter={
            <Button
              className="w-full"
              onClick={dismiss}
              data-testid="promo-code-success-cta"
            >
              {PROMO_POPUP_COPY.successCta}
            </Button>
          }
        />
      </DialogContent>
    </Dialog>
  );
}
