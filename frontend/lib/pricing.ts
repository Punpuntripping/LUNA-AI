/**
 * Pricing catalog for the public /pricing page. This is marketing copy — the
 * single source of truth for what is *displayed*. The matching enforcement
 * limits live in the `plans` DB table (migration 076); keep the two in sync by
 * hand when limits change.
 *
 * ⚠ THE DB IS AUTHORITATIVE FOR THE AMOUNT. `plans.price_sar` is what
 * `/payments/checkout` charges; the strings below are display only, and the two
 * drift silently if edited apart. They were repriced together to 49.90 / 89.90 /
 * 189.90 (VAT-inclusive) in the Moyasar Wave 1 commit — migration 113 and this
 * file must always move as one.
 *
 * Billing model (Moyasar Wave 1, 2026-08-03):
 *   - ALL THREE plans are ONE-TIME purchases with a term. Moyasar has no
 *     subscription engine, so auto-renewal is our own scheduler and is Wave 2.
 *     Until that ships, no card here may promise «تجديد تلقائي» — a field that
 *     lies is the bug migration 091 was written to kill.
 *   - basic — 7-day term; pro / max — 30-day term. An early re-purchase of the
 *     same plan STACKS (extends the term) rather than resetting it.
 *
 * Usage points: the headline allowance shown is the WEEKLY points window, which
 * is anchored to the user's first message and runs 7 days (so for `basic` it
 * spans the whole 7-day subscription). The per-session (5h) cap is shown as a
 * secondary line. The monthly backstop is enforced but intentionally not shown
 * (and `basic` has no monthly cap at all). There is no internet-search feature.
 *
 * Numerals are written in Arabic-Indic to match the rest of the RTL UI, with
 * the Arabic decimal separator ٫ (U+066B) — the same shape `formatSar` produces
 * from the `ar-EG` locale, so a hard-coded card price and a server-computed
 * charge on /pay read identically.
 */
export interface PricingPlan {
  /** Matches plans.plan_id in the DB. */
  id: "basic" | "pro" | "max";
  nameAr: string;
  tagline: string;
  /** Price in SAR, Arabic-Indic numerals, with the ٫ decimal separator. */
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
    price: "٤٩٫٩٠",
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
    price: "٨٩٫٩٠",
    period: "شهرياً",
    billingNote: "بدون تجديد تلقائي · فترة الاشتراك ٣٠ يوماً",
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
    price: "١٨٩٫٩٠",
    period: "شهرياً",
    billingNote: "بدون تجديد تلقائي · فترة الاشتراك ٣٠ يوماً",
    features: [
      "٢٥٠ نقطة استخدام أسبوعياً",
      "٥٠ نقطة لكل جلسة (٥ ساعات)",
      "٢٠٠ صفحة استخراج نص شهرياً",
    ],
  },
];

/** Look up a plan by its `plans.plan_id`. `undefined` for an unknown slug. */
export function findPricingPlan(id: string): PricingPlan | undefined {
  return PRICING_PLANS.find((plan) => plan.id === id);
}

// -----------------------------------------------
// Phase E — the copy that must appear before purchase
// -----------------------------------------------

// «شامل الضريبة» REMOVED (owner, 2026-08-08): the business holds no VAT
// registration yet, and an unregistered business cannot claim to collect VAT.
// Prices display bare until registration; the server keeps stamping
// `vat_amount_sar` internally (never shown to the user). When a VAT number
// exists, reintroduce the note here AND revisit the receipt email, which
// deliberately carries no tax breakdown (backend/tests/test_receipts.py).

/**
 * Shown ONLY at the refund action (PaymentHistoryDialog — owner decision
 * 2026-08-04): out of context it reads as an anytime-refund promise.
 * «أول ٢٤ ساعة من الاشتراك» anchors the window to the purchase, matching the
 * server's paid_at arithmetic. /terms carries the long-form version.
 */
export const REFUND_POLICY_NOTE =
  "استرداد خلال أول ٢٤ ساعة من الاشتراك · تُخصم رسوم بوابة الدفع + ٠٫٥٠ ريال";

