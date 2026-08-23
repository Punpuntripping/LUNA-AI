"use client";

import { useEffect } from "react";
import { useAuthStore } from "@/stores/auth-store";
import { usePreferencesStore } from "@/stores/preferences-store";
import {
  isPromoPopupOwed,
  isPromoPopupUndecided,
} from "@/components/promo/promo-campaign";

/**
 * Is the «عندك رمز تفعيل؟» popup owed to the signed-in account?
 *
 * Two consumers, and they must agree on the answer within a single render:
 * `PromoCodePopup` opens on it, and `OnboardingDialog` holds its own auto-open
 * while it is true. Deriving it in both places from the same stores — rather
 * than passing an `isOpen` flag between them — is what keeps the two dialogs
 * off the screen together on the very first pass, before any effect has run.
 *
 * Server-render safe by construction: `isHydrated` is false until the
 * preferences fetch resolves in the browser, so this is always `false` on the
 * server and there is no hydration mismatch to reconcile.
 */
export function usePromoPopupOwed(): boolean {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const planId = useAuthStore((s) => s.user?.plan_id);
  const isHydrated = usePreferencesStore((s) => s.isHydrated);
  const seen = usePreferencesStore((s) => s.promoCodePopupSeen);

  // ⚠ WITHOUT THIS THE POPUP NEVER OPENS ON THE SESSION IT IS FOR. The login
  // payload reports `plan_id: null` (it does not read `user_subscriptions`),
  // and the gate below tests for exactly `"free"` — so a brand-new signup would
  // be handed the popup only on their SECOND cold boot, long after the WhatsApp
  // code went stale in their clipboard. Ask once; the store no-ops after that.
  useEffect(() => {
    if (!isAuthenticated) return;
    void useAuthStore.getState().ensureSubscriptionLoaded();
    // Kick preferences too rather than relying on `OnboardingDialog` to do it —
    // this gate reads `promoCodePopupSeen`, and depending on a sibling
    // component's effect for our own input is the kind of coupling that breaks
    // silently the day that sibling moves.
    if (!usePreferencesStore.getState().isHydrated) {
      void usePreferencesStore.getState().hydrate();
    }
  }, [isAuthenticated, planId]);

  return isPromoPopupOwed({ isAuthenticated, isHydrated, seen, planId });
}

/**
 * Should «اتعرف على ريحان» stand down?
 *
 * True while the popup is owed AND while we are still waiting to find out —
 * see `isPromoPopupUndecided`. Onboarding must hold through both, or it opens
 * into the gap and the promo popup stacks on top of it.
 */
export function usePromoPopupHold(): boolean {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const planId = useAuthStore((s) => s.user?.plan_id);
  const subscriptionProbed = useAuthStore((s) => s.subscriptionProbed);
  const isHydrated = usePreferencesStore((s) => s.isHydrated);
  const seen = usePreferencesStore((s) => s.promoCodePopupSeen);

  const args = { isAuthenticated, isHydrated, seen, planId };
  return (
    isPromoPopupOwed(args) ||
    isPromoPopupUndecided({ ...args, subscriptionProbed })
  );
}
