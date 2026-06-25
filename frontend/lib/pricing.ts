/**
 * Pricing catalog for the public /pricing page. This is marketing copy — the
 * single source of truth for what is *displayed*. The matching enforcement
 * limits live in the `plans` DB table (migration 076); keep the two in sync by
 * hand when limits change.
 *
 * Billing model (confirmed 2026-06-25):
 *   - basic — a ONE-TIME payment, NO automatic renewal; the subscription lasts
 *     7 days only, then the user re-purchases to continue.
 *   - pro / max — billed monthly with AUTOMATIC renewal every month.
 *   (Auto-renewal is a future payment-layer behavior — payment is not wired yet;
 *   the DB only carries duration_days. This is display copy.)
 *
 * Usage points: the headline allowance shown is the WEEKLY points window, which
 * is anchored to the user's first message and runs 7 days (so for `basic` it
 * spans the whole 7-day subscription). The per-session (5h) cap is shown as a
 * secondary line. The monthly backstop is enforced but intentionally not shown
 * (and `basic` has no monthly cap at all). There is no internet-search feature.
 *
 * Numerals are written in Arabic-Indic to match the rest of the RTL UI (the
 * usage dialog formats with the `ar-EG` locale).
 */
export interface PricingPlan {
  /** Matches plans.plan_id in the DB. */
  id: "basic" | "pro" | "max";
  nameAr: string;
  tagline: string;
  /** Price in SAR, Arabic-Indic numerals. */
  price: string;
  /** Billing cadence label shown next to the price. */
  period: "أسبوعياً" | "شهرياً";
  /** Small muted line under the price: renewal model + term. */
  billingNote: string;
  features: string[];
  /** The visually emphasised "most popular" card. */
  highlighted?: boolean;
}

export const PRICING_PLANS: PricingPlan[] = [
  {
    id: "basic",
    nameAr: "الأساسية",
    tagline: "للبدء والاستخدام الخفيف",
    price: "٤٩",
    period: "أسبوعياً",
    billingNote: "بدون تجديد تلقائي · فترة الاشتراك ٧ أيام فقط",
    features: [
      "٥٠ نقطة استخدام طوال الاشتراك (٧ أيام)",
      "١٠ نقاط لكل جلسة (٥ ساعات)",
      "١٥ صفحة استخراج نص",
    ],
  },
  {
    id: "pro",
    nameAr: "الاحترافية",
    tagline: "الأنسب للممارسة اليومية",
    price: "٨٩",
    period: "شهرياً",
    billingNote: "تجديد تلقائي شهري",
    highlighted: true,
    features: [
      "٧٥ نقطة استخدام أسبوعياً",
      "١٥ نقطة لكل جلسة (٥ ساعات)",
      "٤٠ صفحة استخراج نص شهرياً",
    ],
  },
  {
    id: "max",
    nameAr: "القصوى",
    tagline: "أقصى سعة للقضايا المكثّفة",
    price: "١٨٩",
    period: "شهرياً",
    billingNote: "تجديد تلقائي شهري",
    features: [
      "٢٥٠ نقطة استخدام أسبوعياً",
      "٥٠ نقطة لكل جلسة (٥ ساعات)",
      "٢٠٠ صفحة استخراج نص شهرياً",
    ],
  },
];
