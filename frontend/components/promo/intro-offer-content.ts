import { findPricingPlan, formatSar } from "@/lib/pricing";

/**
 * Single source for «عرض الشهر الأول» copy
 * (`.claude/plans/profession_intro_offer.md` §4.2). The dialog, the quota-wall
 * block and /pay all render from here, so the three can never quote the offer
 * differently.
 *
 * ⚠ Every amount comes from the SERVER (`IntroOffer` / `CheckoutIntroOffer`);
 * nothing here hard-codes 1.99, 9.90, 49.90 or 94.90. ⚠ No end date and no seat
 * count — «المقاعد محدودة» (`SEATS_LIMITED_NOTE`) is the only scarcity signal.
 */

/** The plan the offer sells — its display name comes from the catalog. */
export const INTRO_OFFER_PLAN_ID = "pro" as const;

const PLAN_NAME = findPricingPlan(INTRO_OFFER_PLAN_ID)?.nameAr ?? "الاحترافية";

export const INTRO_OFFER_COPY = {
  /** Only `legal` gets its own headline; specialist / entrepreneur share one. */
  headline: (group: string): string =>
    group === "legal" ? "عرض خاص للقانونيين" : "عرض خاص",
  firstMonth: (first: string): string =>
    `باقة «${PLAN_NAME}» بـ ${formatSar(first)} ريال للشهر الأول`,
  /** Cadence label beside the big price in `PlanPrice`. */
  period: "للشهر الأول",
  nonRefundable: "الشهر الأول غير قابل للاسترداد",
  /** Owner's line, verbatim (2026-10-21). */
  cancelAnytime: "تقدر تلغي اشتراكك أي وقت من خلال إعدادات الحساب",
  cta: "اشترك الآن",
  later: "لاحقاً",
  /** /pay order-summary labels. */
  offerPriceLabel: "سعر العرض للشهر الأول",
  // «الدفعات» not «الخصومات»: on a payment screen «خصم» reads as "discount".
  scheduleHeading: "الدفعات القادمة",
  billingNote: "عرض الشهر الأول · فترة الاشتراك 30 يوماً",
} as const;

/**
 * «ثم 49.90 ريال للشهرين الثاني والثالث، ثم 94.90 ريال شهرياً». Worded for the
 * server's `then_months`, which is 2 today; any other count still reads true.
 */
export function introOfferSchedule(
  thenSar: string,
  thenMonths: number,
  listSar: string,
): string {
  const span =
    thenMonths === 2
      ? "للشهرين الثاني والثالث"
      : thenMonths === 1
        ? "للشهر الثاني"
        : `لـ ${thenMonths} أشهر بعده`;
  return `ثم ${formatSar(thenSar)} ريال ${span}، ثم ${formatSar(listSar)} ريال شهرياً`;
}

/**
 * The same schedule as discrete rows for /pay's order summary, where each
 * future charge sits under the amount due today.
 */
export function introOfferScheduleRows(
  thenSar: string,
  thenMonths: number,
  listSar: string,
): { label: string; amount: string }[] {
  const rows: { label: string; amount: string }[] = [];
  for (let i = 1; i <= thenMonths; i += 1) {
    rows.push({ label: `بعد ${i * 30} يوماً`, amount: formatSar(thenSar) });
  }
  rows.push({ label: "بعدها كل 30 يوماً", amount: formatSar(listSar) });
  return rows;
}
