import { findPricingPlan, formatSar, parsePromoMonths } from "@/lib/pricing";
import { offerSpan } from "@/components/promo/intro-offer-content";
import type { ErrorCode } from "@/lib/contracts/errors";
import type {
  DowngradeBlockedReason,
  DowngradeOption,
  ScheduleDowngradeRequest,
  SubscriptionState,
} from "@/types";

/**
 * «تغيير الباقة» — the scheduled downgrade's copy and wire parsing, in ONE
 * place (`.claude/plans/pro_plus_tier_and_downgrade.md` §4.1 + §8). Pure: no
 * React, so every sentence the user agrees to can be read (and tested) here.
 *
 * Rules every sentence below keeps:
 *   - The switch happens AT THE NEXT RENEWAL, never now (plan D6): nothing is
 *     charged, refunded or shortened today, and every sentence says «حتى …» /
 *     «عند التجديد» rather than implying an immediate change.
 *   - The price is the server's (`downgrade_options`, `pending_price_sar`).
 *     Owner rule 2026-11-20, the PROMISE: the price shown when the switch is
 *     scheduled (offer and its months included) is what the renewal charges —
 *     or the renewal-day price if that is lower — even if the offer runs out
 *     or the plan stops being sold. Nothing is computed here except formatting.
 *   - Latin digits only (`formatSar`, and the dialog's own date formatter).
 *   - Month counts go through `offerSpan()` — the same dual/plural handling the
 *     intro offer uses («للشهر الأول» · «لأول شهرين» · «لأول 3 أشهر»).
 */

/** A wire amount («59.90» or 59.9) → a finite positive number, or null. */
function money(value: unknown): number | null {
  if (typeof value !== "string" && typeof value !== "number") return null;
  if (typeof value === "string" && value.trim() === "") return null;
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : null;
}

/**
 * A valid wire amount, kept AS SENT — «94.90» stays «94.90», never «94.9».
 * The string goes back to the server as `expected_price_sar`, which must be
 * the exact price the confirm sheet showed, so it is never re-serialised from
 * a float. A bare number (not the documented shape) gets two decimals.
 */
function wireAmount(value: unknown): string {
  return typeof value === "string" ? value.trim() : Number(value).toFixed(2);
}

/**
 * `downgrade_options` → entries safe to render, nearest step down FIRST (max's
 * list: pro_plus, then pro).
 *
 * Defensive because the backend is new: an entry with no plan id or no list
 * price is dropped rather than rendered as «NaN ريال»; the current plan is
 * never offered as a "change"; an offer that is not strictly below the list
 * price is ignored (the same rule `resolvePlanPricing` applies to cards); a
 * month count outside 1..12 degrades to the months-free wording. An absent
 * field (older backend) is simply `[]` — no «تغيير الباقة» at all. Amounts
 * keep their wire strings (`wireAmount`).
 */
export function readDowngradeOptions(
  state: SubscriptionState | null | undefined,
): DowngradeOption[] {
  const raw: unknown = state?.downgrade_options;
  if (!Array.isArray(raw)) return [];

  const options: DowngradeOption[] = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== "object") continue;
    const e = entry as Record<string, unknown>;
    const planId = typeof e.plan_id === "string" ? e.plan_id.trim() : "";
    if (!planId || planId === state?.plan_id) continue;
    const list = money(e.price_sar);
    if (list === null) continue;
    const name =
      typeof e.name_ar === "string" && e.name_ar.trim()
        ? e.name_ar.trim()
        : findPricingPlan(planId)?.nameAr;
    if (!name) continue;
    const promo = money(e.promo_price_sar);
    const hasPromo = promo !== null && promo < list;
    options.push({
      plan_id: planId,
      name_ar: name,
      price_sar: wireAmount(e.price_sar),
      promo_price_sar: hasPromo ? wireAmount(e.promo_price_sar) : null,
      promo_months: hasPromo ? parsePromoMonths(e.promo_months) : null,
    });
  }
  return options.sort((a, b) => Number(b.price_sar) - Number(a.price_sar));
}

/**
 * The price the confirm sheet tells the user the renewal will charge — the
 * offer when one is shown, else the list price — as the exact wire string.
 * Sent as `expected_price_sar`; the same field `downgradePricePhrase()` leads
 * with, so what is sent can never differ from what was read.
 */
export function expectedRenewalPriceSar(option: DowngradeOption): string {
  return option.promo_price_sar ?? option.price_sar;
}

/**
 * The offer months the confirm sheet showed — the option's `promo_months` when
 * an offer price is on screen, else null (a list price has no months). Sent as
 * `expected_promo_months`.
 */
export function expectedRenewalPromoMonths(
  option: DowngradeOption,
): number | null {
  return option.promo_price_sar !== null ? option.promo_months : null;
}

/**
 * The exact POST body for scheduling `option` — the one place it is built, so
 * the confirm sheet and the retention card cannot send different shapes.
 */
