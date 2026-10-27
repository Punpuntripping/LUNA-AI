"use client";

import { useEffect } from "react";
import { useAuthStore } from "@/stores/auth-store";
import { useIntroOfferStore } from "@/stores/intro-offer-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { usePreferencesStore } from "@/stores/preferences-store";
import { usePromoPopupHold } from "@/components/promo/use-promo-popup";

/** How often a waiting offer re-checks whether the screen is free. */
const RETRY_MS = 1000;

/** Any open Radix dialog/sheet — the same test `AnnouncementPopup` uses. */
function aDialogIsOpen(): boolean {
  return document.querySelector('[role="dialog"]') !== null;
}

/**
 * Opens the profession offer once when the caller's Basic term has ended
 * (`.claude/plans/pricing_campaigns_v2.md` §1.3-2 / §3).
 *
 * Active-Basic users are not offered Pro tier 1 while Basic runs. When the term
 * ends the server starts answering `GET /payments/intro-offer` with
 * `eligible && reason: "basic_ended"` plus the term's `basic_ended_at`, and this
 * component opens `IntroOfferDialog` (trigger `basic_ended`) on the next visit —
 * ONCE per `basic_ended_at` value, remembered in the
 * `intro_offer_basic_prompt_at` preference (`PATCH /preferences`), so a second
 * Basic purchase that also ends earns exactly one more prompt.
 *
 * Renders nothing. Data comes from the stores other dialogs already load —
 * `IntroOfferDialog` refreshes the offer on sign-in, and the preferences
 * hydrate is kicked by `OnboardingDialog` / `usePromoPopupOwed` — so this adds
 * no request of its own.
 *
 * Fail-closed on every input: no offer, an offer without the reason, an
 * unhydrated (or failed) preferences read, or the same value already shown —
 * all mean "do not open". It also waits for the screen to be free: the
 * activation-code popup, the profession question, an already-open offer, and
 * any other dialog (the same `[role="dialog"]` probe `AnnouncementPopup`
 * uses) all come first.
 */
export function BasicEndedOfferTrigger() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const offer = useIntroOfferStore((s) => s.offer);
  const offerBusy = useIntroOfferStore((s) => s.isOpen || s.checking);
  const onboardingOpen = useOnboardingStore((s) => s.isOpen);
  const promoPopupHold = usePromoPopupHold();
  const prefsHydrated = usePreferencesStore((s) => s.isHydrated);
  const lastShownFor = usePreferencesStore((s) => s.introOfferBasicPromptAt);

  const basicEndedAt =
    offer?.reason === "basic_ended" ? offer.basic_ended_at : null;

  const owed =
    isAuthenticated &&
    basicEndedAt !== null &&
    prefsHydrated &&
    // `undefined` = the preference is not known (failed hydrate) → never open.
    lastShownFor !== undefined &&
    lastShownFor !== basicEndedAt;

  const blocked = offerBusy || onboardingOpen || promoPopupHold;

  useEffect(() => {
    if (!owed || blocked || basicEndedAt === null) return;
    const attempt = (): boolean => {
      if (aDialogIsOpen()) return false;
      useIntroOfferStore.getState().open("basic_ended");
      void usePreferencesStore
        .getState()
        .markIntroOfferBasicPromptShown(basicEndedAt);
      return true;
    };
    if (attempt()) return;
    const timer = window.setInterval(() => {
      if (attempt()) window.clearInterval(timer);
    }, RETRY_MS);
    return () => window.clearInterval(timer);
  }, [owed, blocked, basicEndedAt]);

  return null;
}
