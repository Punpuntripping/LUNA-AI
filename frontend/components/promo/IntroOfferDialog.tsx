"use client";

import { useEffect, useRef } from "react";
import Link from "next/link";
import { Check } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button, buttonVariants } from "@/components/ui/button";
import { useAuthStore } from "@/stores/auth-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { useIntroOfferStore } from "@/stores/intro-offer-store";
import { findPricingPlan } from "@/lib/pricing";
import { track } from "@/lib/analytics/client";
import { cn } from "@/lib/utils";
import { IntroOfferSummary } from "@/components/promo/IntroOfferSummary";
import {
  INTRO_OFFER_COPY,
  INTRO_OFFER_PLAN_ID,
} from "@/components/promo/intro-offer-content";

const PLAN_FEATURES = findPricingPlan(INTRO_OFFER_PLAN_ID)?.features ?? [];

/**
 * The profession offer — Pro tier 1 (`.claude/plans/pricing_campaigns_v2.md`,
 * superseding `profession_intro_offer.md` §4.2–4.3). Price AND months are the
 * server's (`offer_sar` × `offer_months`); nothing here names either.
 *
 * Opened by `OnboardingDialog` right after a profession answer the server
 * actually stored, and only if `GET /payments/intro-offer` then says the caller
 * is eligible — and by `BasicEndedOfferTrigger` once per ended Basic term
 * (`reason: "basic_ended"`). The quota wall shows the same offer inline on the
 * Pro card of `QuotaUpgradeDialog` instead of stacking a second modal on top.
 *
 * Also the one place the store is (re)loaded for the chat shell: keyed on the
 * account id, so a sign-out drops the offer and the next account starts from
 * "not eligible" until its own answer arrives.
 */
export function IntroOfferDialog() {
  const userId = useAuthStore((s) => s.user?.user_id ?? null);
  const offer = useIntroOfferStore((s) => s.offer);
  const isOpen = useIntroOfferStore((s) => s.isOpen);
  const trigger = useIntroOfferStore((s) => s.trigger);
  // Belt and braces: the offer is opened only AFTER the profession dialog
  // closed, but the two must never share the screen whatever opens next.
  const onboardingOpen = useOnboardingStore((s) => s.isOpen);

  useEffect(() => {
    if (!userId) {
      useIntroOfferStore.getState().reset();
      return;
    }
    void useIntroOfferStore.getState().refresh();
  }, [userId]);

  const visible = isOpen && !onboardingOpen && offer !== null;

  // One `intro_offer_shown` per opening, not per render.
  const trackedRef = useRef(false);
  useEffect(() => {
    if (!visible) {
      trackedRef.current = false;
      return;
    }
    if (trackedRef.current || !offer) return;
    trackedRef.current = true;
    track("intro_offer_shown", {
      trigger: trigger ?? "profession",
      group: offer.profession_group,
    });
  }, [visible, offer, trigger]);

  if (!offer) return null;

  const close = () => useIntroOfferStore.getState().close();

  return (
    <Dialog open={visible} onOpenChange={(next) => !next && close()}>
      <DialogContent
        className="max-w-sm"
        presentation="mobileSheet"
        dir="rtl"
        lang="ar"
        data-testid="intro-offer-dialog"
      >
        <DialogHeader>
          <DialogTitle>{INTRO_OFFER_COPY.headline(offer.profession_group)}</DialogTitle>
          <DialogDescription>
            {trigger === "basic_ended" && (
              <span className="mb-1 block">{INTRO_OFFER_COPY.basicEnded}</span>
            )}
            {INTRO_OFFER_COPY.lead(offer)}
          </DialogDescription>
        </DialogHeader>

        <IntroOfferSummary offer={offer} />

        {PLAN_FEATURES.length > 0 && (
          <ul className="space-y-1.5 text-sm">
            {PLAN_FEATURES.map((feature) => (
              <li key={feature} className="flex items-start gap-2">
                <Check className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden />
                <span>{feature}</span>
              </li>
            ))}
          </ul>
        )}

        <div className="flex flex-col gap-2 pt-1">
          <Link
            href={`/pay/${INTRO_OFFER_PLAN_ID}`}
            className={cn(buttonVariants(), "w-full font-semibold")}
            data-testid="intro-offer-cta"
            onClick={() => {
              track("intro_offer_cta", {
                trigger: trigger ?? "profession",
                group: offer.profession_group,
              });
              close();
            }}
          >
            {INTRO_OFFER_COPY.cta}
          </Link>
          <Button variant="ghost" className="w-full" onClick={close}>
            {INTRO_OFFER_COPY.later}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