export function scheduleDowngradeBody(
  option: DowngradeOption,
): ScheduleDowngradeRequest {
  return {
    plan_id: option.plan_id,
    expected_price_sar: expectedRenewalPriceSar(option),
    expected_promo_months: expectedRenewalPromoMonths(option),
  };
}

/**
 * `downgrade_blocked_reason`, narrowed to the values this UI has copy for.
 * Anything else (absent, null, a reason added later) reads as null: the
 * confirm button stays, and the POST answers with its own Arabic message.
 */
export function downgradeBlockedReason(
  state: SubscriptionState | null | undefined,
): DowngradeBlockedReason | null {
  return state?.downgrade_blocked_reason === "no_payment_method"
    ? "no_payment_method"
    : null;
}

/**
 * The whole answer while no card is stored (`no_payment_method`), shown
 * INSTEAD of the confirm sheet and of the retention offer. Without a stored
 * card nothing renews at all — and a card can only be saved by completing a
 * payment, so there is no "add a card" step to point to. The honest answer is
 * that the term ends and the lower plan can be bought after it:
 * «لا توجد بطاقة محفوظة لتجديد اشتراكك تلقائياً، لذلك لن يُجدَّد عند انتهاء
 * مدته في 20 ديسمبر 2026. يمكنك الاشتراك في الباقة الأقل بعد انتهاء اشتراكك
 * الحالي.» — the date only when known (already formatted, Latin digits).
 */
export function downgradeNoMethodNote(termEndsAt: string | null): string {
  const end = termEndsAt ? `عند انتهاء مدته في ${termEndsAt}` : "عند انتهاء مدته";
  return `لا توجد بطاقة محفوظة لتجديد اشتراكك تلقائياً، لذلك لن يُجدَّد ${end}. يمكنك الاشتراك في الباقة الأقل بعد انتهاء اشتراكك الحالي.`;
}

/** The single button under that note: it only closes. */
export const DOWNGRADE_NO_METHOD_DISMISS = "حسناً";

/**
 * After `SUBSCRIPTION_DOWNGRADE_PRICE_CHANGED`, when the server sent no Arabic
 * text of its own: the re-read price is already on screen above it.
 */
export const DOWNGRADE_PRICE_CHANGED_FALLBACK =
  "تغيّر سعر هذه الباقة. راجع السعر الجديد أعلاه ثم أكّد من جديد.";

export const DOWNGRADE_PRICE_CHANGED_CODE: ErrorCode =
  "SUBSCRIPTION_DOWNGRADE_PRICE_CHANGED";
export const DOWNGRADE_NO_METHOD_CODE: ErrorCode =
  "SUBSCRIPTION_DOWNGRADE_NO_METHOD";

/**
 * The machine code on a failed request (`ApiClientError.code`), or null. Read
 * structurally so this module stays free of the API client.
 */
export function errorCodeOf(error: unknown): string | null {
  if (!error || typeof error !== "object") return null;
  const code = (error as { code?: unknown }).code;
  return typeof code === "string" ? code : null;
}

export function isPriceChangedError(error: unknown): boolean {
  return errorCodeOf(error) === DOWNGRADE_PRICE_CHANGED_CODE;
}

/**
 * The retention offer's plan: the CHEAPEST step down, by list price (the plan
 * rank — an offer price never reorders the ladder, see `pricingPlansAbove`).
 */
export function cheapestDowngrade(
  options: DowngradeOption[],
): DowngradeOption | null {
  return options.length > 0 ? options[options.length - 1] : null;
}

/**
 * «94.90 ريال شهرياً», or with an offer
 * «59.90 ريال لأول شهرين، ثم 94.90 ريال شهرياً» — the intro offer's schedule
 * sentence shape (`introOfferSchedule`), so the two never read differently.
 */
export function downgradePricePhrase(option: DowngradeOption): string {
  const list = `${formatSar(option.price_sar)} ريال شهرياً`;
  if (option.promo_price_sar === null) return list;
  const span =
    option.promo_months === null ? "لمدة محددة" : offerSpan(option.promo_months);
  return `${formatSar(option.promo_price_sar)} ريال ${span}، ثم ${list}`;
}

/**
 * The confirm sheet's sentence (plan §4.1):
 * «ستبقى على باقة القصوى حتى 20 ديسمبر 2026، ثم تنتقل إلى باقة الاحترافية بلس
 * بسعر 149.90 ريال شهرياً.»
 *
 * `termEndsAt` is the already-formatted renewal date; null (no date on the
 * subscription) words the same thing without one.
 */
export function downgradeConfirmSentence(
  currentPlanName: string,
  termEndsAt: string | null,
  option: DowngradeOption,
): string {
  const until = termEndsAt ? `حتى ${termEndsAt}` : "حتى نهاية مدتك الحالية";
  return `ستبقى على باقة ${currentPlanName} ${until}، ثم تنتقل إلى باقة ${option.name_ar} بسعر ${downgradePricePhrase(option)}.`;
}

