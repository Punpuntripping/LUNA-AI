import {
  findPricingPlan,
  formatSar,
  parsePromoMonths,
  promoMonthsPhrase,
} from "@/lib/pricing";
import type { CheckoutPromo, IntroOfferState } from "@/types";

/**
 * Single source for the profession-offer copy (Pro tier 1 —
 * `.claude/plans/pricing_campaigns_v2.md` §1.1/§4, superseding
 * `profession_intro_offer.md` §4.2). The dialog, the quota-wall block and /pay
 * all render from here, so the three can never quote the offer differently.
 * /pay also renders a plain promo quote (`termsFromCheckoutPromo`) through the
 * same schedule helpers.
 *
 * ⚠ Every amount AND every month count comes from the SERVER
 * (`IntroOfferState` / `CheckoutIntroOffer` / `CheckoutPromo`); nothing here
 * hard-codes a price or a number of months — seat batches change both with no
 * deploy. ⚠ No end date, no seat count, no batch, no tier number —
 * «المقاعد محدودة» (`SEATS_LIMITED_NOTE`) is the only scarcity signal.
 */

/** The plan the offer sells — its display name comes from the catalog. */
export const INTRO_OFFER_PLAN_ID = "pro" as const;

const PLAN_NAME = findPricingPlan(INTRO_OFFER_PLAN_ID)?.nameAr ?? "الاحترافية";

// -----------------------------------------------
// The schedule, normalised from either backend generation
// -----------------------------------------------

/**
 * What the buyer pays, in order:
 *   1. `offer_sar` for each of the first `offer_months` 30-day terms — the first
 *      of them is the payment being made now;
 *   2. ONLY on a backend from before batches: `then_sar` for `then_months` more
 *      terms (the old early-adopter seat price after the 1.99 / 9.90 month);
 *   3. `list_sar` every 30 days after that.
 *
 * 2-dp LATIN strings, exactly as they arrived. `then_sar` is `null` whenever
 * the middle step does not exist — always, on the current backend.
 */
export interface OfferTerms {
  offer_sar: string;
  offer_months: number;
  then_sar: string | null;
  then_months: number;
  list_sar: string;
}

/** The money keys an offer can arrive with, on either backend generation. */
type OfferWire = Partial<
  Pick<
    IntroOfferState,
    | "first_month_sar"
    | "then_sar"
    | "then_months"
    | "list_sar"
    | "offer_sar"
    | "offer_months"
  >
>;

const MONEY_RE = /^\d+(\.\d{1,2})?$/;

/** A 2-dp wire string, or null. Strings only — a number here is a backend bug. */
function money(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return MONEY_RE.test(trimmed) ? trimmed : null;
}

/**
 * The sale check (pricing_campaigns_v2 §3: `offer_sar < list_sar`), plus, on
 * the legacy shape, a middle step that must not exceed the list price. An
 * "offer" that fails it is not an offer, and is not advertised.
 */
function isSale(terms: OfferTerms): boolean {
  const offer = Number(terms.offer_sar);
  const list = Number(terms.list_sar);
  if (!(offer >= 0 && offer < list)) return false;
  if (terms.then_sar === null) return true;
  return Number(terms.then_sar) <= list;
}

/**
 * Wire → `OfferTerms`, or `null` (fail-closed: anything short of a complete,
 * coherent schedule is "no offer").
 *
 * Prefers the current keys (`offer_sar` + `offer_months`), and only when BOTH
 * are valid. A response without them — an older backend — or with either of
 * them `null` — the current backend while the `ladder_v2` flag is off — falls
 * back to the legacy keys and today's rendering (1.99 / 9.90 → 49.90 × 2 →
 * 94.90). `money()` and `parsePromoMonths()` both read `null` as absent, so
 * null and missing take the identical path. On the legacy keys a middle step EQUAL to the first price is
 * folded into it (the current backend keeps those keys truthful as
 * `first_month_sar = then_sar = offer_sar`, `then_months = offer_months − 1`),
 * so first == then is accepted and reads as one price for N months.
 */
export function parseOfferTerms(
  raw: OfferWire | null | undefined,
): OfferTerms | null {
  if (!raw) return null;
  const list = money(raw.list_sar);
  if (!list) return null;

  const offer = money(raw.offer_sar);
  const offerMonths = parsePromoMonths(raw.offer_months);
  if (offer && offerMonths !== null) {
    const terms: OfferTerms = {
      offer_sar: offer,
      offer_months: offerMonths,
      then_sar: null,
      then_months: 0,
      list_sar: list,
    };
    return isSale(terms) ? terms : null;
  }

  // Legacy shape (backend from before batches).
  const first = money(raw.first_month_sar);
  const then = money(raw.then_sar);
  if (!first || !then) return null;
  const thenMonths = Number(raw.then_months);
  if (!Number.isInteger(thenMonths) || thenMonths < 0 || thenMonths > 11) {
    return null;
  }
  const folded = thenMonths === 0 || Number(then) === Number(first);
  const terms: OfferTerms = folded
    ? {
        offer_sar: first,
        offer_months: 1 + thenMonths,
        then_sar: null,
        then_months: 0,
        list_sar: list,
      }
    : {
        offer_sar: first,
        offer_months: 1,
        then_sar: then,
        then_months: thenMonths,
        list_sar: list,
      };
  return isSale(terms) ? terms : null;
}

/**
 * A checkout's plain promo quote (any plan, any price tier) → `OfferTerms`, so
 * /pay renders it through the same rows as the profession offer. `null` when
 * absent, malformed, or not actually below the list price.
 */
