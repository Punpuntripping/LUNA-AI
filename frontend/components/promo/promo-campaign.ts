/**
 * «عندك رمز تفعيل؟» — the two-week activation-code push (WhatsApp lawyer
 * outreach, opened 2026-08-23).
 *
 * ONE place for the window and the gate, imported by both the popup itself and
 * by `OnboardingDialog`, which has to stand down while this is owed.
 *
 * ⚠ THE CAMPAIGN SELF-EXPIRES. `PROMO_WINDOW_ENDS_AT` is the whole off switch:
 * past that instant `isPromoWindowOpen()` is false forever, the popup stops
 * mounting anything, and «اتعرف على ريحان» goes back to opening first. Nothing
 * has to be deployed, flipped or cleaned up to end it — which is the point of
 * a fortnight-long campaign that nobody will remember to turn off.
 *
 * ⚠ KEEP THIS IN STEP WITH THE CODE'S OWN EXPIRY. The activation code is minted
 * with a matching `--valid-days`, and the popup asking for a code the server
 * has already retired is the one failure worth avoiding: the user types the
 * string off WhatsApp and gets «الرمز غير صالح». If the window here moves, remint
 * or extend `plan_codes.expires_at` to match.
 */

/** 2026-09-07 00:00 Riyadh (UTC+3) — the first instant the campaign is over. */
export const PROMO_WINDOW_ENDS_AT = "2026-09-06T21:00:00Z";

export function isPromoWindowOpen(now: number = Date.now()): boolean {
  const ends = Date.parse(PROMO_WINDOW_ENDS_AT);
  return !Number.isNaN(ends) && now < ends;
}

export interface PromoPopupGateInput {
  isAuthenticated: boolean;
  /** Preferences hydration completed — false keeps the gate CLOSED. */
  isHydrated: boolean;
  /** `promo_code_popup_seen` — fail-closed, defaults to true in the store. */
  seen: boolean;
  /** `user.plan_id` straight off /auth/me. */
  planId: string | null | undefined;
  now?: number;
}

/**
 * Is the popup owed to this account right now?
 *
 * ⚠ `planId === "free"` is an EXACT string test. Everything else reads as no:
 * pro, max, basic, dev and marketing_lawyer already hold a plan (a code would
 * be refused with «باقتك مفعّلة بالفعل»), and `null`/`undefined` mean the plan
 * is not known yet — see `isPromoPopupUndecided`, which is what covers the gap
 * rather than a guess here. Prompting a paying subscriber for an activation
 * code they neither have nor need is the worse error, so unknown is never
 * optimistically treated as free.
 */
export function isPromoPopupOwed({
  isAuthenticated,
  isHydrated,
  seen,
  planId,
  now,
}: PromoPopupGateInput): boolean {
  if (!isAuthenticated || !isHydrated) return false;
  if (seen) return false;
  if (planId !== "free") return false;
  return isPromoWindowOpen(now);
}

/**
 * Do we not YET know whether the popup is owed?
 *
 * ⚠ THIS IS WHAT KEEPS THE TWO DIALOGS FROM STACKING. The login payload carries
 * `"plan_id": null` (MEASURED — it is serialized, not omitted), so `owed` above
 * is false at first even for the brand-new free account the campaign exists
 * for. Without this, «اتعرف على ريحان» opens into that gap and the promo popup
 * lands on top of it a moment later, once /auth/me answers `"free"`.
 *
 * ⚠ NOT `planId === undefined`. That was the first attempt and it never fired:
 * the wire value is `null`, and `null` is ALSO what a genuinely locked account
 * reports, so the two cannot be told apart from the value alone. Only a string
 * settles it; `subscriptionProbed` settles the rest. Once the probe returns,
 * an unknown plan stops being "wait" and becomes a plain no, so a failed
 * /auth/me can never hold onboarding shut for the rest of the campaign.
 */
export function isPromoPopupUndecided({
  isAuthenticated,
  isHydrated,
  seen,
  planId,
  subscriptionProbed,
  now,
}: PromoPopupGateInput & { subscriptionProbed: boolean }): boolean {
  if (!isAuthenticated || !isHydrated) return false;
  if (seen) return false;
  if (typeof planId === "string") return false;
  if (subscriptionProbed) return false;
  return isPromoWindowOpen(now);
}

/** Copy — kept beside the window so the campaign is one file to read. */
export const PROMO_POPUP_COPY = {
  title: "عندك رمز تفعيل؟",
  description:
    "إذا وصلك رمز من ريحان، فعّله الآن وتبدأ باقتك على طول.",
  // ⚠ «المقاعد محدودة» AND NOTHING MORE. No remaining count, no total, no
  // closing date — the standing rule for every Rayhan scarcity signal
  // (early_adopters.md §1.10). The cap lives in `plan_codes.max_uses`, which
  // no surface may read out loud.
  scarcity: "المقاعد محدودة لهذا الرمز المجاني.",
  dismiss: "ليس لدي رمز",
  successCta: "ابدأ الاستخدام",
} as const;