/** Said under every confirm: what a scheduled change does NOT do. */
export const DOWNGRADE_NOTHING_NOW =
  "لا يُخصم منك شيء الآن، ولا تُسترد مدتك الحالية ولا تُختصر. ويمكنك التراجع قبل موعد التجديد.";

/**
 * Said under every price a switch is scheduled at (confirm sheet + retention
 * offer). Owner rule 2026-11-20: the price shown when the switch is scheduled
 * is RESERVED — offer and months included, even if the offer runs out or the
 * plan stops being sold — and the renewal charges the renewal-day price
 * instead only when that one is lower. True for a list price too, so it is not
 * limited to offers (the promo-terms link still is).
 */
export const DOWNGRADE_PRICE_PROMISE =
  "هذا السعر محفوظ لك عند التجديد، وإن كان السعر يومئذٍ أقل دفعت الأقل.";

/** The picker's foot: why Basic is never a "change" (D8). */
export const DOWNGRADE_BASIC_NOTE =
  "للتوقف عن التجديد كلياً، استخدم «إلغاء الاشتراك».";

/**
 * The pending banner's price, from the GET payload: `pending_price_sar` is the
 * stored promise (the price shown when the switch was scheduled — the renewal
 * charges it, or a lower renewal-day price), and `pending_promo_months` (when
 * set) says it is an offer for that many terms. Never null while a switch is
 * pending; if it ever is, this returns null and the banner names no price.
 *
 * ⚠ The payload has no list price for the pending plan, so the «ثم …» step-up
 * is read from `downgrade_options` (the same plan, same quote) or, failing
 * that, from the catalog card. With neither, the sentence names the offer and
 * its span and stops — never a guessed number.
 */
export function pendingPricePhrase(
  state: SubscriptionState,
  options: DowngradeOption[],
): string | null {
  const price = money(state.pending_price_sar);
  if (price === null) return null;
  const months = parsePromoMonths(state.pending_promo_months);
  const pendingId = state.pending_plan_id ?? "";
  if (months === null) return `${formatSar(price)} ريال شهرياً`;
  const list = money(
    options.find((o) => o.plan_id === pendingId)?.price_sar ??
      findPricingPlan(pendingId)?.price,
  );
  const offer = `${formatSar(price)} ريال ${offerSpan(months)}`;
  return list !== null && price < list
    ? `${offer}، ثم ${formatSar(list)} ريال شهرياً`
    : offer;
}

/**
 * The pending banner (plan §4.1):
 * «سيتم تحويل باقتك إلى «الاحترافية» عند التجديد في 20 ديسمبر 2026، بسعر
 * 94.90 ريال شهرياً.»
 *
 * `renewsOn` null → no date (e.g. mid renewal-retry, when the renewal is
 * happening now rather than on a future day). `pricePhrase` null → no price:
 * the defensive fallback for a payload missing the promise, which the backend
 * never sends while a switch is pending.
 */
export function pendingDowngradeSentence(
  planName: string,
  renewsOn: string | null,
  pricePhrase: string | null,
): string {
  const when = renewsOn ? `عند التجديد في ${renewsOn}` : "عند التجديد";
  const price = pricePhrase ? `، بسعر ${pricePhrase}` : "";
  return `سيتم تحويل باقتك إلى «${planName}» ${when}${price}.`;
}

/**
 * The message to show for a failed plan-change request: the backend's own
 * Arabic `detail` (every 409 here carries one), else `fallback`. A transport
 * failure surfaces as an English `TypeError` («Failed to fetch»), and
 * `apiFetch` falls back to «Request failed» / the HTTP status text when a body
 * will not parse — none of which may reach an Arabic UI.
 */
export function planChangeErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && /[\u0600-\u06FF]/.test(error.message)) {
    return error.message;
  }
  return fallback;
}

/** The cancel dialog, while a switch is pending: cancelling drops it (§4.1). */
export function cancelDropsPendingSentence(planName: string): string {
  return `إلغاء الاشتراك يلغي أيضاً تحويل باقتك المجدول إلى «${planName}».`;
}

/**
 * The retention offer inside the cancel flow, for reason `expensive`:
 * «بدلاً من الإلغاء، انتقل إلى باقة الاحترافية بسعر 94.90 ريال شهرياً.»
 */
export function retentionOfferSentence(option: DowngradeOption): string {
  return `بدلاً من الإلغاء، انتقل إلى باقة ${option.name_ar} بسعر ${downgradePricePhrase(option)}.`;
}

/** The line under it: same at-renewal timing as the confirm sheet. */
export function retentionTimingSentence(termEndsAt: string | null): string {
  return termEndsAt
    ? `تبقى على باقتك الحالية حتى ${termEndsAt}، ويبدأ التحويل عند التجديد.`
    : "تبقى على باقتك الحالية حتى نهاية مدتها، ويبدأ التحويل عند التجديد.";
}

export function retentionButtonLabel(option: DowngradeOption): string {
  return `الانتقال إلى باقة ${option.name_ar}`;
}