export function termsFromCheckoutPromo(
  raw: CheckoutPromo | null | undefined,
): OfferTerms | null {
  if (!raw) return null;
  const price = money(raw.price_sar);
  const list = money(raw.list_sar);
  const months = parsePromoMonths(raw.months);
  if (!price || !list || months === null) return null;
  const terms: OfferTerms = {
    offer_sar: price,
    offer_months: months,
    then_sar: null,
    then_months: 0,
    list_sar: list,
  };
  return isSale(terms) ? terms : null;
}

// -----------------------------------------------
// Copy
// -----------------------------------------------

/**
 * «للشهر الأول» · «لأول شهرين» · «لأول 3 أشهر». The offer words a single month
 * as «للشهر الأول» (what the owner's copy has always said); everything longer
 * shares `promoMonthsPhrase` with the pricing cards.
 */
export function offerSpan(months: number): string {
  return months === 1 ? "للشهر الأول" : promoMonthsPhrase(months);
}

export const INTRO_OFFER_COPY = {
  /** Only `legal` gets its own headline; specialist / entrepreneur share one. */
  headline: (group: string): string =>
    group === "legal" ? "عرض خاص للقانونيين" : "عرض خاص",
  /** «باقة «الاحترافية» بـ 19.90 ريال لأول شهرين». */
  lead: (terms: OfferTerms): string =>
    `باقة «${PLAN_NAME}» بـ ${formatSar(terms.offer_sar)} ريال ${offerSpan(terms.offer_months)}`,
  /** Shown above the lead when the Basic term's end is what opened the offer. */
  basicEnded: "انتهى اشتراكك في الباقة «الأساسية»، وصار هذا العرض متاحاً لك.",
  /** Cadence label beside the big price in `PlanPrice`. */
  period: (terms: OfferTerms): string => offerSpan(terms.offer_months),
  /**
   * Only the FIRST payment of the offer is non-refundable; its renewals keep
   * the 24h refund (pricing_campaigns_v2 §1.4-7) — hence «الدفعة الأولى», not
   * «أشهر العرض».
   */
  nonRefundable: "الدفعة الأولى غير قابلة للاسترداد",
  /** Owner's line, verbatim (2026-10-21). */
  cancelAnytime: "تقدر تلغي اشتراكك أي وقت من خلال إعدادات الحساب",
  cta: "اشترك الآن",
  later: "لاحقاً",
  /** /pay order-summary label — «سعر العرض لأول شهرين». */
  offerPriceLabel: (terms: OfferTerms): string =>
    `سعر العرض ${offerSpan(terms.offer_months)}`,
  // «الدفعات» not «الخصومات»: on a payment screen «خصم» reads as "discount".
  scheduleHeading: "الدفعات القادمة",
  /** /pay header note — «سعر العرض لأول شهرين · فترة الاشتراك 30 يوماً». */
  billingNote: (terms: OfferTerms): string =>
    `سعر العرض ${offerSpan(terms.offer_months)} · فترة الاشتراك 30 يوماً`,
} as const;

/** Legacy middle step only — the current backend never has one. */
function legacyThenSpan(offerMonths: number, thenMonths: number): string {
  if (offerMonths === 1 && thenMonths === 2) return "للشهرين الثاني والثالث";
  if (offerMonths === 1 && thenMonths === 1) return "للشهر الثاني";
  return `لـ ${thenMonths} أشهر بعدها`;
}

/**
 * The whole schedule as one sentence:
 *   «19.90 ريال لأول شهرين، ثم 94.90 ريال شهرياً»
 *   «19.90 ريال للشهر الأول، ثم 94.90 ريال شهرياً»
 * and, on a backend from before batches,
 *   «1.99 ريال للشهر الأول، ثم 49.90 ريال للشهرين الثاني والثالث، ثم 94.90 ريال شهرياً».
 */
export function introOfferSchedule(terms: OfferTerms): string {
  const parts = [
    `${formatSar(terms.offer_sar)} ريال ${offerSpan(terms.offer_months)}`,
  ];
  if (terms.then_sar !== null && terms.then_months > 0) {
    parts.push(
      `ثم ${formatSar(terms.then_sar)} ريال ${legacyThenSpan(terms.offer_months, terms.then_months)}`,
    );
  }
  parts.push(`ثم ${formatSar(terms.list_sar)} ريال شهرياً`);
  return parts.join("، ");
}

/**
 * The same schedule as discrete rows for /pay's order summary, where each
 * FUTURE charge sits under the amount due today (which is the first offer
 * term, so it is not repeated here). For a 2-month offer:
 *   «بعد 30 يوماً» → 19.90 · «بعد 60 يوماً، ثم كل 30 يوماً» → 94.90.
 */
export function introOfferScheduleRows(
  terms: OfferTerms,
): { label: string; amount: string }[] {
  const rows: { label: string; amount: string }[] = [];
  for (let i = 1; i < terms.offer_months; i += 1) {
    rows.push({ label: `بعد ${i * 30} يوماً`, amount: formatSar(terms.offer_sar) });
  }
  let middle = 0;
  if (terms.then_sar !== null) {
    const thenAmount = formatSar(terms.then_sar);
    middle = terms.then_months;
    for (let j = 0; j < middle; j += 1) {
      const day = (terms.offer_months + j) * 30;
      rows.push({ label: `بعد ${day} يوماً`, amount: thenAmount });
    }
  }
  const listFromDay = (terms.offer_months + middle) * 30;
  rows.push({
    label: `بعد ${listFromDay} يوماً، ثم كل 30 يوماً`,
    amount: formatSar(terms.list_sar),
  });
  return rows;
}