/**
 * The trust claim, chosen over «لا نحفظ بطاقتك» — see the Phase E table in the
 * plan for what each half of this sentence rests on. It stays true in Wave 2
 * when cards are tokenized at Moyasar, which a no-storage promise would not.
 *
 * ⚠ DO NOT EXTEND THIS with «لا يُحفظ أي شيء عن بطاقتك» or «ريحان لا يحفظ أي
 * بيانات دفع». Both are false: Moyasar retains a payment record as a legal
 * financial record, and `payment_transactions` holds amount, VAT split, status
 * and dates. The mechanics belong in /privacy, where PDPL requires them.
 */
export const PAYMENT_TRUST_NOTE =
  "جميع العمليات المالية تتم عبر مُيسّر؛ بيانات بطاقتك لا تمرّ عبر خوادم ريحان.";

/**
 * The disclosed processing fee, in SAR — DISPLAY ONLY.
 *
 * The server owns the real value (`REFUND_FEE_SAR`) and stamps what it actually
 * charged onto the row. This copy exists solely so the confirmation dialog can
 * show the arithmetic *before* the user commits: someone who expects 49.90 back
 * and receives 47.90 files a complaint; someone who agreed to 47.90 does not.
 */
export const REFUND_FEE_SAR = 3.4; // FALLBACK ONLY (matches the server's REFUND_FEE_FALLBACK_HALALAS) — the real deduction is quoted per payment via refund_quote_fee_sar: provider fee + Moyasar's 1.15 refund fee + a 0.50 margin

/** How long after `paid_at` a self-serve refund stays available. */
export const REFUND_WINDOW_HOURS = 24;

// -----------------------------------------------
// Formatting
// -----------------------------------------------

/**
 * A SAR amount in Arabic-Indic digits with the ٫ separator and exactly two
 * decimals — «٤٩٫٩٠». Matches the `ar-EG` locale the usage dialog already uses,
 * so a server-computed charge and a hard-coded card price render identically.
 *
 * Always two decimals, even for a whole number: a prorated upgrade charge of
 * «١١١٫٩٩» next to a credit of «٧٨» looks like a rounding bug, and on a payment
 * screen a rounding bug looks like a wrong charge.
 */
export function formatSar(value: number | string): string {
  // The API ships SAR as 2-dp strings ("89.90"). Coerce BEFORE formatting:
  // String.prototype.toLocaleString ignores locale arguments entirely, so a
  // string slipping through renders as Latin "89.90" on a payment screen.
  const n = typeof value === "string" ? Number(value) : value;
  return n.toLocaleString("ar-EG", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/** Halalas → the display string. The wire unit is halalas everywhere. */
export function formatHalalas(halalas: number): string {
  return formatSar(halalas / 100);
}

/**
 * Same digits, but without forced decimals — «٢», not «٢٫٠٠».
 *
 * Used ONLY for the processing fee, so that the refund confirmation dialog
 * reads the same fee wording, character-for-character identical to the
 * `REFUND_POLICY_NOTE` the user was shown before they bought. A fee that was
 * disclosed as «٢» and confirmed as «٢٫٠٠» is the same number and a worse
 * disclosure — the reader has to stop and check that it is the same number,
 * which is exactly the moment of doubt this clause exists to prevent.
 *
 * Never use this for an AMOUNT: «٤٩٫٩» beside «٤٧٫٩٠» reads as a rounding bug.
 */
export function formatFeeSar(value: number | string): string {
  const n = typeof value === "string" ? Number(value) : value;
  return n.toLocaleString("ar-EG", { maximumFractionDigits: 2 });
}

/**
 * Split a price string on the Arabic decimal separator so the fractional part
 * can be rendered smaller than the integer part.
 *
 * Purely a layout concern, and a real one: at `text-5xl`, «١٨٩٫٩٠» is materially
 * wider than «١٨٩» and reflows the three-card grid at the md breakpoint. Shared
 * by /pricing and the landing teaser so they can never diverge.
 */
export function splitPrice(price: string): {
  whole: string;
  fraction: string | null;
} {
  const at = price.indexOf("٫");
  if (at < 0) return { whole: price, fraction: null };
  return { whole: price.slice(0, at), fraction: price.slice(at) };
}
