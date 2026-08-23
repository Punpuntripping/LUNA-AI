"use client";

import { useAuthStore } from "@/stores/auth-store";
import { usePreferencesStore } from "@/stores/preferences-store";
import { isPromoPopupOwed } from "@/components/promo/promo-campaign";

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

  return isPromoPopupOwed({ isAuthenticated, isHydrated, seen, planId });
}
